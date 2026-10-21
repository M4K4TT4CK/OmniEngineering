package planted;

import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

import org.springframework.web.client.RestTemplate;

/** Thin client for the inventory service. */
public class ApiClient {
    private final String base;
    private final HttpClient client = HttpClient.newHttpClient(); // expect: arbiter/java.http-client-no-timeout

    public ApiClient(String base) {
        this.base = base;
    }

    public String fetch(String path) throws IOException, InterruptedException {
        HttpRequest request = HttpRequest.newBuilder(URI.create(base + path)) // expect: arbiter/java.http-request-no-timeout
                .header("Accept", "application/json")
                .GET()
                .build();
        return client.send(request, HttpResponse.BodyHandlers.ofString()).body();
    }

    public HttpClient redirectingClient() {
        return HttpClient.newBuilder() // expect: arbiter/java.http-client-no-timeout
                .followRedirects(HttpClient.Redirect.NORMAL)
                .build();
    }

    public byte[] download(String url) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection(); // expect: arbiter/java.url-connection-no-timeout
        try (InputStream in = conn.getInputStream()) {
            return in.readAllBytes();
        }
    }

    public RestTemplate restTemplate() {
        return new RestTemplate(); // expect: arbiter/java.rest-template-no-timeout
    }
}
