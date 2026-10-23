package tenantnotes;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;

/**
 * Multi-tenant notes store backed by SQLite. Every public method takes the acting user and
 * checks their role and organisation before touching data.
 */
public class Service implements AutoCloseable {

    public enum Role {
        ADMIN, EDITOR, VIEWER;

        boolean canWrite() {
            return this != VIEWER;
        }
    }

    public record User(long id, long orgId, String email, Role role) {}

    public record Note(long id, String title, String body, long createdBy, String updatedAt) {}

    /** Failure carrying the HTTP status the caller should report. */
    public static class ServiceError extends RuntimeException {
        private final int status;

        public ServiceError(int status, String message) {
            super(message);
            this.status = status;
        }

        public int status() {
            return status;
        }
    }

    private record Account(User user, String passwordHash) {}

    private record Invitation(long orgId, String email, Role role) {}

    private interface Mapper<T> {
        T map(ResultSet rs) throws SQLException;
    }

    private static final Mapper<User> USER = rs -> new User(
            rs.getLong("id"), rs.getLong("org_id"), rs.getString("email"), Role.valueOf(rs.getString("role")));

    private static final Mapper<Account> ACCOUNT = rs -> new Account(USER.map(rs), rs.getString("password_hash"));

    private static final Mapper<Invitation> INVITATION = rs -> new Invitation(
            rs.getLong("org_id"), rs.getString("email"), Role.valueOf(rs.getString("role")));

    private static final Mapper<Note> NOTE = rs -> new Note(
            rs.getLong("id"), rs.getString("title"), rs.getString("body"),
            rs.getLong("created_by"), rs.getString("updated_at"));

    private static final String[] SCHEMA = {
        "CREATE TABLE IF NOT EXISTS organisations (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, org_id INTEGER NOT NULL REFERENCES organisations(id), email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, role TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id))",
        "CREATE TABLE IF NOT EXISTS invitations (token_hash TEXT PRIMARY KEY, org_id INTEGER NOT NULL REFERENCES organisations(id), email TEXT NOT NULL, role TEXT NOT NULL, accepted INTEGER NOT NULL DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, org_id INTEGER NOT NULL REFERENCES organisations(id), title TEXT NOT NULL, body TEXT NOT NULL, created_by INTEGER NOT NULL REFERENCES users(id), updated_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS notes_org ON notes(org_id)",
    };

    private static final int PBKDF2_ITERATIONS = 120_000;
    private static final int MIN_PASSWORD_LENGTH = 8;
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final Base64.Encoder B64 = Base64.getEncoder();
    private static final Base64.Encoder B64_URL = Base64.getUrlEncoder().withoutPadding();
    private static final Base64.Decoder B64_DECODER = Base64.getDecoder();

    private final Connection db;

    public Service(String jdbcUrl) throws SQLException {
        this.db = DriverManager.getConnection(jdbcUrl);
        try (Statement st = db.createStatement()) {
            for (String sql : SCHEMA) {
                st.execute(sql);
            }
        }
    }

    // ---- Accounts and sessions ----

    /** Creates an organisation together with its first admin. */
    public synchronized User createOrganisation(String name, String adminEmail, String password) {
        requireText(name, "organisation");
        String email = normaliseEmail(adminEmail);
        requireEmail(email);
        requirePassword(password);
        requireNewEmail(email);
        long orgId = execute("INSERT INTO organisations (name) VALUES (?)", name);
        return insertUser(orgId, email, password, Role.ADMIN);
    }

    /** Returns a new session token for valid credentials. */
    public synchronized String login(String email, String password) {
        Optional<Account> account = query(
                "SELECT id, org_id, email, role, password_hash FROM users WHERE email = ?",
                ACCOUNT, normaliseEmail(email)).stream().findFirst();
        if (account.isEmpty() || password == null || !verifyPassword(password, account.get().passwordHash())) {
            throw new ServiceError(401, "invalid email or password");
        }
        return issueToken(account.get().user().id());
    }

    /** Resolves a session token to the user it belongs to. */
    public synchronized User authenticate(String token) {
        if (token == null || token.isBlank()) {
            throw new ServiceError(401, "missing token");
        }
        return query(
                "SELECT u.id, u.org_id, u.email, u.role FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
                USER, sha256(token)).stream().findFirst()
                .orElseThrow(() -> new ServiceError(401, "invalid token"));
    }

    // ---- Invitations and roles ----

