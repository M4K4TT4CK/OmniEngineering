import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ThumbnailServiceTest {

    @TempDir
    Path cacheDir;

    private final ObjectMapper json = new ObjectMapper();
    private final HttpClient client = HttpClient.newHttpClient();
    private HttpServer server;
    private String base;

    @BeforeEach
    void start() throws IOException {
        server = new ThumbnailService(cacheDir, true).start(new InetSocketAddress("127.0.0.1", 0));
        base = "http://127.0.0.1:" + server.getAddress().getPort();
    }

    @AfterEach
    void stop() {
        server.stop(0);
    }

    @Test
    void uploadReturnsThumbnailsAtRequestedWidths() throws Exception {
        HttpResponse<byte[]> resp = postImage(png(200, 100), "50,100");

        assertEquals(200, resp.statusCode());
        JsonNode body = json.readTree(resp.body());
        assertEquals(2, body.get("thumbnails").size());
        assertEquals(50, body.at("/thumbnails/0/width").asInt());
        assertEquals(100, body.at("/thumbnails/1/width").asInt());

        HttpResponse<byte[]> thumb = get(path(body.at("/thumbnails/0")));
        assertEquals(200, thumb.statusCode());
        assertEquals("image/png", thumb.headers().firstValue("Content-Type").orElse(""));
        BufferedImage image = decodePng(thumb.body());
        assertEquals(50, image.getWidth());
        assertEquals(25, image.getHeight());
    }

    @Test
    void thumbnailsAreWrittenToTheDiskCache() throws Exception {
        JsonNode body = json.readTree(postImage(png(64, 64), "32").body());
        String id = body.get("id").asText();

        assertTrue(Files.isRegularFile(cacheDir.resolve(id).resolve("32.png")));
    }

    @Test
    void repeatRequestIsServedFromCacheWithoutRegenerating() throws Exception {
        byte[] image = png(64, 64);
        JsonNode first = json.readTree(postImage(image, "32").body());
        Path cached = cacheDir.resolve(first.get("id").asText()).resolve("32.png");

        // Replace the cached file with a sentinel; a regenerated thumbnail would overwrite it.
        Files.write(cached, new byte[] {1, 2, 3});
        HttpResponse<byte[]> again = postImage(image, "32");

        assertEquals(200, again.statusCode());
        assertArrayEquals(new byte[] {1, 2, 3}, get(path(json.readTree(again.body()).at("/thumbnails/0"))).body());
    }

    @Test
    void urlSourceIsFetchedAndThumbnailed() throws Exception {
        byte[] image = png(80, 40);
        HttpServer origin = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        origin.createContext("/cat.png", ex -> {
            ex.getResponseHeaders().set("Content-Type", "image/png");
            ex.sendResponseHeaders(200, image.length);
            try (OutputStream out = ex.getResponseBody()) {
                out.write(image);
            }
        });
        origin.start();
        try {
            String url = "http://127.0.0.1:" + origin.getAddress().getPort() + "/cat.png";
            HttpResponse<byte[]> resp = postJson(Map.of("url", url, "widths", List.of(20)));

            assertEquals(200, resp.statusCode());
            JsonNode thumb = json.readTree(resp.body()).at("/thumbnails/0");
            BufferedImage image20 = decodePng(get(path(thumb)).body());
            assertEquals(20, image20.getWidth());
            assertEquals(10, image20.getHeight());
        } finally {
            origin.stop(0);
        }
    }

    @Test
    void privateHostsAreRejectedWhenNotAllowed() throws Exception {
        HttpServer strict = new ThumbnailService(cacheDir.resolve("strict"), false)
                .start(new InetSocketAddress("127.0.0.1", 0));
        try {
            String strictBase = "http://127.0.0.1:" + strict.getAddress().getPort();
            HttpRequest req = HttpRequest.newBuilder(URI.create(strictBase + "/thumbnails"))
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofByteArray(json.writeValueAsBytes(
                            Map.of("url", "http://127.0.0.1/x.png", "widths", List.of(10)))))
                    .build();
            HttpResponse<byte[]> resp = client.send(req, HttpResponse.BodyHandlers.ofByteArray());

            assertEquals(400, resp.statusCode());
            assertTrue(new String(resp.body(), StandardCharsets.UTF_8).contains("not publicly routable"));
        } finally {
            strict.stop(0);
        }
    }

    @Test
    void rejectsInvalidWidths() throws Exception {
        byte[] image = png(10, 10);

        assertEquals(400, postImage(image, "").statusCode());
        assertEquals(400, postImage(image, "0").statusCode());
        assertEquals(400, postImage(image, "4097").statusCode());
        assertEquals(400, postImage(image, "abc").statusCode());
        assertEquals(400, postImage(image, "1,2,3,4,5,6,7,8,9,10,11").statusCode());
        assertEquals(400, postJson(Map.of("url", "http://example.com/a.png", "widths", List.of("10"))).statusCode());
    }

    @Test
    void rejectsNonImageAndBadUrls() throws Exception {
        assertEquals(400, post("image/png", "/thumbnails?widths=10", "not an image".getBytes(StandardCharsets.UTF_8)).statusCode());
        assertEquals(400, post("text/plain", "/thumbnails?widths=10", new byte[] {1}).statusCode());
        assertEquals(400, postJson(Map.of("url", "file:///etc/passwd", "widths", List.of(10))).statusCode());
        assertEquals(400, postJson(Map.of("widths", List.of(10))).statusCode());
    }

    @Test
    void unknownOrMalformedThumbnailPathsReturn404() throws Exception {
        assertEquals(404, get("/thumbnails/" + "0".repeat(64) + "/10.png").statusCode());
        assertEquals(404, get("/thumbnails/not-a-real-id/10.png").statusCode());
        assertEquals(404, get("/elsewhere").statusCode());
    }

    private static byte[] png(int width, int height) throws IOException {
        BufferedImage image = new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ImageIO.write(image, "png", out);
        return out.toByteArray();
    }

    private static BufferedImage decodePng(byte[] bytes) throws IOException {
        return ImageIO.read(new ByteArrayInputStream(bytes));
    }

    private static String path(JsonNode thumbnail) {
        return URI.create(thumbnail.get("url").asText()).getRawPath();
    }

    private HttpResponse<byte[]> postImage(byte[] image, String widths) throws Exception {
        return post("image/png", "/thumbnails?widths=" + widths, image);
    }

    private HttpResponse<byte[]> postJson(Map<String, Object> body) throws Exception {
        return post("application/json", "/thumbnails", json.writeValueAsBytes(body));
    }

    private HttpResponse<byte[]> post(String contentType, String pathAndQuery, byte[] body) throws Exception {
        HttpRequest req = HttpRequest.newBuilder(URI.create(base + pathAndQuery))
                .header("Content-Type", contentType)
                .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                .build();
        return client.send(req, HttpResponse.BodyHandlers.ofByteArray());
    }

    private HttpResponse<byte[]> get(String pathAndQuery) throws Exception {
        HttpRequest req = HttpRequest.newBuilder(URI.create(base + pathAndQuery)).GET().build();
        return client.send(req, HttpResponse.BodyHandlers.ofByteArray());
    }
}
