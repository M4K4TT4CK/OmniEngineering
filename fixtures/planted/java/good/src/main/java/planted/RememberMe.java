package planted;

import java.security.SecureRandom;
import java.util.Base64;

/** Remember-me cookies: the value is drawn first, then turned into the key. */
public class RememberMe {
    private static final SecureRandom SECURE = new SecureRandom();

    public String issue() {
        double value = SECURE.nextDouble();
        String rememberMeKey = Double.toString(value).substring(2);
        return rememberMeKey;
    }

    public String issueFromMath() {
        // A jitter is not a secret, whatever it is called nearby.
        double jitter = java.lang.Math.random();
        String rememberMeKey = Long.toHexString(SECURE.nextLong());
        return rememberMeKey + jitter;
    }

    public String issueBytes() {
        byte[] bytes = new byte[16];
        SECURE.nextBytes(bytes);
        String rememberMeKey = Base64.getEncoder().encodeToString(bytes);
        return rememberMeKey;
    }

    public long backoffMillis() {
        return (long) (new java.util.Random().nextDouble() * 250);
    }
}