    /** Admin invites an email address to their organisation; returns the invitation token. */
    public synchronized String invite(User actor, String email, Role role) {
        requireAdmin(actor);
        String normalised = normaliseEmail(email);
        requireEmail(normalised);
        requireNewEmail(normalised);
        String token = randomToken();
        execute("INSERT INTO invitations (token_hash, org_id, email, role) VALUES (?, ?, ?, ?)",
                sha256(token), actor.orgId(), normalised, role.name());
        return token;
    }

    /** Sets the password for an invited address and returns a session token. */
    public synchronized String acceptInvitation(String token, String password) {
        requireText(token, "invitation token");
        requirePassword(password);
        Invitation invitation = query(
                "SELECT org_id, email, role FROM invitations WHERE token_hash = ? AND accepted = 0",
                INVITATION, sha256(token)).stream().findFirst()
                .orElseThrow(() -> new ServiceError(404, "invitation not found"));
        requireNewEmail(invitation.email());
        execute("UPDATE invitations SET accepted = 1 WHERE token_hash = ?", sha256(token));
        User user = insertUser(invitation.orgId(), invitation.email(), password, invitation.role());
        return issueToken(user.id());
    }

    /** Admin changes the role of a user in their own organisation. */
    public synchronized void changeRole(User actor, long userId, Role role) {
        requireAdmin(actor);
        User target = query(
                "SELECT id, org_id, email, role FROM users WHERE id = ? AND org_id = ?",
                USER, userId, actor.orgId()).stream().findFirst()
                .orElseThrow(() -> new ServiceError(404, "user not found"));
        if (target.role() == Role.ADMIN && role != Role.ADMIN && countAdmins(actor.orgId()) <= 1) {
            throw new ServiceError(409, "organisation must keep at least one admin");
        }
        execute("UPDATE users SET role = ? WHERE id = ? AND org_id = ?", role.name(), userId, actor.orgId());
    }

    // ---- Notes ----

    public synchronized Note createNote(User actor, String title, String body) {
        requireWriter(actor);
        requireText(title, "title");
        long id = execute(
                "INSERT INTO notes (org_id, title, body, created_by, updated_at) VALUES (?, ?, ?, ?, ?)",
                actor.orgId(), title, body == null ? "" : body, actor.id(), now());
        return getNote(actor, id);
    }

    /** Fetches a note; notes from other organisations are reported as not found. */
    public synchronized Note getNote(User actor, long id) {
        return query(
                "SELECT id, title, body, created_by, updated_at FROM notes WHERE id = ? AND org_id = ?",
                NOTE, id, actor.orgId()).stream().findFirst()
                .orElseThrow(() -> new ServiceError(404, "note not found"));
    }

    /** Updates the given fields; a null title or body leaves that field unchanged. */
    public synchronized Note updateNote(User actor, long id, String title, String body) {
        requireWriter(actor);
        Note current = getNote(actor, id);
        String newTitle = title == null ? current.title() : requireText(title, "title");
        String newBody = body == null ? current.body() : body;
        execute("UPDATE notes SET title = ?, body = ?, updated_at = ? WHERE id = ? AND org_id = ?",
                newTitle, newBody, now(), id, actor.orgId());
        return getNote(actor, id);
    }

    public synchronized void deleteNote(User actor, long id) {
        requireWriter(actor);
        getNote(actor, id); // throws 404 unless the note belongs to the actor's organisation
        execute("DELETE FROM notes WHERE id = ? AND org_id = ?", id, actor.orgId());
    }

    public synchronized List<Note> listNotes(User actor) {
        return query(
                "SELECT id, title, body, created_by, updated_at FROM notes WHERE org_id = ? ORDER BY id",
                NOTE, actor.orgId());
    }

    /** Case-insensitive substring match on title or body. */
    public synchronized List<Note> searchNotes(User actor, String text) {
        String pattern = "%" + escapeLike(requireText(text, "q").toLowerCase(Locale.ROOT)) + "%";
        return query(
                "SELECT id, title, body, created_by, updated_at FROM notes WHERE org_id = ?"
                        + " AND (lower(title) LIKE ? ESCAPE '\\' OR lower(body) LIKE ? ESCAPE '\\') ORDER BY id",
                NOTE, actor.orgId(), pattern, pattern);
    }

    @Override
    public synchronized void close() throws SQLException {
        db.close();
    }

    // ---- Authorisation and validation ----

