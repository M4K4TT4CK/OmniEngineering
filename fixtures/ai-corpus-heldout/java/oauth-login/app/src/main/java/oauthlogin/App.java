package oauthlogin;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.javalin.Javalin;
import io.javalin.http.Context;
import java.io.IOException;
import java.net.URI;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.Base64;
import java.util.Optional;

import static java.nio.charset.StandardCharsets.UTF_8;

public class App {
    static final String SESSION_COOKIE = "sid";
    static final String STATE_COOKIE = "oauth_state";
    static final String NEXT_COOKIE = "oauth_next";
    static final String DEFAULT_NEXT = "/profile";
    static final int SESSION_DAYS = 30;

    private static final ObjectMapper JSON = new ObjectMapper();
    private static final SecureRandom RANDOM = new SecureRandom();

    public record Config(String clientId, String clientSecret, String redirectUri,
                         String authorizeUrl, String tokenUrl, String apiUrl,
                         String jdbcUrl, boolean secureCookies) {

        public static Config fromEnv() {
            String base = env("BASE_URL", "http://localhost:8080");
            return new Config(
                    required("GITHUB_CLIENT_ID"),
                    required("GITHUB_CLIENT_SECRET"),
                    base + "/callback",
                    env("GITHUB_AUTHORIZE_URL", "https://github.com/login/oauth/authorize"),
                    env("GITHUB_TOKEN_URL", "https://github.com/login/oauth/access_token"),
                    env("GITHUB_API_URL", "https://api.github.com"),
                    "jdbc:sqlite:" + env("DB_PATH", "oauth-login.db"),
                    base.startsWith("https://"));
        }

        private static String env(String key, String fallback) {
            String v = System.getenv(key);
            return v == null || v.isBlank() ? fallback : v;
        }

        private static String required(String key) {
            String v = System.getenv(key);
            if (v == null || v.isBlank()) {
                throw new IllegalStateException("missing env var " + key);
            }
            return v;
        }
    }

    record GitHubProfile(long id, String login, String name, String email, String avatarUrl) {}

    public record User(long id, long githubId, String login, String name, String email, String avatarUrl) {}

    private final Config config;
    private final Store store;
    private final HttpClient http = HttpClient.newHttpClient();

    public App(Config config) throws SQLException {
        this.config = config;
        this.store = new Store(config.jdbcUrl());
    }

    public static void main(String[] args) throws SQLException {
        App app = new App(Config.fromEnv());
        app.javalin().start(Integer.parseInt(System.getenv().getOrDefault("PORT", "8080")));
    }

    public Javalin javalin() {
        Javalin app = Javalin.create();
        app.get("/", ctx -> ctx.html(page("Home", "<p><a href=\"/login\">Log in with GitHub</a></p>")));
        app.get("/login", this::login);
        app.get("/callback", this::callback);
        app.get("/profile", this::profile);
        app.post("/logout", this::logout);
        return app;
    }

    private void login(Context ctx) {
        String state = randomToken();
        setCookie(ctx, STATE_COOKIE, state, 600, true);
        setCookie(ctx, NEXT_COOKIE, enc(safeNext(ctx.queryParam("next"))), 600, true);
        ctx.redirect(config.authorizeUrl()
                + "?client_id=" + enc(config.clientId())
                + "&redirect_uri=" + enc(config.redirectUri())
                + "&scope=" + enc("read:user")
                + "&state=" + enc(state));
    }

    private void callback(Context ctx) {
        String expectedState = ctx.cookie(STATE_COOKIE);
        String next = readNext(ctx);
        clearCookie(ctx, STATE_COOKIE);
        clearCookie(ctx, NEXT_COOKIE);

        String state = ctx.queryParam("state");
        String code = ctx.queryParam("code");
        if (expectedState == null || state == null || code == null
                || !MessageDigest.isEqual(expectedState.getBytes(UTF_8), state.getBytes(UTF_8))) {
            ctx.status(400).result("Login failed: invalid or missing state or code");
            return;
        }

        try {
            GitHubProfile profile = fetchProfile(exchangeCode(code));
            User user = store.upsertUser(profile);
            String sid = store.createSession(user.id());
            setCookie(ctx, SESSION_COOKIE, sid, SESSION_DAYS * 86400, true);
            ctx.redirect(safeNext(next));
        } catch (Exception e) {
            System.err.println("GitHub login failed: " + e);
            ctx.status(502).result("Login failed: could not complete GitHub sign-in");
        }
    }

