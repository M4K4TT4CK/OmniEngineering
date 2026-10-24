package jobqueue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Path;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.*;

class JobQueueTest {
    private static final ObjectMapper JSON = new ObjectMapper();

    private static JsonNode obj(String json) throws Exception {
        return JSON.readTree(json);
    }

    private static JobQueue.Job await(JobQueue.Job job, JobQueue.Status status) throws Exception {
        long deadline = System.currentTimeMillis() + 5000;
        while (job.status() != status) {
            if (System.currentTimeMillis() > deadline) {
                fail("Timed out waiting for " + status + ", was " + job.status());
            }
            Thread.sleep(10);
        }
        return job;
    }

    @Test
    void successfulJobStoresResult() throws Exception {
        try (JobQueue q = new JobQueue(Map.of("echo", p -> p.get("x").asText()), 2, 3, 10)) {
            JobQueue.Job job = await(q.submit("echo", obj("{\"x\":\"hi\"}")), JobQueue.Status.SUCCEEDED);
            assertEquals("hi", job.result());
            assertEquals(1, job.attempts());
            assertSame(job, q.get(job.id));
        }
    }

    @Test
    void retriesUntilSuccess() throws Exception {
        AtomicInteger calls = new AtomicInteger();
        JobQueue.Handler flaky = p -> {
            if (calls.incrementAndGet() < 3) throw new RuntimeException("boom");
            return "ok";
        };
        try (JobQueue q = new JobQueue(Map.of("flaky", flaky), 1, 3, 10)) {
            JobQueue.Job job = await(q.submit("flaky", obj("{}")), JobQueue.Status.SUCCEEDED);
            assertEquals(3, job.attempts());
            assertEquals("ok", job.result());
        }
    }

    @Test
    void failsAfterInitialAttemptPlusThreeRetries() throws Exception {
        AtomicInteger calls = new AtomicInteger();
        JobQueue.Handler bad = p -> {
            calls.incrementAndGet();
            throw new IllegalStateException("nope");
        };
        try (JobQueue q = new JobQueue(Map.of("bad", bad), 1, 3, 10)) {
            JobQueue.Job job = await(q.submit("bad", obj("{}")), JobQueue.Status.FAILED);
            assertEquals(4, job.attempts());
            assertEquals(4, calls.get());
            assertTrue(job.error().contains("nope"));
        }
    }

    @Test
    void processesJobsConcurrently() throws Exception {
        CountDownLatch bothRunning = new CountDownLatch(2);
        JobQueue.Handler h = p -> {
            bothRunning.countDown();
            // Only completes if two workers are inside the handler at once.
            return bothRunning.await(5, TimeUnit.SECONDS);
        };
        try (JobQueue q = new JobQueue(Map.of("wait", h), 2, 0, 10)) {
            JobQueue.Job a = q.submit("wait", obj("{}"));
            JobQueue.Job b = q.submit("wait", obj("{}"));
            assertEquals(true, await(a, JobQueue.Status.SUCCEEDED).result());
            assertEquals(true, await(b, JobQueue.Status.SUCCEEDED).result());
        }
    }

    @Test
    void rejectsUnknownTypeAndNonObjectPayload() {
        try (JobQueue q = new JobQueue(Map.of("echo", p -> "x"), 1, 3, 10)) {
            assertThrows(IllegalArgumentException.class, () -> q.submit("nope", JSON.createObjectNode()));
            assertThrows(IllegalArgumentException.class, () -> q.submit("echo", null));
            assertThrows(IllegalArgumentException.class, () -> q.submit("echo", JSON.getNodeFactory().textNode("s")));
        }
    }

    @Test
    void resizeImageWritesScaledCopy(@TempDir Path dir) throws Exception {
        Path img = dir.resolve("pic.png");
        ImageIO.write(new BufferedImage(400, 200, BufferedImage.TYPE_INT_RGB), "png", img.toFile());

        Handlers.ResizeImage h = new Handlers.ResizeImage(null);
        JsonNode payload = obj("{\"path\":\"" + img.toString().replace("\\", "\\\\") + "\",\"width\":100}");
        h.validate(payload);
        Map<?, ?> result = (Map<?, ?>) h.handle(payload);

        BufferedImage out = ImageIO.read(Path.of((String) result.get("outputPath")).toFile());
        assertEquals(100, out.getWidth());
        assertEquals(50, out.getHeight());
    }

