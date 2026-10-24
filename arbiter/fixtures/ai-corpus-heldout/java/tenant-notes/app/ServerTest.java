package tenantnotes;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.HashMap;
import java.util.Map;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class ServerTest {

    private static final String PASSWORD = "password123";
    private static final ObjectMapper JSON = new ObjectMapper();

    private Service service;
    private Server server;
    private HttpClient client;
    private String base;

    @BeforeEach
    void setUp() throws Exception {
        service = new Service("jdbc:sqlite::memory:");
        server = new Server(service, 0);
        server.start();
        client = HttpClient.newHttpClient();
        base = "http://localhost:" + server.port();
    }

    @AfterEach
    void tearDown() throws Exception {
        server.stop();
        service.close();
    }

    @Test
    void adminAndViewerFlowOverHttp() throws Exception {
        assertEquals(201, send("POST", "/orgs", null,
                json("organisation", "Acme", "email", "admin@acme.test", "password", PASSWORD)).statusCode());
        String admin = login("admin@acme.test");

        HttpResponse<String> created = send("POST", "/notes", admin, json("title", "Hello", "body", "World"));
        assertEquals(201, created.statusCode());
        long noteId = field(created, "id").asLong();

        HttpResponse<String> invited = send("POST", "/invitations", admin,
                json("email", "viewer@acme.test", "role", "viewer"));
        assertEquals(201, invited.statusCode());
        HttpResponse<String> accepted = send("POST", "/invitations/accept", null,
                json("token", field(invited, "invitationToken").asText(), "password", PASSWORD));
        assertEquals(200, accepted.statusCode());
        String viewer = field(accepted, "token").asText();

        assertEquals(200, send("GET", "/notes/" + noteId, viewer, null).statusCode());
        HttpResponse<String> search = send("GET", "/notes?q=hello", viewer, null);
        assertEquals(200, search.statusCode());
        assertTrue(search.body().contains("\"title\":\"Hello\""));
        assertEquals(403, send("POST", "/notes", viewer, json("title", "x")).statusCode());
        assertEquals(403, send("DELETE", "/notes/" + noteId, viewer, null).statusCode());
        assertEquals(401, send("GET", "/notes", null, null).statusCode());
        assertEquals(404, send("GET", "/notes/abc", admin, null).statusCode());
    }

    private String login(String email) throws Exception {
        HttpResponse<String> response = send("POST", "/login", null,
                json("email", email, "password", PASSWORD));
        assertEquals(200, response.statusCode());
        return field(response, "token").asText();
    }

    private JsonNode field(HttpResponse<String> response, String name) throws Exception {
        return JSON.readTree(response.body()).get(name);
    }

    private HttpResponse<String> send(String method, String path, String token, String body) throws Exception {
        HttpRequest.Builder request = HttpRequest.newBuilder(URI.create(base + path))
                .header("Content-Type", "application/json")
                .method(method, body == null
                        ? HttpRequest.BodyPublishers.noBody()
                        : HttpRequest.BodyPublishers.ofString(body));
        if (token != null) {
            request.header("Authorization", "Bearer " + token);
        }
        return client.send(request.build(), HttpResponse.BodyHandlers.ofString());
    }

    /** Builds a JSON object from alternating key and value arguments. */
    private static String json(String... keysAndValues) throws Exception {
        Map<String, String> fields = new HashMap<>();
        for (int i = 0; i < keysAndValues.length; i += 2) {
            fields.put(keysAndValues[i], keysAndValues[i + 1]);
        }
        return JSON.writeValueAsString(fields);
    }
}
