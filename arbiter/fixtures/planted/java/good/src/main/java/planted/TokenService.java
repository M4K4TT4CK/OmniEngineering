package planted;

import java.security.SecureRandom;
import java.util.Base64;
import java.util.Collections;
import java.util.List;
import java.util.Random;

/** Issues session tokens and one-time passwords. */
public class TokenService {
    private static final String ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    private static final SecureRandom SECURE = new SecureRandom();
    private static final Random RANDOM = new Random();

    public String sessionToken() {
        byte[] bytes = new byte[32];
        SECURE.nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    public String oneTimePassword() {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < 8; i++) {
            sb.append(ALPHABET.charAt(SECURE.nextInt(ALPHABET.length())));
        }
        return sb.toString();
    }

    public String newApiKey(String owner) {
        return owner + ":" + Long.toHexString(SECURE.nextLong());
    }

    public void register(String email) {
        String resetToken = Long.toHexString(SECURE.nextLong());
        send(email, resetToken);
    }

    public long jitterMillis() {
        return RANDOM.nextInt(250);
    }

    public <T> List<T> shuffled(List<T> items) {
        Random random = new Random();
        Collections.shuffle(items, random);
        return items;
    }

    private void send(String email, String token) {
    }
}
