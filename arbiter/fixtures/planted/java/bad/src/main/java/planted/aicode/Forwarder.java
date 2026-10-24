package planted.aicode;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;

/** Webhook forwarding as model-written receivers ship it (ARB-083). */
public final class Forwarder {
    private final HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();

    public int deliver(Map<String, String> merchant, byte[] body) throws IOException, InterruptedException {
        HttpRequest req = HttpRequest.newBuilder(URI.create(merchant.get("callback_url")))
                .timeout(Duration.ofSeconds(10))
                .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                .build();
        return client.send(req, HttpResponse.BodyHandlers.discarding()).statusCode(); // expect: arbiter/java.ssrf-stored-callback
    }
}
