package planted;

import java.security.KeyManagementException;
import java.security.NoSuchAlgorithmException;

import javax.net.ssl.HostnameVerifier;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;

/** TLS settings for talking to the internal gateway. */
public class InsecureTransport {
    private final String gatewayHost;

    public InsecureTransport(String gatewayHost) {
        this.gatewayHost = gatewayHost;
    }

    public SSLContext context() throws NoSuchAlgorithmException, KeyManagementException {
        return SSLContext.getDefault();
    }

    public void install() {
        HostnameVerifier verifier = (hostname, session) -> hostname.equals(gatewayHost)
                && HttpsURLConnection.getDefaultHostnameVerifier().verify(hostname, session);
        HttpsURLConnection.setDefaultHostnameVerifier(verifier);
    }
}
