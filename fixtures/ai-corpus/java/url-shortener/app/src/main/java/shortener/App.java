package shortener;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.Executors;
import java.util.function.Function;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** URL shortener: HTTP API on the JDK's built-in server, links persisted in SQLite. */
public class App {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final Pattern ALIAS = Pattern.compile("[A-Za-z0-9_-]{3,32}");
    private static final Pattern CODE_PATH = Pattern.compile("/([A-Za-z0-9_-]+)");
    private static final Pattern STATS_PATH = Pattern.compile("/api/links/([A-Za-z0-9_-]+)/stats");
    private static final String ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    private static final int MAX_BODY = 8 * 1024;
    private static final int MAX_URL = 2048;

    private final String jdbcUrl;
    private final Function<String, String> titleFetcher;
    private final HttpServer server;
    private final SecureRandom random = new SecureRandom();

    public App(Path dbFile, int port, Function<String, String> titleFetcher) throws IOException, SQLException {
        this.jdbcUrl = "jdbc:sqlite:" + dbFile;
        this.titleFetcher = titleFetcher;
        try (Connection c = connect(); Statement s = c.createStatement()) {
            s.execute("CREATE TABLE IF NOT EXISTS links ("
                    + "code TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT, "
                    + "created_at INTEGER NOT NULL, clicks INTEGER NOT NULL DEFAULT 0)");
        }
        this.server = HttpServer.create(new InetSocketAddress(port), 0);
        this.server.createContext("/", this::handle);
        this.server.setExecutor(Executors.newFixedThreadPool(8));
    }

    public void start() { server.start(); }

    public void stop() { server.stop(0); }

    public int port() { return server.getAddress().getPort(); }

    public static void main(String[] args) throws Exception {
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8080"));
        Path db = Path.of(System.getenv().getOrDefault("DB_PATH", "links.db"));
        App app = new App(db, port, new TitleFetcher());
        app.start();
        System.out.println("Listening on port " + app.port());
    }

    // ---- HTTP handling ----

    private void handle(HttpExchange ex) throws IOException {
        try {
            String path = ex.getRequestURI().getPath();
            String method = ex.getRequestMethod();
            Matcher stats = STATS_PATH.matcher(path);
            Matcher code = CODE_PATH.matcher(path);

            if (path.equals("/api/links")) {
                if (method.equals("POST")) create(ex); else methodNotAllowed(ex, "POST");
            } else if (stats.matches()) {
                if (method.equals("GET")) stats(ex, stats.group(1)); else methodNotAllowed(ex, "GET");
            } else if (code.matches() && !path.equals("/api")) {
                if (method.equals("GET")) redirect(ex, code.group(1)); else methodNotAllowed(ex, "GET");
            } else {
                error(ex, 404, "Not found");
            }
        } catch (SQLException e) {
            e.printStackTrace();
            error(ex, 500, "Internal error");
        } finally {
            ex.close();
        }
    }

    private void create(HttpExchange ex) throws IOException, SQLException {
        byte[] body = ex.getRequestBody().readNBytes(MAX_BODY + 1);
        if (body.length > MAX_BODY) { error(ex, 413, "Request body too large"); return; }

        JsonNode req;
        try {
            req = JSON.readTree(body);
        } catch (IOException e) {
            error(ex, 400, "Body must be valid JSON");
            return;
        }
        if (req == null || !req.isObject() || !req.path("url").isTextual()) {
            error(ex, 400, "Field 'url' is required");
            return;
        }
        String url = req.get("url").asText().trim();
        if (!validUrl(url)) { error(ex, 400, "'url' must be an absolute http(s) URL"); return; }

        String alias = null;
        if (req.hasNonNull("alias")) {
            if (!req.get("alias").isTextual() || !ALIAS.matcher(req.get("alias").asText()).matches()) {
                error(ex, 400, "'alias' must be 3-32 characters of A-Z, a-z, 0-9, '_' or '-'");
                return;
            }
            alias = req.get("alias").asText();
            if (alias.equals("api")) { error(ex, 400, "'alias' is reserved"); return; }
        }

        // Fetched before insert so the title is stored with the link; fetcher must never throw.
        String title = titleFetcher.apply(url);
        long now = System.currentTimeMillis();

        String code = alias;
        if (alias != null) {
            if (!insert(alias, url, title, now)) { error(ex, 409, "Alias already in use"); return; }
        } else {
            do {
                code = randomCode(7);
            } while (!insert(code, url, title, now));
        }

        String host = Optional.ofNullable(ex.getRequestHeaders().getFirst("Host")).orElse("localhost");
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("code", code);
        out.put("shortUrl", "http://" + host + "/" + code);
        out.put("url", url);
        out.put("title", title);
        out.put("createdAt", Instant.ofEpochMilli(now).toString());
        send(ex, 201, out);
    }

    private void redirect(HttpExchange ex, String code) throws IOException, SQLException {
        String url = hit(code);
        if (url == null) { error(ex, 404, "Unknown code"); return; }
        ex.getResponseHeaders().set("Location", url);
        ex.getResponseHeaders().set("Cache-Control", "no-store");
        ex.sendResponseHeaders(302, -1);
    }

    private void stats(HttpExchange ex, String code) throws IOException, SQLException {
        try (Connection c = connect();
             PreparedStatement ps = c.prepareStatement(
                     "SELECT url, title, created_at, clicks FROM links WHERE code = ?")) {
            ps.setString(1, code);
            try (ResultSet rs = ps.executeQuery()) {
                if (!rs.next()) { error(ex, 404, "Unknown code"); return; }
                Map<String, Object> out = new LinkedHashMap<>();
                out.put("code", code);
                out.put("url", rs.getString("url"));
                out.put("title", rs.getString("title"));
                out.put("createdAt", Instant.ofEpochMilli(rs.getLong("created_at")).toString());
                out.put("clicks", rs.getLong("clicks"));
                send(ex, 200, out);
            }
        }
    }

