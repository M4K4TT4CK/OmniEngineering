package webhook;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Duration;
import java.util.HexFormat;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Receives signed payment events, stores them in SQLite and forwards successful
 * payments to the merchant's callback URL.
 *
 * Endpoints:
 *   POST /webhooks/payments               provider events (authenticated by X-Signature)
 *   GET  /merchants/{id}/events?limit=N   recent events (Bearer admin token)
 *   PUT  /merchants/{id}/callback         body {"callback_url": "..."} (Bearer admin token)
 */
public class WebhookServer {

    static final String SUCCESS_TYPE = "payment.succeeded";
    static final int MAX_BODY_BYTES = 1024 * 1024;
    static final int MAX_FORWARD_ATTEMPTS = 3;

    private static final ObjectMapper JSON = new ObjectMapper();

    private final HttpServer http;
    private final Store store;
    private final byte[] secret;
    private final byte[] adminToken;
    private final HttpClient client = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5))
            .followRedirects(HttpClient.Redirect.NEVER)
            .build();
    private final ExecutorService forwarder = Executors.newFixedThreadPool(4);
    private final long retryDelayMillis;

    public WebhookServer(int port, Store store, String secret, String adminToken, long retryDelayMillis)
            throws IOException {
        if (secret == null || secret.isEmpty() || adminToken == null || adminToken.isEmpty()) {
            throw new IllegalArgumentException("secret and adminToken must be non-empty");
        }
        this.store = store;
        this.secret = secret.getBytes(StandardCharsets.UTF_8);
        this.adminToken = adminToken.getBytes(StandardCharsets.UTF_8);
        this.retryDelayMillis = retryDelayMillis;
        this.http = HttpServer.create(new InetSocketAddress(port), 0);
        this.http.createContext("/webhooks/payments", this::handleWebhook);
        this.http.createContext("/merchants/", this::handleMerchants);
        this.http.setExecutor(Executors.newFixedThreadPool(8));
    }

    public void start() {
        http.start();
    }

    public void stop() {
        http.stop(0);
        forwarder.shutdownNow();
    }

    public int port() {
        return http.getAddress().getPort();
    }

    public static void main(String[] args) throws Exception {
        String secret = System.getenv("WEBHOOK_SECRET");
        String adminToken = System.getenv("ADMIN_TOKEN");
        if (secret == null || secret.isEmpty() || adminToken == null || adminToken.isEmpty()) {
            System.err.println("WEBHOOK_SECRET and ADMIN_TOKEN environment variables are required");
            System.exit(1);
        }
        String dbPath = System.getenv().getOrDefault("DB_PATH", "webhooks.db");
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8080"));
        WebhookServer server = new WebhookServer(port, new Store("jdbc:sqlite:" + dbPath), secret, adminToken, 1000);
        server.start();
        System.out.println("Listening on port " + server.port());
    }

    // ---- POST /webhooks/payments -------------------------------------------------------------

    private void handleWebhook(HttpExchange ex) throws IOException {
        try (ex) {
            if (!ex.getRequestMethod().equals("POST")) {
                send(ex, 405, error("method not allowed"));
                return;
            }
            byte[] body = readBody(ex);
            if (body == null) {
                send(ex, 413, error("payload too large"));
                return;
            }
            String signature = ex.getRequestHeaders().getFirst("X-Signature");
            if (!validSignature(body, signature)) {
                send(ex, 401, error("invalid signature"));
                return;
            }

            JsonNode event;
            try {
                event = JSON.readTree(body);
            } catch (IOException e) {
                send(ex, 400, error("invalid JSON"));
                return;
            }
            String id = text(event, "id");
            String type = text(event, "type");
            String merchantId = text(event, "merchant_id");
            if (id == null || type == null || merchantId == null) {
                send(ex, 400, error("id, type and merchant_id are required strings"));
                return;
            }

            String callbackUrl = null;
            String status = "not_applicable";
            if (type.equals(SUCCESS_TYPE)) {
                callbackUrl = store.callbackUrl(merchantId);
                status = callbackUrl == null ? "no_callback" : "pending";
            }

            String payload = new String(body, StandardCharsets.UTF_8);
            if (!store.insertEvent(id, merchantId, type, payload, status)) {
                // Providers retry; an already-stored event is acknowledged but not re-forwarded.
                send(ex, 200, JSON.createObjectNode().put("status", "duplicate"));
                return;
            }
            if (callbackUrl != null) {
                String url = callbackUrl;
                forwarder.submit(() -> forward(id, url, body, signature));
            }
            send(ex, 200, JSON.createObjectNode().put("status", "received"));
        } catch (SQLException e) {
            e.printStackTrace();
            send(ex, 500, error("internal error"));
        }
    }

    private boolean validSignature(byte[] body, String header) {
        if (header == null) {
            return false;
        }
        try {
            byte[] provided = HexFormat.of().parseHex(header.trim());
            return MessageDigest.isEqual(hmac(secret, body), provided);
        } catch (IllegalArgumentException e) {
            return false; // not valid hex
        }
    }

    static byte[] hmac(byte[] key, byte[] data) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            return mac.doFinal(data);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }

    private void forward(String eventId, String url, byte[] body, String signature) {
        String result = "failed";
        for (int attempt = 1; attempt <= MAX_FORWARD_ATTEMPTS; attempt++) {
            try {
                HttpRequest req = HttpRequest.newBuilder(URI.create(url))
                        .timeout(Duration.ofSeconds(10))
                        .header("Content-Type", "application/json")
                        .header("X-Signature", signature)
                        .header("X-Event-Id", eventId)
                        .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                        .build();
                int code = client.send(req, HttpResponse.BodyHandlers.discarding()).statusCode();
                if (code >= 200 && code < 300) {
                    result = "delivered";
                    break;
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            } catch (Exception e) {
                // fall through to retry
            }
            if (attempt < MAX_FORWARD_ATTEMPTS) {
                try {
                    Thread.sleep(retryDelayMillis * attempt);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        }
        try {
            store.setDeliveryStatus(eventId, result);
        } catch (SQLException e) {
            e.printStackTrace();
        }
    }

    // ---- /merchants/{id}/... -----------------------------------------------------------------

    private void handleMerchants(HttpExchange ex) throws IOException {
        try (ex) {
            String auth = ex.getRequestHeaders().getFirst("Authorization");
            if (auth == null || !auth.startsWith("Bearer ")
                    || !MessageDigest.isEqual(adminToken, auth.substring(7).getBytes(StandardCharsets.UTF_8))) {
                send(ex, 401, error("unauthorized"));
                return;
            }
            String[] parts = ex.getRequestURI().getPath().split("/");
            // ["", "merchants", "{id}", "events"|"callback"]
            if (parts.length != 4 || parts[2].isEmpty()) {
                send(ex, 404, error("not found"));
                return;
            }
            String merchantId = parts[2];
            String method = ex.getRequestMethod();

            if (parts[3].equals("events") && method.equals("GET")) {
                int limit = parseLimit(ex.getRequestURI().getRawQuery());
                if (limit < 0) {
                    send(ex, 400, error("limit must be an integer between 1 and 200"));
                    return;
                }
                ArrayNode out = JSON.createArrayNode();
                for (ObjectNode row : store.recentEvents(merchantId, limit)) {
                    out.add(row);
                }
                send(ex, 200, out);
            } else if (parts[3].equals("callback") && method.equals("PUT")) {
                byte[] body = readBody(ex);
                if (body == null) {
                    send(ex, 413, error("payload too large"));
                    return;
                }
                String url;
                try {
                    url = text(JSON.readTree(body), "callback_url");
                } catch (IOException e) {
                    url = null;
                }
                if (!validCallbackUrl(url)) {
                    send(ex, 400, error("callback_url must be an absolute http(s) URL"));
                    return;
                }
                store.setCallbackUrl(merchantId, url);
                send(ex, 200, JSON.createObjectNode().put("status", "ok"));
            } else {
                send(ex, 404, error("not found"));
            }
        } catch (SQLException e) {
            e.printStackTrace();
            send(ex, 500, error("internal error"));
        }
    }

    private static boolean validCallbackUrl(String url) {
        if (url == null) {
            return false;
        }
        try {
            URI uri = URI.create(url);
            return ("http".equals(uri.getScheme()) || "https".equals(uri.getScheme())) && uri.getHost() != null;
        } catch (IllegalArgumentException e) {
            return false;
        }
    }

    /** Returns the limit, 50 if absent, or -1 if invalid. */
    private static int parseLimit(String query) {
        if (query == null) {
            return 50;
        }
        for (String pair : query.split("&")) {
            if (pair.startsWith("limit=")) {
                try {
                    int n = Integer.parseInt(pair.substring(6));
                    return n >= 1 && n <= 200 ? n : -1;
                } catch (NumberFormatException e) {
                    return -1;
                }
            }
        }
        return 50;
    }

    // ---- helpers -----------------------------------------------------------------------------

    /** Reads the request body, or returns null if it exceeds MAX_BODY_BYTES. */
    private static byte[] readBody(HttpExchange ex) throws IOException {
        byte[] data = ex.getRequestBody().readNBytes(MAX_BODY_BYTES + 1);
        return data.length > MAX_BODY_BYTES ? null : data;
    }

    private static String text(JsonNode node, String field) {
        JsonNode v = node == null ? null : node.get(field);
        return v != null && v.isTextual() && !v.asText().isEmpty() ? v.asText() : null;
    }

    private static ObjectNode error(String message) {
        return JSON.createObjectNode().put("error", message);
    }

    private static void send(HttpExchange ex, int status, JsonNode body) throws IOException {
        byte[] bytes = JSON.writeValueAsBytes(body);
        ex.getResponseHeaders().set("Content-Type", "application/json");
        ex.sendResponseHeaders(status, bytes.length);
        ex.getResponseBody().write(bytes);
    }

    // ---- SQLite storage ----------------------------------------------------------------------

    /** Single shared connection; all access is serialized, which is plenty for SQLite. */
    public static class Store {
        private final Connection conn;

        public Store(String jdbcUrl) throws SQLException {
            conn = DriverManager.getConnection(jdbcUrl);
            try (Statement st = conn.createStatement()) {
                st.execute("CREATE TABLE IF NOT EXISTS merchants ("
                        + "id TEXT PRIMARY KEY, callback_url TEXT NOT NULL)");
                st.execute("CREATE TABLE IF NOT EXISTS events ("
                        + "id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, type TEXT NOT NULL, "
                        + "payload TEXT NOT NULL, received_at INTEGER NOT NULL, delivery_status TEXT NOT NULL)");
                st.execute("CREATE INDEX IF NOT EXISTS idx_events_merchant "
                        + "ON events (merchant_id, received_at DESC)");
            }
        }

        /** Returns false if an event with this id already exists. */
        synchronized boolean insertEvent(String id, String merchantId, String type, String payload, String status)
                throws SQLException {
            try (PreparedStatement ps = conn.prepareStatement(
                    "INSERT OR IGNORE INTO events (id, merchant_id, type, payload, received_at, delivery_status) "
                            + "VALUES (?, ?, ?, ?, ?, ?)")) {
                ps.setString(1, id);
                ps.setString(2, merchantId);
                ps.setString(3, type);
                ps.setString(4, payload);
                ps.setLong(5, System.currentTimeMillis());
                ps.setString(6, status);
                return ps.executeUpdate() == 1;
            }
        }

        synchronized void setDeliveryStatus(String id, String status) throws SQLException {
            try (PreparedStatement ps = conn.prepareStatement("UPDATE events SET delivery_status = ? WHERE id = ?")) {
                ps.setString(1, status);
                ps.setString(2, id);
                ps.executeUpdate();
            }
        }

        synchronized String callbackUrl(String merchantId) throws SQLException {
            try (PreparedStatement ps = conn.prepareStatement("SELECT callback_url FROM merchants WHERE id = ?")) {
                ps.setString(1, merchantId);
                try (ResultSet rs = ps.executeQuery()) {
                    return rs.next() ? rs.getString(1) : null;
                }
            }
        }

        synchronized void setCallbackUrl(String merchantId, String url) throws SQLException {
            try (PreparedStatement ps = conn.prepareStatement(
                    "INSERT INTO merchants (id, callback_url) VALUES (?, ?) "
                            + "ON CONFLICT(id) DO UPDATE SET callback_url = excluded.callback_url")) {
                ps.setString(1, merchantId);
                ps.setString(2, url);
                ps.executeUpdate();
            }
        }

        synchronized java.util.List<ObjectNode> recentEvents(String merchantId, int limit) throws SQLException {
            java.util.List<ObjectNode> out = new java.util.ArrayList<>();
            try (PreparedStatement ps = conn.prepareStatement(
                    "SELECT id, type, payload, received_at, delivery_status FROM events "
                            + "WHERE merchant_id = ? ORDER BY received_at DESC, rowid DESC LIMIT ?")) {
                ps.setString(1, merchantId);
                ps.setInt(2, limit);
                try (ResultSet rs = ps.executeQuery()) {
                    while (rs.next()) {
                        ObjectNode row = JSON.createObjectNode();
                        row.put("id", rs.getString(1));
                        row.put("type", rs.getString(2));
                        try {
                            row.set("payload", JSON.readTree(rs.getString(3)));
                        } catch (IOException e) {
                            row.put("payload", rs.getString(3));
                        }
                        row.put("received_at", rs.getLong(4));
                        row.put("delivery_status", rs.getString(5));
                        out.add(row);
                    }
                }
            }
            return out;
        }
    }
}
