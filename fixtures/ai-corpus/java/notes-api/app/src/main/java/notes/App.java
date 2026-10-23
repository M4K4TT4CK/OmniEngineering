package notes;

import io.javalin.Javalin;
import io.javalin.http.Context;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.security.spec.KeySpec;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;

/** Personal notes REST API: Javalin + SQLite. */
public class App {

    static final int RATE_LIMIT_PER_MINUTE = 60;
    static final long TOKEN_TTL_SECONDS = 24 * 3600;

    record Credentials(String username, String password) {}
    record NoteInput(String title, String body) {}
    record Note(long id, String title, String body, String createdAt, String updatedAt) {}
    record User(long id, String passwordHash) {}

    static class ApiException extends RuntimeException {
        final int status;
        ApiException(int status, String message) {
            super(message);
            this.status = status;
        }
    }

    public static void main(String[] args) throws Exception {
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "7070"));
        String db = System.getenv().getOrDefault("NOTES_DB", "notes.db");
        create("jdbc:sqlite:" + db, RATE_LIMIT_PER_MINUTE).start(port);
    }

    /** Builds (but does not start) the application. */
    public static Javalin create(String jdbcUrl, int rateLimit) throws SQLException {
        Db db = new Db(jdbcUrl);
        RateLimiter limiter = new RateLimiter(rateLimit, 60_000);
        Javalin app = Javalin.create();

        app.exception(ApiException.class, (e, ctx) ->
                ctx.status(e.status).json(Map.of("error", e.getMessage())));
        app.exception(Exception.class, (e, ctx) -> {
            // Javalin's own HTTP errors (e.g. malformed JSON) keep their status; anything else is a 500.
            if (e instanceof io.javalin.http.HttpResponseException h) {
                ctx.status(h.getStatus()).json(Map.of("error", String.valueOf(h.getMessage())));
            } else {
                e.printStackTrace();
                ctx.status(500).json(Map.of("error", "internal error"));
            }
        });

        // Authentication + rate limiting. Signup/login are limited per client IP, everything else per user.
        app.before(ctx -> {
            String path = ctx.path();
            boolean open = path.equals("/signup") || path.equals("/login");
            String key;
            if (open) {
                key = "ip:" + ctx.ip();
            } else {
                String header = ctx.header("Authorization");
                if (header == null || !header.startsWith("Bearer ")) {
                    throw new ApiException(401, "missing bearer token");
                }
                Long userId = db.userIdForToken(hash(header.substring(7).trim()));
                if (userId == null) throw new ApiException(401, "invalid or expired token");
                ctx.attribute("userId", userId);
                key = "user:" + userId;
            }
            long retryAfter = limiter.tryAcquire(key);
            if (retryAfter > 0) {
                ctx.header("Retry-After", String.valueOf(retryAfter));
                throw new ApiException(429, "rate limit exceeded");
            }
        });

        app.post("/signup", ctx -> {
            Credentials c = credentials(ctx);
            if (!USERNAME.matcher(c.username()).matches()) {
                throw new ApiException(400, "username must be 3-32 characters: letters, digits, underscore");
            }
            if (c.password().length() < 8 || c.password().length() > 128) {
                throw new ApiException(400, "password must be 8-128 characters");
            }
            long id = db.createUser(c.username(), hashPassword(c.password()));
            if (id < 0) throw new ApiException(409, "username already taken");
            ctx.status(201).json(Map.of("id", id, "username", c.username()));
        });

        app.post("/login", ctx -> {
            Credentials c = credentials(ctx);
            User u = db.findUser(c.username());
            // Verify against a dummy hash when the user is unknown so timing doesn't reveal which usernames exist.
            boolean ok = verifyPassword(c.password(), u != null ? u.passwordHash() : DUMMY_HASH) && u != null;
            if (!ok) throw new ApiException(401, "invalid username or password");
            byte[] raw = new byte[32];
            RANDOM.nextBytes(raw);
            String token = Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
            long expires = Instant.now().getEpochSecond() + TOKEN_TTL_SECONDS;
            db.saveToken(hash(token), u.id(), expires);
            ctx.json(Map.of("token", token, "expiresAt", Instant.ofEpochSecond(expires).toString()));
        });

        app.post("/notes", ctx -> {
            NoteInput in = noteInput(ctx);
            ctx.status(201).json(db.createNote(uid(ctx), in.title(), in.body()));
        });

        app.get("/notes", ctx -> {
            int limit = intParam(ctx, "limit", 50, 1, 100);
            int offset = intParam(ctx, "offset", 0, 0, Integer.MAX_VALUE);
            ctx.json(db.listNotes(uid(ctx), ctx.queryParam("q"), limit, offset));
        });

        app.get("/notes/{id}", ctx -> {
            Note n = db.getNote(uid(ctx), noteId(ctx));
            if (n == null) throw new ApiException(404, "note not found");
            ctx.json(n);
        });

        app.put("/notes/{id}", ctx -> {
            NoteInput in = noteInput(ctx);
            Note n = db.updateNote(uid(ctx), noteId(ctx), in.title(), in.body());
            if (n == null) throw new ApiException(404, "note not found");
            ctx.json(n);
        });

        app.delete("/notes/{id}", ctx -> {
            if (!db.deleteNote(uid(ctx), noteId(ctx))) throw new ApiException(404, "note not found");
            ctx.status(204);
        });

        return app;
    }

    // ---------- request helpers ----------

    static final Pattern USERNAME = Pattern.compile("[A-Za-z0-9_]{3,32}");

    static long uid(Context ctx) {
        return ctx.<Long>attribute("userId");
    }

    static long noteId(Context ctx) {
        try {
            return Long.parseLong(ctx.pathParam("id"));
        } catch (NumberFormatException e) {
            throw new ApiException(404, "note not found");
        }
    }

    static int intParam(Context ctx, String name, int def, int min, int max) {
        String v = ctx.queryParam(name);
        if (v == null) return def;
        try {
            int n = Integer.parseInt(v);
            if (n < min || n > max) throw new NumberFormatException();
            return n;
        } catch (NumberFormatException e) {
            throw new ApiException(400, name + " must be an integer between " + min + " and " + max);
        }
    }

    static Credentials credentials(Context ctx) {
        Credentials c = ctx.bodyAsClass(Credentials.class);
        if (c.username() == null || c.password() == null) {
            throw new ApiException(400, "username and password are required");
        }
        return c;
    }

    static NoteInput noteInput(Context ctx) {
        NoteInput in = ctx.bodyAsClass(NoteInput.class);
        if (in.title() == null || in.title().isBlank() || in.title().length() > 200) {
            throw new ApiException(400, "title is required (max 200 characters)");
        }
        String body = in.body() == null ? "" : in.body();
        if (body.length() > 10_000) throw new ApiException(400, "body too long (max 10000 characters)");
        return new NoteInput(in.title(), body);
    }

    // ---------- crypto ----------

    static final SecureRandom RANDOM = new SecureRandom();
    static final int PBKDF2_ITERATIONS = 210_000;
    static final String DUMMY_HASH = hashPassword("dummy-password-for-timing");

    static String hashPassword(String password) {
        byte[] salt = new byte[16];
        RANDOM.nextBytes(salt);
        byte[] dk = pbkdf2(password, salt);
        return Base64.getEncoder().encodeToString(salt) + ":" + Base64.getEncoder().encodeToString(dk);
    }

    static boolean verifyPassword(String password, String stored) {
        String[] parts = stored.split(":");
        byte[] salt = Base64.getDecoder().decode(parts[0]);
        byte[] expected = Base64.getDecoder().decode(parts[1]);
        return MessageDigest.isEqual(expected, pbkdf2(password, salt));
    }

    private static byte[] pbkdf2(String password, byte[] salt) {
        try {
            KeySpec spec = new PBEKeySpec(password.toCharArray(), salt, PBKDF2_ITERATIONS, 256);
            return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).getEncoded();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    /** Tokens are stored as SHA-256 hashes so a leaked database doesn't expose usable tokens. */
    static String hash(String token) {
        try {
            byte[] d = MessageDigest.getInstance("SHA-256").digest(token.getBytes(StandardCharsets.UTF_8));
            return Base64.getEncoder().encodeToString(d);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    // ---------- rate limiting ----------

    /** Fixed-window counter per key. */
    static class RateLimiter {
        private final int limit;
        private final long windowMs;
        private final Map<String, long[]> windows = new HashMap<>(); // key -> {windowStart, count}

        RateLimiter(int limit, long windowMs) {
            this.limit = limit;
            this.windowMs = windowMs;
        }

        /** Returns 0 if the request is allowed, otherwise the seconds until the window resets. */
        synchronized long tryAcquire(String key) {
            long now = System.currentTimeMillis();
            if (windows.size() > 10_000) windows.values().removeIf(w -> now - w[0] >= windowMs);
            long[] w = windows.get(key);
            if (w == null || now - w[0] >= windowMs) {
                windows.put(key, new long[] {now, 1});
                return 0;
            }
            if (w[1] < limit) {
                w[1]++;
                return 0;
            }
            return Math.max(1, (w[0] + windowMs - now + 999) / 1000);
        }
    }

    // ---------- persistence ----------

    /** SQLite access over a single shared connection; methods are synchronized. */
    static class Db {
        private final Connection conn;

        Db(String url) throws SQLException {
            conn = DriverManager.getConnection(url);
            try (Statement s = conn.createStatement()) {
                s.execute("PRAGMA foreign_keys = ON");
                s.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                          id INTEGER PRIMARY KEY AUTOINCREMENT,
                          username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                          password_hash TEXT NOT NULL)""");
                s.execute("""
                        CREATE TABLE IF NOT EXISTS tokens (
                          token_hash TEXT PRIMARY KEY,
                          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                          expires_at INTEGER NOT NULL)""");
                s.execute("""
                        CREATE TABLE IF NOT EXISTS notes (
                          id INTEGER PRIMARY KEY AUTOINCREMENT,
                          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                          title TEXT NOT NULL,
                          body TEXT NOT NULL,
                          created_at TEXT NOT NULL,
                          updated_at TEXT NOT NULL)""");
                s.execute("CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id)");
            }
        }

        private static RuntimeException wrap(SQLException e) {
            return new IllegalStateException(e);
        }

        synchronized long createUser(String username, String passwordHash) {
            try {
                try (PreparedStatement q = conn.prepareStatement("SELECT 1 FROM users WHERE username = ?")) {
                    q.setString(1, username);
                    if (q.executeQuery().next()) return -1;
                }
                try (PreparedStatement p = conn.prepareStatement(
                        "INSERT INTO users(username, password_hash) VALUES (?, ?)", Statement.RETURN_GENERATED_KEYS)) {
                    p.setString(1, username);
                    p.setString(2, passwordHash);
                    p.executeUpdate();
                    ResultSet k = p.getGeneratedKeys();
                    k.next();
                    return k.getLong(1);
                }
            } catch (SQLException e) {
                throw wrap(e);
            }
        }

        synchronized User findUser(String username) {
            try (PreparedStatement p = conn.prepareStatement("SELECT id, password_hash FROM users WHERE username = ?")) {
                p.setString(1, username);
                ResultSet r = p.executeQuery();
                return r.next() ? new User(r.getLong(1), r.getString(2)) : null;
            } catch (SQLException e) {
                throw wrap(e);
            }
        }

        synchronized void saveToken(String tokenHash, long userId, long expiresAt) {
            try {
                try (PreparedStatement d = conn.prepareStatement("DELETE FROM tokens WHERE expires_at < ?")) {
                    d.setLong(1, Instant.now().getEpochSecond());
                    d.executeUpdate();
                }
                try (PreparedStatement p = conn.prepareStatement(
                        "INSERT INTO tokens(token_hash, user_id, expires_at) VALUES (?, ?, ?)")) {
                    p.setString(1, tokenHash);
                    p.setLong(2, userId);
                    p.setLong(3, expiresAt);
                    p.executeUpdate();
                }
            } catch (SQLException e) {
                throw wrap(e);
            }
        }

        synchronized Long userIdForToken(String tokenHash) {
            try (PreparedStatement p = conn.prepareStatement(
                    "SELECT user_id FROM tokens WHERE token_hash = ? AND expires_at > ?")) {
                p.setString(1, tokenHash);
                p.setLong(2, Instant.now().getEpochSecond());
                ResultSet r = p.executeQuery();
                return r.next() ? r.getLong(1) : null;
            } catch (SQLException e) {
                throw wrap(e);
            }
        }

        synchronized Note createNote(long userId, String title, String body) {
            String now = Instant.now().toString();
            try (PreparedStatement p = conn.prepareStatement(
                    "INSERT INTO notes(user_id, title, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    Statement.RETURN_GENERATED_KEYS)) {
                p.setLong(1, userId);
                p.setString(2, title);
                p.setString(3, body);
                p.setString(4, now);
                p.setString(5, now);
                p.executeUpdate();
                ResultSet k = p.getGeneratedKeys();
                k.next();
                return new Note(k.getLong(1), title, body, now, now);
            } catch (SQLException e) {
                throw wrap(e);
            }
        }

        synchronized List<Note> listNotes(long userId, String q, int limit, int offset) {
            boolean search = q != null && !q.isBlank();
            String sql = "SELECT id, title, body, created_at, updated_at FROM notes WHERE user_id = ?"
                    + (search ? " AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')" : "")
                    + " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?";
            try (PreparedStatement p = conn.prepareStatement(sql)) {
                int i = 1;
                p.setLong(i++, userId);
                if (search) {
                    String pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%";
                    p.setString(i++, pattern);
                    p.setString(i++, pattern);
                }
                p.setInt(i++, limit);
                p.setInt(i, offset);
                ResultSet r = p.executeQuery();
                List<Note> out = new ArrayList<>();
                while (r.next()) out.add(note(r));
                return out;
            } catch (SQLException e) {
                throw wrap(e);
            }
        }

        synchronized Note getNote(long userId, long id) {
            try (PreparedStatement p = conn.prepareStatement(
                    "SELECT id, title, body, created_at, updated_at FROM notes WHERE id = ? AND user_id = ?")) {
                p.setLong(1, id);
                p.setLong(2, userId);
                ResultSet r = p.executeQuery();
                return r.next() ? note(r) : null;
            } catch (SQLException e) {
                throw wrap(e);
            }
        }

        synchronized Note updateNote(long userId, long id, String title, String body) {
            try (PreparedStatement p = conn.prepareStatement(
                    "UPDATE notes SET title = ?, body = ?, updated_at = ? WHERE id = ? AND user_id = ?")) {
                p.setString(1, title);
                p.setString(2, body);
                p.setString(3, Instant.now().toString());
                p.setLong(4, id);
                p.setLong(5, userId);
                if (p.executeUpdate() == 0) return null;
            } catch (SQLException e) {
                throw wrap(e);
            }
            return getNote(userId, id);
        }

        synchronized boolean deleteNote(long userId, long id) {
            try (PreparedStatement p = conn.prepareStatement("DELETE FROM notes WHERE id = ? AND user_id = ?")) {
                p.setLong(1, id);
                p.setLong(2, userId);
                return p.executeUpdate() > 0;
            } catch (SQLException e) {
                throw wrap(e);
            }
        }

        private static Note note(ResultSet r) throws SQLException {
            return new Note(r.getLong(1), r.getString(2), r.getString(3), r.getString(4), r.getString(5));
        }
    }
}
