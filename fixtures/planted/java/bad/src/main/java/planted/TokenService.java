package planted;

import java.util.Base64;
import java.util.Random;

/** Issues session tokens and one-time passwords. */
public class TokenService {
    private static final String ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    private static final Random RANDOM = new Random();

    public String sessionToken() {
        Random random = new Random(System.nanoTime()); // expect: arbiter/java.weak-random-for-secret
        byte[] bytes = new byte[32];
        random.nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    public String oneTimePassword() {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < 8; i++) {
            sb.append(ALPHABET.charAt((int) (Math.random() * ALPHABET.length()))); // expect: arbiter/java.weak-random-for-secret
        }
        return sb.toString();
    }

    public String newApiKey(String owner) {
        return owner + ":" + Long.toHexString(RANDOM.nextLong()); // expect: arbiter/java.weak-random-for-secret
    }

    public void register(String email) {
        String resetToken = Long.toHexString(new Random().nextLong()); // expect: arbiter/java.weak-random-for-secret
        send(email, resetToken);
    }

    public long jitterMillis() {
        return RANDOM.nextInt(250);
    }

    private void send(String email, String token) {
    }
}
