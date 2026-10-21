package planted.cwe25;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** CWE-918: the server fetches whatever URL the caller names, metadata endpoints included. */
@RestController
public class Cwe918Ssrf {
    private final HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();

    @GetMapping("/preview")
    public String preview(@RequestParam String url) throws IOException, InterruptedException {
        HttpRequest req = HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(5)).build(); // gap: CWE-918 - nothing in this build reports it (docs/cwe-top25.md)
        return client.send(req, HttpResponse.BodyHandlers.ofString()).body();
    }
}
