import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import static org.junit.jupiter.api.Assertions.assertEquals;

class WebhookServerTest {

    private static final byte[] SECRET = "test-secret".getBytes(StandardCharsets.UTF_8);
    private static final ObjectMapper JSON = new ObjectMapper();

    @TempDir
    Path dir;

    private PaymentStore store;
    private WebhookServer server;
    private final HttpClient client = HttpClient.newHttpClient();

    @BeforeEach
    void start() throws Exception {
        startServer();
    }

    @AfterEach
    void stop() throws Exception {
        server.stop();
        store.close();
    }

    private void startServer() throws Exception {
        store = new PaymentStore("jdbc:sqlite:" + dir.resolve("test.db"));
        server = new WebhookServer(new InetSocketAddress("127.0.0.1", 0), store, SECRET);
        server.start();
    }

    // ---- helpers ------------------------------------------------------------

    private static String event(String id, String type, String orderId, long amount) {
        return String.format("{\"id\":\"%s\",\"type\":\"%s\",\"order_id\":\"%s\",\"amount\":%d}", id, type, orderId, amount);
    }

    private static String sign(String body) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(SECRET, "HmacSHA256"));
        return HexFormat.of().formatHex(mac.doFinal(body.getBytes(StandardCharsets.UTF_8)));
    }

    private HttpResponse<String> post(String body, String signature) throws Exception {
        HttpRequest.Builder req = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + server.port() + "/webhooks"))
                .POST(HttpRequest.BodyPublishers.ofString(body));
        if (signature != null) {
            req.header(WebhookServer.SIGNATURE_HEADER, signature);
        }
        return client.send(req.build(), HttpResponse.BodyHandlers.ofString());
    }

    private HttpResponse<String> postSigned(String body) throws Exception {
        return post(body, sign(body));
    }

    private HttpResponse<String> getOrder(String orderId) throws Exception {
        HttpRequest req = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + server.port() + "/orders/" + orderId)).GET().build();
        return client.send(req, HttpResponse.BodyHandlers.ofString());
    }

    private JsonNode order(String orderId) throws Exception {
        HttpResponse<String> res = getOrder(orderId);
        assertEquals(200, res.statusCode(), res.body());
        return JSON.readTree(res.body());
    }

    // ---- tests --------------------------------------------------------------

    @Test
    void appliesSignedPayment() throws Exception {
        HttpResponse<String> res = postSigned(event("evt_1", "payment.succeeded", "ord_1", 1500));
        assertEquals(200, res.statusCode());
        assertEquals("applied", JSON.readTree(res.body()).get("result").asText());

        JsonNode o = order("ord_1");
        assertEquals("PAID", o.get("status").asText());
        assertEquals(1500, o.get("amount_paid").asLong());
        assertEquals(0, o.get("amount_refunded").asLong());
    }

    @Test
    void acceptsPrefixedSignature() throws Exception {
        String body = event("evt_1", "payment.succeeded", "ord_1", 100);
        assertEquals(200, post(body, "sha256=" + sign(body)).statusCode());
    }

    @Test
    void redeliveredEventIsAppliedOnce() throws Exception {
        String body = event("evt_1", "payment.succeeded", "ord_1", 1500);
        assertEquals("applied", JSON.readTree(postSigned(body).body()).get("result").asText());

        HttpResponse<String> retry = postSigned(body);
        assertEquals(200, retry.statusCode());
        assertEquals("duplicate", JSON.readTree(retry.body()).get("result").asText());

        assertEquals(1500, order("ord_1").get("amount_paid").asLong());
    }

    @Test
    void duplicateRefundIsNotDoubleCounted() throws Exception {
        postSigned(event("evt_pay", "payment.succeeded", "ord_1", 1000));
        String refund = event("evt_ref", "refund.created", "ord_1", 400);
        postSigned(refund);
        postSigned(refund);
        postSigned(refund);

        JsonNode o = order("ord_1");
        assertEquals(400, o.get("amount_refunded").asLong());
        assertEquals("PARTIALLY_REFUNDED", o.get("status").asText());
    }

    @Test
    void concurrentRedeliveriesAreAppliedOnce() throws Exception {
        String body = event("evt_race", "payment.succeeded", "ord_1", 700);
        String sig = sign(body);
        ExecutorService pool = Executors.newFixedThreadPool(16);
        try {
            List<Callable<HttpResponse<String>>> calls = new ArrayList<>();
            for (int i = 0; i < 32; i++) {
                calls.add(() -> post(body, sig));
            }
            int applied = 0;
            for (Future<HttpResponse<String>> f : pool.invokeAll(calls)) {
                HttpResponse<String> res = f.get();
                assertEquals(200, res.statusCode(), res.body());
                if (JSON.readTree(res.body()).get("result").asText().equals("applied")) {
                    applied++;
                }
            }
            assertEquals(1, applied);
        } finally {
            pool.shutdownNow();
        }
        assertEquals(700, order("ord_1").get("amount_paid").asLong());
    }

    @Test
    void deduplicationSurvivesRestart() throws Exception {
        String body = event("evt_1", "payment.succeeded", "ord_1", 500);
        postSigned(body);

        server.stop();
        store.close();
        startServer();

        assertEquals("duplicate", JSON.readTree(postSigned(body).body()).get("result").asText());
        assertEquals(500, order("ord_1").get("amount_paid").asLong());
    }

    @Test
    void reusedEventIdWithDifferentContentIsRejected() throws Exception {
        postSigned(event("evt_1", "payment.succeeded", "ord_1", 500));
        assertEquals(409, postSigned(event("evt_1", "payment.succeeded", "ord_1", 9999)).statusCode());
        assertEquals(500, order("ord_1").get("amount_paid").asLong());
    }

    @Test
    void rejectsMissingOrWrongSignature() throws Exception {
        String body = event("evt_1", "payment.succeeded", "ord_1", 500);
        assertEquals(401, post(body, null).statusCode());
        assertEquals(401, post(body, "not-hex").statusCode());
        assertEquals(401, post(body, "00".repeat(32)).statusCode());

        String tampered = event("evt_1", "payment.succeeded", "ord_1", 5000000);
        assertEquals(401, post(tampered, sign(body)).statusCode());

        assertEquals(404, getOrder("ord_1").statusCode());
    }

    @Test
    void rejectsInvalidPayloads() throws Exception {
        assertEquals(400, postSigned("not json").statusCode());
        assertEquals(400, postSigned("[]").statusCode());
        assertEquals(400, postSigned(event("evt_1", "payment.teleported", "ord_1", 100)).statusCode());
        assertEquals(400, postSigned(event("evt_1", "payment.succeeded", "ord_1", 0)).statusCode());
        assertEquals(400, postSigned(event("evt_1", "payment.succeeded", "ord_1", -5)).statusCode());
        assertEquals(400, postSigned(event("", "payment.succeeded", "ord_1", 100)).statusCode());
        assertEquals(400, postSigned("{\"id\":\"evt_1\",\"type\":\"payment.succeeded\",\"order_id\":\"ord_1\",\"amount\":10.5}").statusCode());
        assertEquals(400, postSigned("{\"id\":\"evt_1\",\"type\":\"payment.succeeded\",\"order_id\":\"ord_1\",\"amount\":\"100\"}").statusCode());
        assertEquals(400, postSigned("{\"id\":\"evt_1\",\"type\":\"payment.succeeded\",\"order_id\":\"ord_1\",\"amount\":1,\"amount\":2}").statusCode());
        assertEquals(400, postSigned("{\"id\":\"evt_1\",\"type\":\"payment.succeeded\",\"order_id\":\"ord_1\"}").statusCode());

        // A rejected event must not consume its id.
        assertEquals("applied", JSON.readTree(postSigned(event("evt_1", "payment.succeeded", "ord_1", 100)).body()).get("result").asText());
    }

    @Test
    void rejectsOversizedBody() throws Exception {
        String body = "{\"pad\":\"" + "x".repeat(WebhookServer.MAX_BODY_BYTES) + "\"}";
        assertEquals(413, postSigned(body).statusCode());
    }

    @Test
    void statusFollowsEventHistory() throws Exception {
        postSigned(event("e1", "payment.failed", "ord_1", 1000));
        assertEquals("PAYMENT_FAILED", order("ord_1").get("status").asText());

        postSigned(event("e2", "payment.succeeded", "ord_1", 1000));
        assertEquals("PAID", order("ord_1").get("status").asText());

        postSigned(event("e3", "refund.created", "ord_1", 300));
        assertEquals("PARTIALLY_REFUNDED", order("ord_1").get("status").asText());

        postSigned(event("e4", "refund.created", "ord_1", 700));
        JsonNode o = order("ord_1");
        assertEquals("REFUNDED", o.get("status").asText());
        assertEquals(1000, o.get("amount_refunded").asLong());
    }

    @Test
    void lateFailureDoesNotOverrideSuccess() throws Exception {
        postSigned(event("e1", "payment.succeeded", "ord_1", 1000));
        postSigned(event("e2", "payment.failed", "ord_1", 1000));
        assertEquals("PAID", order("ord_1").get("status").asText());
    }

    @Test
    void outOfOrderRefundResolvesOncePaymentArrives() throws Exception {
        postSigned(event("e2", "refund.created", "ord_1", 1000));
        assertEquals("PENDING", order("ord_1").get("status").asText());
        postSigned(event("e1", "payment.succeeded", "ord_1", 1000));
        assertEquals("REFUNDED", order("ord_1").get("status").asText());
    }

    @Test
    void unknownOrderIs404AndWrongMethodIs405() throws Exception {
        assertEquals(404, getOrder("nope").statusCode());
        HttpRequest get = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + server.port() + "/webhooks")).GET().build();
        assertEquals(405, client.send(get, HttpResponse.BodyHandlers.ofString()).statusCode());
    }
}
