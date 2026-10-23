package webhook;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.HexFormat;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.*;

class WebhookServerTest {
    static final String SECRET = "test-secret";
    static final String TOKEN = "admin-token";
    static final ObjectMapper JSON = new ObjectMapper();

    final HttpClient client = HttpClient.newHttpClient();
    WebhookServer server;
    HttpServer callback;
    final List<String> callbackBodies = new CopyOnWriteArrayList<>();
    final AtomicInteger callbackStatus = new AtomicInteger(200);
    String base;

    @BeforeEach
    void setUp() throws Exception {
        // In-memory SQLite is per-connection; Store uses exactly one connection.
        server = new WebhookServer(0, new WebhookServer.Store("jdbc:sqlite::memory:"), SECRET, TOKEN, 10);
        server.start();
        base = "http://localhost:" + server.port();

        callback = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
        callback.createContext("/hook", ex -> {
            callbackBodies.add(new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            ex.sendResponseHeaders(callbackStatus.get(), -1);
            ex.close();
        });
        callback.start();
    }

    @AfterEach
    void tearDown() {
        server.stop();
        callback.stop(0);
    }

    String callbackUrl() {
        return "http://localhost:" + callback.getAddress().getPort() + "/hook";
    }

    static String sign(String body) {
        return HexFormat.of().formatHex(WebhookServer.hmac(SECRET.getBytes(StandardCharsets.UTF_8),
                body.getBytes(StandardCharsets.UTF_8)));
    }

    static String event(String id, String type, String merchant) {
        return "{\"id\":\"" + id + "\",\"type\":\"" + type + "\",\"merchant_id\":\"" + merchant
                + "\",\"data\":{\"amount\":1000}}";
    }

    HttpResponse<String> post(String body, String signature) throws Exception {
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(base + "/webhooks/payments"))
                .POST(HttpRequest.BodyPublishers.ofString(body));
        if (signature != null) {
            b.header("X-Signature", signature);
        }
        return client.send(b.build(), HttpResponse.BodyHandlers.ofString());
    }

    HttpResponse<String> adminGet(String path, String token) throws Exception {
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(base + path)).GET();
        if (token != null) {
            b.header("Authorization", "Bearer " + token);
        }
        return client.send(b.build(), HttpResponse.BodyHandlers.ofString());
    }

    void setCallback(String merchant, String url) throws Exception {
        HttpResponse<String> r = client.send(HttpRequest.newBuilder(URI.create(base + "/merchants/" + merchant + "/callback"))
                .header("Authorization", "Bearer " + TOKEN)
                .PUT(HttpRequest.BodyPublishers.ofString("{\"callback_url\":\"" + url + "\"}"))
                .build(), HttpResponse.BodyHandlers.ofString());
        assertEquals(200, r.statusCode());
    }

    JsonNode events(String merchant) throws Exception {
        HttpResponse<String> r = adminGet("/merchants/" + merchant + "/events", TOKEN);
        assertEquals(200, r.statusCode());
        return JSON.readTree(r.body());
    }

    void awaitStatus(String merchant, String expected) throws Exception {
        for (int i = 0; i < 100; i++) {
            if (expected.equals(events(merchant).get(0).get("delivery_status").asText())) {
                return;
            }
            Thread.sleep(50);
        }
        fail("delivery_status never became " + expected);
    }

    @Test
    void rejectsMissingBadAndNonHexSignatures() throws Exception {
        String body = event("e1", "payment.succeeded", "m1");
        assertEquals(401, post(body, null).statusCode());
        assertEquals(401, post(body, sign(body.replace("1000", "9999"))).statusCode());
        assertEquals(401, post(body, "not-hex").statusCode());
        assertEquals(0, events("m1").size());
    }

    @Test
    void storesValidEventAndListsNewestFirst() throws Exception {
        String a = event("e1", "payment.failed", "m1");
        String b = event("e2", "payment.refunded", "m1");
        assertEquals(200, post(a, sign(a)).statusCode());
        assertEquals(200, post(b, sign(b)).statusCode());

        JsonNode list = events("m1");
        assertEquals(2, list.size());
        assertEquals("e2", list.get(0).get("id").asText());
        assertEquals(1000, list.get(0).get("payload").get("data").get("amount").asInt());
        assertEquals("not_applicable", list.get(0).get("delivery_status").asText());
        assertEquals(1, JSON.readTree(adminGet("/merchants/m1/events?limit=1", TOKEN).body()).size());
        assertEquals(0, events("other").size());
    }

    @Test
    void rejectsMalformedEvents() throws Exception {
        String notJson = "nope";
        assertEquals(400, post(notJson, sign(notJson)).statusCode());
        String missing = "{\"id\":\"e1\"}";
        assertEquals(400, post(missing, sign(missing)).statusCode());
    }

    @Test
    void duplicateEventIsAcknowledgedAndNotForwardedTwice() throws Exception {
        setCallback("m1", callbackUrl());
        String body = event("e1", "payment.succeeded", "m1");
        assertEquals(200, post(body, sign(body)).statusCode());
        awaitStatus("m1", "delivered");

        HttpResponse<String> second = post(body, sign(body));
        assertEquals(200, second.statusCode());
        assertEquals("duplicate", JSON.readTree(second.body()).get("status").asText());
        assertEquals(1, events("m1").size());
        assertEquals(1, callbackBodies.size());
    }

    @Test
    void forwardsSuccessfulPaymentsOnly() throws Exception {
        setCallback("m1", callbackUrl());
        String ok = event("e1", "payment.succeeded", "m1");
        assertEquals(200, post(ok, sign(ok)).statusCode());
        awaitStatus("m1", "delivered");
        assertEquals(List.of(ok), callbackBodies);

        String failed = event("e2", "payment.failed", "m1");
        assertEquals(200, post(failed, sign(failed)).statusCode());
        Thread.sleep(200);
        assertEquals(1, callbackBodies.size());
    }

    @Test
    void marksNoCallbackWhenMerchantHasNone() throws Exception {
        String body = event("e1", "payment.succeeded", "m1");
        assertEquals(200, post(body, sign(body)).statusCode());
        assertEquals("no_callback", events("m1").get(0).get("delivery_status").asText());
    }

    @Test
    void retriesThenMarksFailedWhenCallbackErrors() throws Exception {
        callbackStatus.set(500);
        setCallback("m1", callbackUrl());
        String body = event("e1", "payment.succeeded", "m1");
        assertEquals(200, post(body, sign(body)).statusCode());
        awaitStatus("m1", "failed");
        assertEquals(WebhookServer.MAX_FORWARD_ATTEMPTS, callbackBodies.size());
    }

    @Test
    void adminEndpointsRequireTokenAndValidInput() throws Exception {
        assertEquals(401, adminGet("/merchants/m1/events", null).statusCode());
        assertEquals(401, adminGet("/merchants/m1/events", "wrong").statusCode());
        assertEquals(400, adminGet("/merchants/m1/events?limit=0", TOKEN).statusCode());
        assertEquals(400, adminGet("/merchants/m1/events?limit=abc", TOKEN).statusCode());

        HttpResponse<String> bad = client.send(HttpRequest.newBuilder(URI.create(base + "/merchants/m1/callback"))
                .header("Authorization", "Bearer " + TOKEN)
                .PUT(HttpRequest.BodyPublishers.ofString("{\"callback_url\":\"file:///etc/passwd\"}"))
                .build(), HttpResponse.BodyHandlers.ofString());
        assertEquals(400, bad.statusCode());
    }
}
