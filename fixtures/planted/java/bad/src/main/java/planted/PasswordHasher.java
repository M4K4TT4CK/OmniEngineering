package planted;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/** Hashes passwords before they are stored. */
public class PasswordHasher {
    public String hash(String password) throws NoSuchAlgorithmException {
        MessageDigest md = MessageDigest.getInstance("MD5"); // expect-ext: semgrep/semgrep-rules.java.lang.security.audit.crypto.use-of-md5
        return HexFormat.of().formatHex(md.digest(password.getBytes(StandardCharsets.UTF_8)));
    }
}
