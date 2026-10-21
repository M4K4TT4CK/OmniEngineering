package planted.cwe25;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Set;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** CWE-918: only https URLs on allowlisted hosts, and redirects are not followed. */
@RestController
public class Cwe918Ssrf {
    private static final Set<String> ALLOWED_HOSTS = Set.of("github.com", "gitlab.com", "docs.oracle.com");
    private final HttpClient client = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(3))
        .followRedirects(HttpClient.Redirect.NEVER)
        .build();

    @GetMapping("/preview")
    public String preview(@RequestParam String url) throws IOException, InterruptedException {
        URI target = URI.create(url);
        if (!"https".equals(target.getScheme()) || !ALLOWED_HOSTS.contains(target.getHost())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST);
        }
        HttpRequest req = HttpRequest.newBuilder(target).timeout(Duration.ofSeconds(5)).build();
        return client.send(req, HttpResponse.BodyHandlers.ofString()).body();
    }
}