    private static boolean validUrl(String url) {
        if (url.length() > MAX_URL) return false;
        try {
            URI u = new URI(url);
            return u.getScheme() != null
                    && (u.getScheme().equalsIgnoreCase("http") || u.getScheme().equalsIgnoreCase("https"))
                    && u.getHost() != null;
        } catch (Exception e) {
            return false;
        }
    }

    private static void methodNotAllowed(HttpExchange ex, String allowed) throws IOException {
        ex.getResponseHeaders().set("Allow", allowed);
        error(ex, 405, "Method not allowed");
    }

    private static void error(HttpExchange ex, int status, String message) throws IOException {
        send(ex, status, Map.of("error", message));
    }

    private static void send(HttpExchange ex, int status, Object payload) throws IOException {
        byte[] bytes = JSON.writeValueAsBytes(payload);
        ex.getResponseHeaders().set("Content-Type", "application/json");
        ex.sendResponseHeaders(status, bytes.length);
        ex.getResponseBody().write(bytes);
    }

    // ---- Persistence ----

    private Connection connect() throws SQLException {
        return DriverManager.getConnection(jdbcUrl);
    }

    /** Returns false if the code already exists. */
    private boolean insert(String code, String url, String title, long createdAt) throws SQLException {
        try (Connection c = connect();
             PreparedStatement ps = c.prepareStatement(
                     "INSERT OR IGNORE INTO links (code, url, title, created_at) VALUES (?, ?, ?, ?)")) {
            ps.setString(1, code);
            ps.setString(2, url);
            ps.setString(3, title);
            ps.setLong(4, createdAt);
            return ps.executeUpdate() == 1;
        }
    }

    /** Increments the click counter and returns the target URL, or null if the code is unknown. */
    private String hit(String code) throws SQLException {
        try (Connection c = connect();
             PreparedStatement ps = c.prepareStatement(
                     "UPDATE links SET clicks = clicks + 1 WHERE code = ?")) {
            ps.setString(1, code);
            if (ps.executeUpdate() == 0) return null;
        }
        try (Connection c = connect();
             PreparedStatement ps = c.prepareStatement("SELECT url FROM links WHERE code = ?")) {
            ps.setString(1, code);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? rs.getString(1) : null;
            }
        }
    }

    private String randomCode(int length) {
        StringBuilder sb = new StringBuilder(length);
        for (int i = 0; i < length; i++) sb.append(ALPHABET.charAt(random.nextInt(ALPHABET.length())));
        return sb.toString();
    }

    // ---- Title fetching ----

    /**
     * Fetches the page title of a URL. Returns null on any failure (the link is still created).
     * Since the URL is user-supplied, requests to loopback/private/link-local addresses are refused,
     * redirects are not followed, and the response is capped at 64 KiB.
     */
    public static class TitleFetcher implements Function<String, String> {
        private static final Pattern TITLE =
                Pattern.compile("<title[^>]*>(.*?)</title>", Pattern.CASE_INSENSITIVE | Pattern.DOTALL);
        private static final int MAX_BYTES = 64 * 1024;
        private final HttpClient client = HttpClient.newBuilder()
                .followRedirects(HttpClient.Redirect.NEVER)
                .connectTimeout(Duration.ofSeconds(3))
                .build();

        @Override
        public String apply(String url) {
            try {
                URI uri = URI.create(url);
                if (!isPublicHost(uri.getHost())) return null;
                HttpRequest req = HttpRequest.newBuilder(uri)
                        .timeout(Duration.ofSeconds(5))
                        .header("Accept", "text/html")
                        .GET().build();
                HttpResponse<java.io.InputStream> resp =
                        client.send(req, HttpResponse.BodyHandlers.ofInputStream());
                try (java.io.InputStream in = resp.body()) {
                    String type = resp.headers().firstValue("Content-Type").orElse("");
                    if (resp.statusCode() != 200 || !type.toLowerCase().contains("html")) return null;
                    String html = new String(in.readNBytes(MAX_BYTES), StandardCharsets.UTF_8);
                    Matcher m = TITLE.matcher(html);
                    if (!m.find()) return null;
                    String t = unescape(m.group(1)).replaceAll("\\s+", " ").trim();
                    if (t.isEmpty()) return null;
                    return t.length() > 300 ? t.substring(0, 300) : t;
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return null;
            } catch (Exception e) {
                return null;
            }
        }

        static boolean isPublicHost(String host) {
            if (host == null) return false;
            try {
                for (InetAddress a : InetAddress.getAllByName(host)) {
                    byte[] b = a.getAddress();
                    boolean ula = b.length == 16 && (b[0] & 0xfe) == 0xfc;
                    boolean cgnat = b.length == 4 && (b[0] & 0xff) == 100 && (b[1] & 0xc0) == 0x40;
                    if (a.isAnyLocalAddress() || a.isLoopbackAddress() || a.isLinkLocalAddress()
                            || a.isSiteLocalAddress() || a.isMulticastAddress() || ula || cgnat) {
                        return false;
                    }
                }
                return true;
            } catch (Exception e) {
                return false;
            }
        }

        private static String unescape(String s) {
            return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"")
                    .replace("&#39;", "'").replace("&amp;", "&");
        }
    }
}
