package oauthlogin;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import io.javalin.Javalin;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AppTest {
    private HttpServer github;
    private Javalin server;
    private String githubBase;
    private String appBase;
    private volatile String profileName;

    private final HttpClient client = HttpClient.newBuilder()
            .followRedirects(HttpClient.Redirect.NEVER)
            .build();

    @BeforeEach
    void start() throws Exception {
        profileName = "Octo Cat";

        // Fake GitHub: accepts only code=good-code and only the token tok-123.
        github = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
        github.createContext("/login/oauth/access_token", ex -> {
            String body = new String(ex.getRequestBody().readAllBytes(), UTF_8);
            if (body.contains("code=good-code")) {
                respond(ex, 200, "{\"access_token\":\"tok-123\"}");
            } else {
                respond(ex, 200, "{\"error\":\"bad_verification_code\"}");
            }
        });
        github.createContext("/user", ex -> {
            if (!"Bearer tok-123".equals(ex.getRequestHeaders().getFirst("Authorization"))) {
                respond(ex, 401, "{}");
                return;
            }
            respond(ex, 200, "{\"id\":42,\"login\":\"octocat\",\"name\":\"" + profileName
                    + "\",\"email\":null,\"avatar_url\":\"https://example.com/a.png\"}");
        });
        github.start();
        githubBase = "http://localhost:" + github.getAddress().getPort();

        App app = new App(new App.Config("cid", "secret", "http://localhost/callback",
                githubBase + "/login/oauth/authorize", githubBase + "/login/oauth/access_token",
                githubBase, "jdbc:sqlite::memory:", false));
        server = app.javalin().start(0);
        appBase = "http://localhost:" + server.port();
    }

    @AfterEach
    void stop() {
        server.stop();
        github.stop(0);
    }

    @Test
    void loginRedirectsToGitHubWithStateCookie() throws Exception {
        HttpResponse<String> res = get("/login?next=/profile", null);

        assertEquals(302, res.statusCode());
        String location = res.headers().firstValue("location").orElseThrow();
        assertTrue(location.startsWith(githubBase + "/login/oauth/authorize?"));
        assertTrue(location.contains("client_id=cid"));
        assertTrue(location.contains("scope=read%3Auser"));
        assertNotNull(cookie(res, "oauth_state"));
    }

    @Test
    void callbackRejectsStateMismatch() throws Exception {
        HttpResponse<String> res = get("/callback?code=good-code&state=forged", "oauth_state=real");

        assertEquals(400, res.statusCode());
    }

    @Test
    void callbackRejectsFailedTokenExchange() throws Exception {
        HttpResponse<String> start = get("/login", null);
        String state = cookie(start, "oauth_state");

        HttpResponse<String> res = get("/callback?code=bad-code&state=" + state, "oauth_state=" + state);

        assertEquals(502, res.statusCode());
    }

    @Test
    void fullLoginShowsProfileAndRedirectsBack() throws Exception {
        String sid = loginAs("/projects?tab=2");

        HttpResponse<String> res = get("/profile", "sid=" + sid);

        assertEquals(200, res.statusCode());
        assertTrue(res.body().contains("octocat"));
        assertTrue(res.body().contains("Octo Cat"));
    }

    @Test
    void profileRequiresSession() throws Exception {
        HttpResponse<String> res = get("/profile", null);

        assertEquals(302, res.statusCode());
        assertTrue(res.headers().firstValue("location").orElseThrow().startsWith("/login?next="));
    }

    @Test
    void repeatLoginUpdatesExistingUser() throws Exception {
        loginAs("/profile");
        profileName = "Renamed Cat";

        String sid = loginAs("/profile");

        HttpResponse<String> res = get("/profile", "sid=" + sid);
        assertTrue(res.body().contains("Renamed Cat"));
        assertTrue(!res.body().contains("Octo Cat"));
    }

    @Test
    void logoutEndsSession() throws Exception {
        String sid = loginAs("/profile");

        HttpResponse<String> logout = post("/logout", "sid=" + sid);
        assertEquals(302, logout.statusCode());
        assertEquals("/", logout.headers().firstValue("location").orElseThrow());

        HttpResponse<String> after = get("/profile", "sid=" + sid);
        assertEquals(302, after.statusCode());
        assertTrue(after.headers().firstValue("location").orElseThrow().startsWith("/login"));
    }

    @Test
    void profileEscapesHtml() throws Exception {
        profileName = "<script>alert(1)</script>";
        String sid = loginAs("/profile");

        String body = get("/profile", "sid=" + sid).body();

        assertTrue(body.contains("&lt;script&gt;alert(1)&lt;/script&gt;"));
        assertTrue(!body.contains("<script>"));
    }

    @Test
    void safeNextRejectsOffSiteTargets() {
        assertEquals("/x?y=1", App.safeNext("/x?y=1"));
        assertEquals("/profile", App.safeNext("//evil.com"));
        assertEquals("/profile", App.safeNext("https://evil.com"));
        assertEquals("/profile", App.safeNext("/\\evil.com"));
        assertEquals("/profile", App.safeNext(null));
    }

    /** Runs the login and callback round trip and returns the session id. */
    private String loginAs(String next) throws Exception {
        HttpResponse<String> start = get("/login?next=" + URLEncoder.encode(next, UTF_8), null);
        String state = cookie(start, "oauth_state");
        String nextCookie = cookie(start, "oauth_next");

        HttpResponse<String> cb = get("/callback?code=good-code&state=" + state,
                "oauth_state=" + state + "; oauth_next=" + nextCookie);

        assertEquals(302, cb.statusCode());
        assertEquals(next, cb.headers().firstValue("location").orElseThrow());
        return cookie(cb, "sid");
    }

    private HttpResponse<String> get(String path, String cookies) throws Exception {
        return send(HttpRequest.newBuilder(URI.create(appBase + path)).GET(), cookies);
    }

    private HttpResponse<String> post(String path, String cookies) throws Exception {
        return send(HttpRequest.newBuilder(URI.create(appBase + path))
                .POST(HttpRequest.BodyPublishers.noBody()), cookies);
    }

    private HttpResponse<String> send(HttpRequest.Builder builder, String cookies) throws Exception {
        if (cookies != null) {
            builder.header("Cookie", cookies);
        }
        return client.send(builder.build(), HttpResponse.BodyHandlers.ofString());
    }

    private static String cookie(HttpResponse<?> res, String name) {
        for (String header : res.headers().allValues("set-cookie")) {
            if (header.startsWith(name + "=")) {
                return header.substring(name.length() + 1).split(";", 2)[0];
            }
        }
        return null;
    }

    private static void respond(HttpExchange ex, int status, String json) throws IOException {
        byte[] bytes = json.getBytes(UTF_8);
        ex.getResponseHeaders().set("Content-Type", "application/json");
        ex.sendResponseHeaders(status, bytes.length);
        try (OutputStream out = ex.getResponseBody()) {
            out.write(bytes);
        }
    }
}
