package planted.cwe25;

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.URL;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

import javax.servlet.http.HttpServletRequest;

import okhttp3.OkHttpClient;
import okhttp3.Request;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.reactive.function.client.WebClient;

/** CWE-918: the server fetches whatever URL the caller names, metadata endpoints included. */
@RestController
public class Cwe918Ssrf {
    private final HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
    private final RestTemplate rest = new RestTemplate();
    private final OkHttpClient ok = new OkHttpClient();
    private final WebClient web = WebClient.create();

    @GetMapping("/preview")
    public String preview(@RequestParam String url) throws IOException, InterruptedException {
        HttpRequest req = HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(5)).build(); // expect: arbiter/java.ssrf-request-url
        return client.send(req, HttpResponse.BodyHandlers.ofString()).body();
    }

    @GetMapping("/proxy")
    public String proxy(@RequestParam("target") String target) {
        return rest.getForObject(target, String.class); // expect: arbiter/java.ssrf-request-url
    }

    @GetMapping("/webhook-test")
    public String webhookTest(@RequestParam String callback) throws IOException {
        Request r = new Request.Builder().url(callback).build(); // expect: arbiter/java.ssrf-request-url
        return ok.newCall(r).execute().body().string();
    }

    @GetMapping("/avatar-fetch")
    public String avatar(@RequestParam String host) {
        return web.get().uri("http://" + host + "/avatar.png").retrieve().bodyToMono(String.class).block(); // expect: arbiter/java.ssrf-request-url
    }

    public byte[] feed(HttpServletRequest request) throws IOException {
        String feedUrl = request.getParameter("feed");
        try (InputStream in = new URL(feedUrl).openStream()) { // expect: arbiter/java.ssrf-request-url
            return in.readAllBytes();
        }
    }
}
