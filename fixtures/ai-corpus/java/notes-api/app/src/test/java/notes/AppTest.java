package notes;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.javalin.Javalin;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

class AppTest {

    static final ObjectMapper JSON = new ObjectMapper();
    static final HttpClient HTTP = HttpClient.newHttpClient();
    static Javalin app;
    static String base;

    @BeforeAll
    static void start() throws Exception {
        app = App.create("jdbc:sqlite::memory:", 1000).start(0);
        base = "http://localhost:" + app.port();
    }

    @AfterAll
    static void stop() {
        app.stop();
    }

    record Res(int status, JsonNode json, HttpResponse<String> raw) {}

    static Res call(String method, String path, String token, String body) throws Exception {
        return call(base, method, path, token, body);
    }

    static Res call(String baseUrl, String method, String path, String token, String body) throws Exception {
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(baseUrl + path))
                .header("Content-Type", "application/json")
                .method(method, body == null ? HttpRequest.BodyPublishers.noBody()
                        : HttpRequest.BodyPublishers.ofString(body));
        if (token != null) b.header("Authorization", "Bearer " + token);
        HttpResponse<String> r = HTTP.send(b.build(), HttpResponse.BodyHandlers.ofString());
        JsonNode node = r.body().isEmpty() ? null : JSON.readTree(r.body());
        return new Res(r.statusCode(), node, r);
    }

    static String creds(String user, String pass) {
        return "{\"username\":\"" + user + "\",\"password\":\"" + pass + "\"}";
    }

    static String note(String title, String body) {
        return "{\"title\":\"" + title + "\",\"body\":\"" + body + "\"}";
    }

    /** Signs up and logs in, returning a token. */
    static String register(String baseUrl, String user) throws Exception {
        assertEquals(201, call(baseUrl, "POST", "/signup", null, creds(user, "password123")).status());
        Res login = call(baseUrl, "POST", "/login", null, creds(user, "password123"));
        assertEquals(200, login.status());
        return login.json().get("token").asText();
    }

    @Test
    void signupValidationAndDuplicates() throws Exception {
        assertEquals(201, call("POST", "/signup", null, creds("alice", "password123")).status());
        assertEquals(409, call("POST", "/signup", null, creds("ALICE", "password123")).status());
        assertEquals(400, call("POST", "/signup", null, creds("bob", "short")).status());
        assertEquals(400, call("POST", "/signup", null, creds("b!", "password123")).status());
        assertEquals(400, call("POST", "/signup", null, "{}").status());
        assertEquals(400, call("POST", "/signup", null, "not json").status());
    }

    @Test
    void loginRejectsBadCredentials() throws Exception {
        call("POST", "/signup", null, creds("carol", "password123"));
        assertEquals(401, call("POST", "/login", null, creds("carol", "wrong-password")).status());
        assertEquals(401, call("POST", "/login", null, creds("nobody", "password123")).status());
    }

    @Test
    void notesRequireValidToken() throws Exception {
        assertEquals(401, call("GET", "/notes", null, null).status());
        assertEquals(401, call("GET", "/notes", "bogus", null).status());
    }

    @Test
    void noteCrud() throws Exception {
        String token = register(base, "dave");

        Res created = call("POST", "/notes", token, note("Groceries", "milk, eggs"));
        assertEquals(201, created.status());
        long id = created.json().get("id").asLong();
        assertEquals("Groceries", created.json().get("title").asText());

        assertEquals("milk, eggs", call("GET", "/notes/" + id, token, null).json().get("body").asText());

        Res updated = call("PUT", "/notes/" + id, token, note("Groceries v2", "bread"));
        assertEquals(200, updated.status());
        assertEquals("bread", updated.json().get("body").asText());

        Res list = call("GET", "/notes", token, null);
        assertEquals(1, list.json().size());
        assertEquals("Groceries v2", list.json().get(0).get("title").asText());

        assertEquals(204, call("DELETE", "/notes/" + id, token, null).status());
        assertEquals(404, call("GET", "/notes/" + id, token, null).status());
        assertEquals(404, call("DELETE", "/notes/" + id, token, null).status());
    }

    @Test
    void noteValidation() throws Exception {
        String token = register(base, "erin");
        assertEquals(400, call("POST", "/notes", token, "{\"body\":\"no title\"}").status());
        assertEquals(400, call("POST", "/notes", token, note("   ", "x")).status());
        assertEquals(404, call("GET", "/notes/abc", token, null).status());
        assertEquals(400, call("GET", "/notes?limit=0", token, null).status());
    }

    @Test
    void search() throws Exception {
        String token = register(base, "frank");
        call("POST", "/notes", token, note("Meeting", "discuss roadmap"));
        call("POST", "/notes", token, note("Recipe", "100% whole wheat"));
        call("POST", "/notes", token, note("Roadmap ideas", "later"));

        assertEquals(2, call("GET", "/notes?q=roadmap", token, null).json().size());
        assertEquals(1, call("GET", "/notes?q=recipe", token, null).json().size());
        // LIKE wildcards in the query are treated literally
        assertEquals(1, call("GET", "/notes?q=100%25", token, null).json().size());
        assertEquals(0, call("GET", "/notes?q=%25%25", token, null).json().size());
        assertEquals(3, call("GET", "/notes", token, null).json().size());
    }

    @Test
    void usersCannotSeeOrModifyEachOthersNotes() throws Exception {
        String a = register(base, "grace");
        String b = register(base, "heidi");
        long id = call("POST", "/notes", a, note("secret", "mine")).json().get("id").asLong();

        assertEquals(404, call("GET", "/notes/" + id, b, null).status());
        assertEquals(404, call("PUT", "/notes/" + id, b, note("hacked", "x")).status());
        assertEquals(404, call("DELETE", "/notes/" + id, b, null).status());
        assertEquals(0, call("GET", "/notes?q=secret", b, null).json().size());

        assertEquals("secret", call("GET", "/notes/" + id, a, null).json().get("title").asText());
    }

    @Test
    void rateLimitIs60PerMinutePerUser() throws Exception {
        Javalin limited = App.create("jdbc:sqlite::memory:", App.RATE_LIMIT_PER_MINUTE).start(0);
        try {
            String url = "http://localhost:" + limited.port();
            String t1 = register(url, "ivan");
            String t2 = register(url, "judy");

            // ivan has not used any requests on his token yet; the 60 below are all allowed
            for (int i = 0; i < 60; i++) {
                assertEquals(200, call(url, "GET", "/notes", t1, null).status(), "request " + (i + 1));
            }
            Res blocked = call(url, "GET", "/notes", t1, null);
            assertEquals(429, blocked.status());
            assertNotNull(blocked.raw().headers().firstValue("Retry-After").orElse(null));
            assertTrue(Integer.parseInt(blocked.raw().headers().firstValue("Retry-After").get()) <= 60);

            // another user is unaffected
            assertEquals(200, call(url, "GET", "/notes", t2, null).status());
        } finally {
            limited.stop();
        }
    }
}