    private void profile(Context ctx) throws SQLException {
        Optional<User> user = currentUser(ctx);
        if (user.isEmpty()) {
            ctx.redirect("/login?next=" + enc("/profile"));
            return;
        }
        User u = user.get();
        ctx.html(page("Profile", """
                <h1>Profile</h1>
                <img src="%s" alt="avatar" width="64">
                <ul>
                  <li>GitHub ID: %d</li>
                  <li>Login: %s</li>
                  <li>Name: %s</li>
                  <li>Email: %s</li>
                </ul>
                <form method="post" action="/logout"><button type="submit">Log out</button></form>
                """.formatted(esc(u.avatarUrl()), u.githubId(), esc(u.login()), esc(u.name()), esc(u.email()))));
    }

    private void logout(Context ctx) throws SQLException {
        String sid = ctx.cookie(SESSION_COOKIE);
        if (sid != null) {
            store.deleteSession(sid);
        }
        clearCookie(ctx, SESSION_COOKIE);
        ctx.redirect("/");
    }

    private Optional<User> currentUser(Context ctx) throws SQLException {
        String sid = ctx.cookie(SESSION_COOKIE);
        return sid == null ? Optional.empty() : store.findUserBySession(sid);
    }

    private String exchangeCode(String code) throws IOException, InterruptedException {
        String form = "client_id=" + enc(config.clientId())
                + "&client_secret=" + enc(config.clientSecret())
                + "&code=" + enc(code)
                + "&redirect_uri=" + enc(config.redirectUri());
        HttpRequest req = HttpRequest.newBuilder(URI.create(config.tokenUrl()))
                .header("Accept", "application/json")
                .header("Content-Type", "application/x-www-form-urlencoded")
                .POST(HttpRequest.BodyPublishers.ofString(form))
                .build();
        JsonNode body = JSON.readTree(http.send(req, HttpResponse.BodyHandlers.ofString()).body());
        JsonNode token = body.get("access_token");
        if (token == null || token.asText().isEmpty()) {
            throw new IllegalStateException("token exchange failed: " + body);
        }
        return token.asText();
    }

    private GitHubProfile fetchProfile(String accessToken) throws IOException, InterruptedException {
        HttpRequest req = HttpRequest.newBuilder(URI.create(config.apiUrl() + "/user"))
                .header("Accept", "application/vnd.github+json")
                .header("Authorization", "Bearer " + accessToken)
                .GET()
                .build();
        HttpResponse<String> res = http.send(req, HttpResponse.BodyHandlers.ofString());
        if (res.statusCode() != 200) {
            throw new IllegalStateException("profile fetch failed: HTTP " + res.statusCode());
        }
        JsonNode n = JSON.readTree(res.body());
        return new GitHubProfile(n.get("id").asLong(), n.get("login").asText(),
                text(n, "name"), text(n, "email"), text(n, "avatar_url"));
    }

    private static String text(JsonNode n, String field) {
        JsonNode v = n.get(field);
        return v == null || v.isNull() ? null : v.asText();
    }

    /** Only allows same-site relative paths, so the callback can't be used as an open redirect. */
    static String safeNext(String next) {
        if (next == null || !next.startsWith("/") || next.startsWith("//") || next.contains("\\")
                || next.contains("\r") || next.contains("\n")) {
            return DEFAULT_NEXT;
        }
        return next;
    }

