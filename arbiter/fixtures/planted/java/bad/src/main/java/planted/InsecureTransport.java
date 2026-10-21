package planted;

import java.security.cert.X509Certificate;

import javax.net.ssl.HostnameVerifier;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.X509TrustManager;

/** TLS settings for talking to the internal gateway. */
public class InsecureTransport {
    static final class TrustAll implements X509TrustManager {
        @Override
        public void checkClientTrusted(X509Certificate[] chain, String authType) {
            // clients are not verified by this side
        }

        @Override
        public void checkServerTrusted(X509Certificate[] chain, String authType) { // expect: arbiter/java.trust-all-certificates
        }

        @Override
        public X509Certificate[] getAcceptedIssuers() {
            return new X509Certificate[0];
        }
    }

    public void install() {
        HostnameVerifier verifier = (hostname, session) -> true; // expect: arbiter/java.hostname-verifier-disabled
        HttpsURLConnection.setDefaultHostnameVerifier(verifier);
    }

    public void installInline() {
        HttpsURLConnection.setDefaultHostnameVerifier((h, s) -> true); // expect: arbiter/java.hostname-verifier-disabled
    }
}