    private static void requireAdmin(User actor) {
        if (actor.role() != Role.ADMIN) {
            throw new ServiceError(403, "admin role required");
        }
    }

    private static void requireWriter(User actor) {
        if (!actor.role().canWrite()) {
            throw new ServiceError(403, "viewers cannot change notes");
        }
    }

    private static String requireText(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new ServiceError(400, field + " is required");
        }
        return value;
    }

    private static void requireEmail(String email) {
        if (!email.contains("@")) {
            throw new ServiceError(400, "a valid email is required");
        }
    }

    private static void requirePassword(String password) {
        if (password == null || password.length() < MIN_PASSWORD_LENGTH) {
            throw new ServiceError(400, "password must be at least " + MIN_PASSWORD_LENGTH + " characters");
        }
    }

    private void requireNewEmail(String email) {
        if (!query("SELECT id FROM users WHERE email = ?", rs -> rs.getLong("id"), email).isEmpty()) {
            throw new ServiceError(409, "email already registered");
        }
    }

    // ---- Persistence helpers ----

    private User insertUser(long orgId, String email, String password, Role role) {
        long id = execute(
                "INSERT INTO users (org_id, email, password_hash, role) VALUES (?, ?, ?, ?)",
                orgId, email, hashPassword(password), role.name());
        return new User(id, orgId, email, role);
    }

    private String issueToken(long userId) {
        String token = randomToken();
        execute("INSERT INTO sessions (token_hash, user_id) VALUES (?, ?)", sha256(token), userId);
        return token;
    }

    private long countAdmins(long orgId) {
        return query("SELECT COUNT(*) AS n FROM users WHERE org_id = ? AND role = ?",
                rs -> rs.getLong("n"), orgId, Role.ADMIN.name()).get(0);
    }

    private <T> List<T> query(String sql, Mapper<T> mapper, Object... params) {
        try (PreparedStatement ps = db.prepareStatement(sql)) {
            bind(ps, params);
            try (ResultSet rs = ps.executeQuery()) {
                List<T> rows = new ArrayList<>();
                while (rs.next()) {
                    rows.add(mapper.map(rs));
                }
                return rows;
            }
        } catch (SQLException e) {
            throw new IllegalStateException("database error", e);
        }
    }

    /** Runs an insert or update and returns the generated key (0 if none). */
    private long execute(String sql, Object... params) {
        try (PreparedStatement ps = db.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS)) {
            bind(ps, params);
            ps.executeUpdate();
            try (ResultSet keys = ps.getGeneratedKeys()) {
                return keys.next() ? keys.getLong(1) : 0;
            }
        } catch (SQLException e) {
            throw new IllegalStateException("database error", e);
        }
    }

    private static void bind(PreparedStatement ps, Object[] params) throws SQLException {
        for (int i = 0; i < params.length; i++) {
            ps.setObject(i + 1, params[i]);
        }
    }

    // ---- Crypto and small utilities ----

    private static String hashPassword(String password) {
        byte[] salt = new byte[16];
        RANDOM.nextBytes(salt);
        byte[] hash = pbkdf2(password, salt, PBKDF2_ITERATIONS);
        return PBKDF2_ITERATIONS + "$" + B64.encodeToString(salt) + "$" + B64.encodeToString(hash);
    }

    private static boolean verifyPassword(String password, String stored) {
        String[] parts = stored.split("\\$");
        int iterations = Integer.parseInt(parts[0]);
        byte[] salt = B64_DECODER.decode(parts[1]);
        byte[] expected = B64_DECODER.decode(parts[2]);
        return MessageDigest.isEqual(expected, pbkdf2(password, salt, iterations));
    }

    private static byte[] pbkdf2(String password, byte[] salt, int iterations) {
        try {
            PBEKeySpec spec = new PBEKeySpec(password.toCharArray(), salt, iterations, 256);
            return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).getEncoded();
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }

    private static String randomToken() {
        byte[] bytes = new byte[32];
        RANDOM.nextBytes(bytes);
        return B64_URL.encodeToString(bytes);
    }

    /** Tokens are stored only as SHA-256 digests, so a leaked database cannot be replayed. */
    private static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }

    private static String normaliseEmail(String email) {
        return email == null ? "" : email.trim().toLowerCase(Locale.ROOT);
    }

    private static String escapeLike(String value) {
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_");
    }

    private static String now() {
        return Instant.now().toString();
    }
}