    private static String readNext(Context ctx) {
        String raw = ctx.cookie(NEXT_COOKIE);
        if (raw == null) {
            return null;
        }
        try {
            return URLDecoder.decode(raw, UTF_8);
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    private void setCookie(Context ctx, String name, String value, int maxAge, boolean httpOnly) {
        StringBuilder sb = new StringBuilder(name).append('=').append(value)
                .append("; Path=/; SameSite=Lax; Max-Age=").append(maxAge);
        if (httpOnly) {
            sb.append("; HttpOnly");
        }
        if (config.secureCookies()) {
            sb.append("; Secure");
        }
        ctx.res().addHeader("Set-Cookie", sb.toString());
    }

    private void clearCookie(Context ctx, String name) {
        setCookie(ctx, name, "", 0, true);
    }

    private static String page(String title, String body) {
        return "<!doctype html><html><head><meta charset=\"utf-8\"><title>" + esc(title)
                + "</title></head><body>" + body + "</body></html>";
    }

    static String esc(String s) {
        if (s == null) {
            return "";
        }
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\"", "&quot;").replace("'", "&#39;");
    }

    private static String enc(String s) {
        return URLEncoder.encode(s, UTF_8);
    }

    static String randomToken() {
        byte[] bytes = new byte[32];
        RANDOM.nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    static final class Store {
        private final Connection conn;

        Store(String jdbcUrl) throws SQLException {
            conn = DriverManager.getConnection(jdbcUrl);
            try (Statement st = conn.createStatement()) {
                st.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                          id INTEGER PRIMARY KEY AUTOINCREMENT,
                          github_id INTEGER NOT NULL UNIQUE,
                          login TEXT NOT NULL,
                          name TEXT,
                          email TEXT,
                          avatar_url TEXT,
                          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""");
                st.execute("""
                        CREATE TABLE IF NOT EXISTS sessions (
                          token TEXT PRIMARY KEY,
                          user_id INTEGER NOT NULL REFERENCES users(id),
                          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""");
            }
        }

        synchronized User upsertUser(GitHubProfile p) throws SQLException {
            try (PreparedStatement ps = conn.prepareStatement("""
                    INSERT INTO users (github_id, login, name, email, avatar_url) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(github_id) DO UPDATE SET login = excluded.login, name = excluded.name,
                      email = excluded.email, avatar_url = excluded.avatar_url, updated_at = CURRENT_TIMESTAMP""")) {
                ps.setLong(1, p.id());
                ps.setString(2, p.login());
                ps.setString(3, p.name());
                ps.setString(4, p.email());
                ps.setString(5, p.avatarUrl());
                ps.executeUpdate();
            }
            try (PreparedStatement ps = conn.prepareStatement("SELECT * FROM users WHERE github_id = ?")) {
                ps.setLong(1, p.id());
                try (ResultSet rs = ps.executeQuery()) {
                    rs.next();
                    return toUser(rs);
                }
            }
        }

        synchronized String createSession(long userId) throws SQLException {
            String token = randomToken();
            try (PreparedStatement ps = conn.prepareStatement(
                    "INSERT INTO sessions (token, user_id) VALUES (?, ?)")) {
                ps.setString(1, token);
                ps.setLong(2, userId);
                ps.executeUpdate();
            }
            return token;
        }

        synchronized Optional<User> findUserBySession(String token) throws SQLException {
            try (PreparedStatement ps = conn.prepareStatement("""
                    SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
                    WHERE s.token = ? AND s.created_at > datetime('now', '-%d days')""".formatted(SESSION_DAYS))) {
                ps.setString(1, token);
                try (ResultSet rs = ps.executeQuery()) {
                    return rs.next() ? Optional.of(toUser(rs)) : Optional.empty();
                }
            }
        }

        synchronized void deleteSession(String token) throws SQLException {
            try (PreparedStatement ps = conn.prepareStatement("DELETE FROM sessions WHERE token = ?")) {
                ps.setString(1, token);
                ps.executeUpdate();
            }
        }

        private static User toUser(ResultSet rs) throws SQLException {
            return new User(rs.getLong("id"), rs.getLong("github_id"), rs.getString("login"),
                    rs.getString("name"), rs.getString("email"), rs.getString("avatar_url"));
        }
    }
}
