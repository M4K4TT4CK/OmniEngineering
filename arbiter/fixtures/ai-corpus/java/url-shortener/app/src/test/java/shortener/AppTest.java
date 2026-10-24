package shortener;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Path;
import java.time.Instant;

import static org.junit.jupiter.api.Assertions.*;

class AppTest {
    private static final ObjectMapper JSON = new ObjectMapper();

    @TempDir Path tmp;
    private App app;
    private String base;
    private final HttpClient client = HttpClient.newBuilder()
            .followRedirects(HttpClient.Redirect.NEVER).build();

    @BeforeEach
    void setUp() throws Exception {
        app = new App(tmp.resolve("test.db"), 0, url -> "Title of " + url);
        app.start();
        base = "http://localhost:" + app.port();
    }

    @AfterEach
    void tearDown() { app.stop(); }

    private HttpResponse<String> post(String json) throws Exception {
        return client.send(HttpRequest.newBuilder(URI.create(base + "/api/links"))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(json)).build(),
                HttpResponse.BodyHandlers.ofString());
    }

    private HttpResponse<String> get(String path) throws Exception {
        return client.send(HttpRequest.newBuilder(URI.create(base + path)).GET().build(),
                HttpResponse.BodyHandlers.ofString());
    }

    @Test
    void createRedirectAndStats() throws Exception {
        HttpResponse<String> created = post("{\"url\":\"https://example.com/page\"}");
        assertEquals(201, created.statusCode());
        JsonNode c = JSON.readTree(created.body());
        String code = c.get("code").asText();
        assertEquals(7, code.length());
        assertEquals(base + "/" + code, c.get("shortUrl").asText());

        assertEquals(0, JSON.readTree(get("/api/links/" + code + "/stats").body()).get("clicks").asInt());

        for (int i = 0; i < 2; i++) {
            HttpResponse<String> r = get("/" + code);
            assertEquals(302, r.statusCode());
            assertEquals("https://example.com/page", r.headers().firstValue("Location").orElseThrow());
        }

        JsonNode stats = JSON.readTree(get("/api/links/" + code + "/stats").body());
        assertEquals(2, stats.get("clicks").asInt());
        assertEquals("https://example.com/page", stats.get("url").asText());
        assertEquals("Title of https://example.com/page", stats.get("title").asText());
        assertNotNull(Instant.parse(stats.get("createdAt").asText()));
    }

    @Test
    void customAlias() throws Exception {
        HttpResponse<String> r = post("{\"url\":\"https://example.com\",\"alias\":\"my-link\"}");
        assertEquals(201, r.statusCode());
        assertEquals("my-link", JSON.readTree(r.body()).get("code").asText());
        assertEquals(302, get("/my-link").statusCode());
    }

    @Test
    void duplicateAliasConflicts() throws Exception {
        assertEquals(201, post("{\"url\":\"https://a.example\",\"alias\":\"taken\"}").statusCode());
        assertEquals(409, post("{\"url\":\"https://b.example\",\"alias\":\"taken\"}").statusCode());
        // original is untouched
        assertEquals("https://a.example", get("/taken").headers().firstValue("Location").orElseThrow());
    }

    @Test
    void rejectsBadInput() throws Exception {
        assertEquals(400, post("not json").statusCode());
        assertEquals(400, post("{}").statusCode());
        assertEquals(400, post("{\"url\":\"ftp://example.com\"}").statusCode());
        assertEquals(400, post("{\"url\":\"javascript:alert(1)\"}").statusCode());
        assertEquals(400, post("{\"url\":\"https://example.com\",\"alias\":\"a b\"}").statusCode());
        assertEquals(400, post("{\"url\":\"https://example.com\",\"alias\":\"ab\"}").statusCode());
        assertEquals(400, post("{\"url\":\"https://example.com\",\"alias\":\"api\"}").statusCode());
    }

    @Test
    void unknownCodeIs404() throws Exception {
        assertEquals(404, get("/nope123").statusCode());
        assertEquals(404, get("/api/links/nope123/stats").statusCode());
    }

    @Test
    void wrongMethodIs405() throws Exception {
        assertEquals(405, get("/api/links").statusCode());
    }

    @Test
    void linksSurviveRestart() throws Exception {
        String code = JSON.readTree(post("{\"url\":\"https://example.com/x\"}").body()).get("code").asText();
        app.stop();
        app = new App(tmp.resolve("test.db"), 0, url -> null);
        app.start();
        base = "http://localhost:" + app.port();
        assertEquals("https://example.com/x", get("/" + code).headers().firstValue("Location").orElseThrow());
    }

    @Test
    void missingTitleIsNull() throws Exception {
        app.stop();
        app = new App(tmp.resolve("other.db"), 0, url -> null);
        app.start();
        base = "http://localhost:" + app.port();
        String code = JSON.readTree(post("{\"url\":\"https://example.com\"}").body()).get("code").asText();
        assertTrue(JSON.readTree(get("/api/links/" + code + "/stats").body()).get("title").isNull());
    }

    @Test
    void titleFetcherRefusesInternalAddresses() {
        App.TitleFetcher f = new App.TitleFetcher();
        assertNull(f.apply("http://127.0.0.1:" + app.port() + "/"));
        assertNull(f.apply("http://localhost/"));
        assertNull(f.apply("http://10.0.0.1/"));
        assertNull(f.apply("http://169.254.169.254/latest/meta-data/"));
        assertNull(f.apply("http://[::1]/"));
    }
}
