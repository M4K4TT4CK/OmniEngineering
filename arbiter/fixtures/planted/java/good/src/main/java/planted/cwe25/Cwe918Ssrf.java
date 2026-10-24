package planted.cwe25;

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.URL;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Set;

import javax.servlet.http.HttpServletRequest;

import okhttp3.OkHttpClient;
import okhttp3.Request;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.server.ResponseStatusException;

/** CWE-918: only https URLs on allowlisted hosts, and redirects are not followed. */
@RestController
public class Cwe918Ssrf {
    private static final Set<String> ALLOWED_HOSTS = Set.of("github.com", "gitlab.com", "docs.oracle.com");
    private static final String AVATARS = "https://avatars.example.com/";
    private final HttpClient client = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(3))
        .followRedirects(HttpClient.Redirect.NEVER)
        .build();
    private final RestTemplate rest = new RestTemplate();
    private final OkHttpClient ok = new OkHttpClient();
    private final WebClient web = WebClient.create();

    @GetMapping("/preview")
    public String preview(@RequestParam String url) throws IOException, InterruptedException {
        URI target = URI.create(url);
        if (!"https".equals(target.getScheme()) || !ALLOWED_HOSTS.contains(target.getHost())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST);
        }
        HttpRequest req = HttpRequest.newBuilder(target).timeout(Duration.ofSeconds(5)).build();
        return client.send(req, HttpResponse.BodyHandlers.ofString()).body();
    }

    @GetMapping("/proxy")
    public String proxy(@RequestParam("target") String target) {
        if (!ALLOWED_HOSTS.contains(URI.create(target).getHost())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST);
        }
        return rest.getForObject(target, String.class);
    }

    @GetMapping("/webhook-test")
    public String webhookTest(@RequestParam String callback) throws IOException {
        Request r = new Request.Builder().url("https://hooks.example.com/test?callback=" + callback).build();
        return ok.newCall(r).execute().body().string();
    }

    @GetMapping("/avatar-fetch")
    public String avatar(@RequestParam String user) {
        return web.get().uri(AVATARS + user + ".png").retrieve().bodyToMono(String.class).block();
    }

    public byte[] feed(HttpServletRequest request) throws IOException {
        String feedName = request.getParameter("feed");
        if (!feedName.matches("[a-z0-9-]{1,40}")) {
            throw new IllegalArgumentException("bad feed name");
        }
        try (InputStream in = new URL("https://feeds.example.com/" + feedName + ".xml").openStream()) {
            return in.readAllBytes();
        }
    }
}