    @Test
    void resizeImageValidation(@TempDir Path dir) throws Exception {
        Handlers.ResizeImage h = new Handlers.ResizeImage(dir.toString());
        assertThrows(IllegalArgumentException.class, () -> h.validate(obj("{}")));
        assertThrows(IllegalArgumentException.class, () -> h.validate(obj("{\"path\":\"/etc/passwd\"}")));
        assertThrows(IllegalArgumentException.class,
                () -> h.validate(obj("{\"path\":\"" + dir.resolve("../x.png").toString().replace("\\", "\\\\") + "\"}")));
        assertThrows(IllegalArgumentException.class,
                () -> h.validate(obj("{\"path\":\"" + dir.resolve("a.png").toString().replace("\\", "\\\\") + "\",\"width\":0}")));
    }

    @Test
    void sendEmailValidationAndMissingConfig() throws Exception {
        Handlers.SendEmail h = new Handlers.SendEmail(Map.of());
        h.validate(obj("{\"recipient\":\"a@b.com\",\"subject\":\"s\",\"body\":\"b\"}"));
        assertThrows(IllegalArgumentException.class, () -> h.validate(obj("{\"recipient\":\"a@b.com\",\"subject\":\"s\"}")));
        assertThrows(IllegalArgumentException.class,
                () -> h.validate(obj("{\"recipient\":\"not-an-email\",\"subject\":\"s\",\"body\":\"b\"}")));
        assertThrows(IllegalArgumentException.class,
                () -> h.validate(obj("{\"recipient\":\"a@b.com\",\"subject\":\"s\\nBcc: x@y.com\",\"body\":\"b\"}")));
        assertThrows(IllegalStateException.class,
                () -> h.handle(obj("{\"recipient\":\"a@b.com\",\"subject\":\"s\",\"body\":\"b\"}")));
    }

    @Test
    void httpApiSubmitAndPoll() throws Exception {
        try (JobQueue q = new JobQueue(Map.of("echo", p -> Map.of("got", p.get("x").asText())), 2, 3, 10)) {
            HttpServer server = App.createServer(q, 0);
            server.start();
            try {
                String base = "http://localhost:" + server.getAddress().getPort();
                HttpClient client = HttpClient.newHttpClient();

                HttpResponse<String> created = client.send(HttpRequest.newBuilder(URI.create(base + "/jobs"))
                        .POST(HttpRequest.BodyPublishers.ofString("{\"type\":\"echo\",\"payload\":{\"x\":\"v\"}}"))
                        .build(), HttpResponse.BodyHandlers.ofString());
                assertEquals(202, created.statusCode());
                String id = JSON.readTree(created.body()).get("id").asText();

                JsonNode status = null;
                for (int i = 0; i < 200; i++) {
                    HttpResponse<String> r = client.send(HttpRequest.newBuilder(URI.create(base + "/jobs/" + id)).build(),
                            HttpResponse.BodyHandlers.ofString());
                    assertEquals(200, r.statusCode());
                    status = JSON.readTree(r.body());
                    if (status.get("status").asText().equals("SUCCEEDED")) break;
                    Thread.sleep(25);
                }
                assertEquals("SUCCEEDED", status.get("status").asText());
                assertEquals("v", status.get("result").get("got").asText());

                assertEquals(404, client.send(HttpRequest.newBuilder(URI.create(base + "/jobs/missing")).build(),
                        HttpResponse.BodyHandlers.ofString()).statusCode());
                assertEquals(400, client.send(HttpRequest.newBuilder(URI.create(base + "/jobs"))
                        .POST(HttpRequest.BodyPublishers.ofString("{\"type\":\"nope\",\"payload\":{}}")).build(),
                        HttpResponse.BodyHandlers.ofString()).statusCode());
                assertEquals(400, client.send(HttpRequest.newBuilder(URI.create(base + "/jobs"))
                        .POST(HttpRequest.BodyPublishers.ofString("not json")).build(),
                        HttpResponse.BodyHandlers.ofString()).statusCode());
            } finally {
                server.stop(0);
            }
        }
    }
}
