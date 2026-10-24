import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.io.IOException;
import java.io.InputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * HTTP front end:
 *   POST /webhooks          signed payment events (header X-Signature: hex HMAC-SHA256 of the raw body)
 *   GET  /orders/{orderId}  current payment status of an order
 */
public final class WebhookServer {

    public static final String SIGNATURE_HEADER = "X-Signature";
    static final int MAX_BODY_BYTES = 64 * 1024;
    static final int MAX_ID_LENGTH = 200;
    // Upper bound per event keeps running totals far away from long overflow.
    static final long MAX_AMOUNT = 1_000_000_000_000L;

    private static final Logger LOG = Logger.getLogger(WebhookServer.class.getName());

    private final PaymentStore store;
    private final SecretKeySpec key;
    private final ObjectMapper mapper = new ObjectMapper()
            .enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION)
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS);
    private final HttpServer http;
    private final ExecutorService executor = Executors.newFixedThreadPool(8);

    public WebhookServer(InetSocketAddress address, PaymentStore store, byte[] secret) throws IOException {
        if (secret.length == 0) {
            throw new IllegalArgumentException("webhook secret must not be empty");
        }
        this.store = store;
        this.key = new SecretKeySpec(secret, "HmacSHA256");
        this.http = HttpServer.create(address, 0);
        http.createContext("/webhooks", guarded(this::handleWebhook));
        http.createContext("/orders/", guarded(this::handleOrder));
        http.setExecutor(executor);
    }

    public void start() {
        http.start();
    }

    public void stop() {
        http.stop(1);
        executor.shutdown();
    }

    public int port() {
        return http.getAddress().getPort();
    }

    // ---- handlers ---------------------------------------------------------

    private void handleWebhook(HttpExchange ex) throws Exception {
        if (!ex.getRequestURI().getPath().equals("/webhooks")) {
            respond(ex, 404, Map.of("error", "not found"));
            return;
        }
        if (!ex.getRequestMethod().equals("POST")) {
            ex.getResponseHeaders().set("Allow", "POST");
            respond(ex, 405, Map.of("error", "method not allowed"));
            return;
        }

        byte[] body;
        try (InputStream in = ex.getRequestBody()) {
            body = in.readNBytes(MAX_BODY_BYTES + 1);
        }
        if (body.length > MAX_BODY_BYTES) {
            respond(ex, 413, Map.of("error", "body too large"));
            return;
        }

        // Authenticate before looking at the content at all.
        if (!signatureValid(body, ex.getRequestHeaders().getFirst(SIGNATURE_HEADER))) {
            respond(ex, 401, Map.of("error", "invalid signature"));
            return;
        }

        PaymentStore.Event event;
        try {
            event = parseEvent(body);
        } catch (InvalidEventException e) {
            respond(ex, 400, Map.of("error", e.getMessage()));
            return;
        }

        PaymentStore.Outcome outcome = store.apply(event);
        switch (outcome) {
            case APPLIED -> respond(ex, 200, Map.of("result", "applied"));
            // Acknowledge retries with 2xx so the provider stops redelivering.
            case DUPLICATE -> respond(ex, 200, Map.of("result", "duplicate"));
            case CONFLICT -> respond(ex, 409, Map.of("error", "event id already used for a different event"));
        }
    }

    private void handleOrder(HttpExchange ex) throws Exception {
        if (!ex.getRequestMethod().equals("GET")) {
            ex.getResponseHeaders().set("Allow", "GET");
            respond(ex, 405, Map.of("error", "method not allowed"));
            return;
        }
        String orderId = ex.getRequestURI().getPath().substring("/orders/".length());
        if (orderId.isEmpty() || orderId.contains("/") || orderId.length() > MAX_ID_LENGTH) {
            respond(ex, 404, Map.of("error", "not found"));
            return;
        }
        Optional<PaymentStore.Order> order = store.findOrder(orderId);
        if (order.isEmpty()) {
            respond(ex, 404, Map.of("error", "order not found"));
            return;
        }
        PaymentStore.Order o = order.get();
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("order_id", o.orderId());
        out.put("status", o.status());
        out.put("amount_paid", o.amountPaid());
        out.put("amount_refunded", o.amountRefunded());
        out.put("updated_at", o.updatedAt());
        respond(ex, 200, out);
    }

    // ---- signature & parsing ----------------------------------------------

    boolean signatureValid(byte[] body, String header) {
        if (header == null) {
            return false;
        }
        String hex = header.trim();
        if (hex.startsWith("sha256=")) {
            hex = hex.substring("sha256=".length());
        }
        byte[] provided;
        try {
            provided = HexFormat.of().parseHex(hex);
        } catch (IllegalArgumentException e) {
            return false;
        }
        // Constant-time comparison so timing does not leak the expected MAC.
        return MessageDigest.isEqual(hmac(body), provided);
    }

    byte[] hmac(byte[] body) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(key);
            return mac.doFinal(body);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }

    private PaymentStore.Event parseEvent(byte[] body) throws InvalidEventException {
        JsonNode root;
        try {
            root = mapper.readTree(body);
        } catch (IOException e) {
            throw new InvalidEventException("malformed JSON");
        }
        if (root == null || !root.isObject()) {
            throw new InvalidEventException("body must be a JSON object");
        }
        String id = requireId(root, "id");
        String orderId = requireId(root, "order_id");

        JsonNode typeNode = root.get("type");
        if (typeNode == null || !typeNode.isTextual() || !PaymentStore.EVENT_TYPES.contains(typeNode.asText())) {
            throw new InvalidEventException("type must be one of " + PaymentStore.EVENT_TYPES);
        }

        JsonNode amountNode = root.get("amount");
        if (amountNode == null || !amountNode.isIntegralNumber() || !amountNode.canConvertToLong()) {
            throw new InvalidEventException("amount must be an integer in minor units");
        }
        long amount = amountNode.longValue();
        if (amount <= 0 || amount > MAX_AMOUNT) {
            throw new InvalidEventException("amount out of range");
        }
        return new PaymentStore.Event(id, typeNode.asText(), orderId, amount);
    }

    private static String requireId(JsonNode root, String field) throws InvalidEventException {
        JsonNode node = root.get(field);
        if (node == null || !node.isTextual() || node.asText().isBlank() || node.asText().length() > MAX_ID_LENGTH) {
            throw new InvalidEventException(field + " must be a non-empty string of at most " + MAX_ID_LENGTH + " characters");
        }
        return node.asText();
    }

    private static final class InvalidEventException extends Exception {
        InvalidEventException(String message) {
            super(message);
        }
    }

    // ---- plumbing ---------------------------------------------------------

    private interface Handler {
        void handle(HttpExchange ex) throws Exception;
    }

    private HttpHandler guarded(Handler h) {
        return ex -> {
            try {
                h.handle(ex);
            } catch (Exception e) {
                // Nothing was committed; a 5xx makes the provider retry later.
                LOG.log(Level.SEVERE, "request failed", e);
                try {
                    respond(ex, 500, Map.of("error", "internal error"));
                } catch (IOException ignored) {
                    // response may already have been started
                }
            } finally {
                ex.close();
            }
        };
    }

    private void respond(HttpExchange ex, int status, Object body) throws IOException {
        byte[] bytes;
        try {
            bytes = mapper.writeValueAsBytes(body);
        } catch (JsonProcessingException e) {
            throw new IOException(e);
        }
        ex.getResponseHeaders().set("Content-Type", "application/json");
        ex.sendResponseHeaders(status, bytes.length);
        ex.getResponseBody().write(bytes);
    }

    public static void main(String[] args) throws Exception {
        String secret = System.getenv("WEBHOOK_SECRET");
        if (secret == null || secret.isEmpty()) {
            System.err.println("WEBHOOK_SECRET must be set");
            System.exit(1);
        }
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8080"));
        String dbPath = System.getenv().getOrDefault("DB_PATH", "payments.db");

        PaymentStore store = new PaymentStore("jdbc:sqlite:" + dbPath);
        WebhookServer server = new WebhookServer(new InetSocketAddress(port), store, secret.getBytes(StandardCharsets.UTF_8));
        server.start();
        LOG.info("listening on port " + server.port() + ", database " + dbPath);

        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            server.stop();
            try {
                store.close();
            } catch (Exception e) {
                LOG.log(Level.WARNING, "closing database failed", e);
            }
        }));
    }
}
