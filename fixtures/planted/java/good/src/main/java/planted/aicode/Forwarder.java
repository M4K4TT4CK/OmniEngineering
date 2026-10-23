package planted.aicode;

import java.io.IOException;
import java.net.InetAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;

/** Webhook forwarding that refuses internal addresses (ARB-083). */
public final class Forwarder {
    private final HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();

    static boolean isPublic(InetAddress a) {
        return !(a.isLoopbackAddress() || a.isSiteLocalAddress() || a.isLinkLocalAddress()
                || a.isAnyLocalAddress() || a.isMulticastAddress());
    }

    public int deliver(Map<String, String> merchant, byte[] body) throws IOException, InterruptedException {
        URI uri = URI.create(merchant.get("callback_url"));
        for (InetAddress a : InetAddress.getAllByName(uri.getHost())) {
            if (!isPublic(a)) {
                return -1;
            }
        }
        HttpRequest req = HttpRequest.newBuilder(uri)
                .timeout(Duration.ofSeconds(10))
                .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                .build();
        return client.send(req, HttpResponse.BodyHandlers.discarding()).statusCode();
    }
}
