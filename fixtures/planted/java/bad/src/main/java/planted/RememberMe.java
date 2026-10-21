package planted;

import java.util.Base64;

/** Remember-me cookies: the value is drawn first, then turned into the key. */
public class RememberMe {
    public String issue() {
        double value = new java.util.Random().nextDouble(); // expect: arbiter/java.weak-random-for-secret
        String rememberMeKey = Double.toString(value).substring(2);
        return rememberMeKey;
    }

    public String issueFromMath() {
        double value = java.lang.Math.random(); // expect: arbiter/java.weak-random-for-secret
        String rememberMeKey = Double.toString(value).substring(2);
        return rememberMeKey;
    }

    public String issueBytes() {
        byte[] bytes = new byte[16];
        new java.util.Random(System.nanoTime()).nextBytes(bytes); // expect: arbiter/java.weak-random-for-secret
        String rememberMeKey = Base64.getEncoder().encodeToString(bytes);
        return rememberMeKey;
    }
}
