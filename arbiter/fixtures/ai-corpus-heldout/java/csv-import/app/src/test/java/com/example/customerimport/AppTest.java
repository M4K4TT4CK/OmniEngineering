package com.example.customerimport;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.fail;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Path;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class AppTest {

    static final int MAX_UPLOAD_BYTES = 4096;
    static final String CSV = "name,email,phone,country,signup_date\n"
            + "Ada Lovelace,ada@example.com,,GB,2024-01-15\n"
            + "Bad,not-an-email,,GB,2024-01-15\n"
            + "Worse,,,GB,2024-01-15\n";

    static final ObjectMapper JSON = new ObjectMapper();

    @TempDir
    Path dir;

    ImportService service;
    App app;
    HttpClient client;
    String base;

    @BeforeEach
    void start() throws Exception {
        Clock clock = Clock.fixed(Instant.parse("2025-06-01T12:00:00Z"), ZoneOffset.UTC);
        service = new ImportService("jdbc:sqlite:" + dir.resolve("test.db"), clock, 10);
        app = new App(service, new InetSocketAddress("127.0.0.1", 0), MAX_UPLOAD_BYTES);
        app.start();
        base = "http://127.0.0.1:" + app.port();
        client = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).build();
    }

    @AfterEach
    void stop() {
        app.stop();
        service.close();
    }

    @Test
    void rawCsvUploadIsImportedInTheBackground() throws Exception {
        HttpResponse<String> response = post("text/csv", CSV);

        assertEquals(202, response.statusCode());
        JsonNode body = JSON.readTree(response.body());
        String id = body.get("importId").asText();
        assertEquals("QUEUED", body.get("status").asText());
        assertEquals("/imports/" + id, response.headers().firstValue("Location").orElseThrow());

        JsonNode status = awaitDone(id);
        assertEquals("COMPLETED", status.get("status").asText());
        assertEquals(3, status.get("totalRows").asInt());
        assertEquals(1, status.get("insertedRows").asInt());
        assertEquals(2, status.get("failedRows").asInt());
        assertEquals(2, status.get("errorCount").asInt());
        assertEquals(3, status.get("errors").get(0).get("line").asInt());
        assertEquals("email", status.get("errors").get(0).get("field").asText());
    }

    @Test
    void multipartUploadIsImported() throws Exception {
        String boundary = "----test-boundary-1234";
        String body = "--" + boundary + "\r\n"
                + "Content-Disposition: form-data; name=\"note\"\r\n\r\n"
                + "ignored\r\n"
                + "--" + boundary + "\r\n"
                + "Content-Disposition: form-data; name=\"file\"; filename=\"customers.csv\"\r\n"
                + "Content-Type: text/csv\r\n\r\n"
                + CSV + "\r\n"
                + "--" + boundary + "--\r\n";

        HttpResponse<String> response = post("multipart/form-data; boundary=" + boundary, body);

        assertEquals(202, response.statusCode());
        JsonNode status = awaitDone(JSON.readTree(response.body()).get("importId").asText());
        assertEquals("COMPLETED", status.get("status").asText());
        assertEquals("customers.csv", status.get("filename").asText());
        assertEquals(1, status.get("insertedRows").asInt());
    }

    @Test
    void multipartWithoutFilePartIsRejected() throws Exception {
        String boundary = "b";
        String body = "--b\r\nContent-Disposition: form-data; name=\"other\"\r\n\r\nx\r\n--b--\r\n";

        assertEquals(400, post("multipart/form-data; boundary=" + boundary, body).statusCode());
    }

    @Test
    void errorsArePaginated() throws Exception {
        String id = JSON.readTree(post("text/csv", CSV).body()).get("importId").asText();
        awaitDone(id);

        JsonNode page = getJson("/imports/" + id + "?errorsOffset=1&errorsLimit=1");
        assertEquals(2, page.get("errorCount").asInt());
        assertEquals(1, page.get("errors").size());
        assertEquals(4, page.get("errors").get(0).get("line").asInt());
    }

    @Test
    void rejectsBadRequests() throws Exception {
        assertEquals(400, post("text/csv", "").statusCode());
        assertEquals(413, post("text/csv", "x".repeat(MAX_UPLOAD_BYTES + 1)).statusCode());
        assertEquals(404, get("/imports/no-such-import").statusCode());
        assertEquals(400, get("/imports/abc?errorsLimit=0").statusCode());
        assertEquals(400, get("/imports/abc?errorsOffset=x").statusCode());
        assertEquals(405, get("/imports").statusCode());
        assertEquals(404, get("/nothing-here").statusCode());
    }

    private HttpResponse<String> post(String contentType, String body) throws Exception {
        HttpRequest request = HttpRequest.newBuilder(URI.create(base + "/imports"))
                .header("Content-Type", contentType)
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();
        return client.send(request, HttpResponse.BodyHandlers.ofString());
    }

    private HttpResponse<String> get(String path) throws Exception {
        return client.send(HttpRequest.newBuilder(URI.create(base + path)).GET().build(),
                HttpResponse.BodyHandlers.ofString());
    }

    private JsonNode getJson(String path) throws Exception {
        HttpResponse<String> response = get(path);
        assertEquals(200, response.statusCode(), response.body());
        return JSON.readTree(response.body());
    }

    private JsonNode awaitDone(String id) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10);
        while (true) {
            JsonNode status = getJson("/imports/" + id);
            String state = status.get("status").asText();
            if (state.equals("COMPLETED") || state.equals("FAILED")) {
                return status;
            }
            if (System.nanoTime() > deadline) {
                fail("import did not finish: " + status);
            }
            Thread.sleep(20);
        }
    }
}
