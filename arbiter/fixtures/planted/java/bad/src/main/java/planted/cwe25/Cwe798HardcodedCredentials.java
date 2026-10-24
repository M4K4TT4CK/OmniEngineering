package planted.cwe25;

import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

import com.auth0.jwt.JWT;
import com.auth0.jwt.algorithms.Algorithm;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.SignatureAlgorithm;
import io.jsonwebtoken.security.Keys;

/** CWE-798: the database password and the token-signing secrets are compiled in. */
public class Cwe798HardcodedCredentials {
    public Connection connect() throws SQLException {
        return DriverManager.getConnection("jdbc:postgresql://db.internal:5432/app", "app_user", "Tr1cky-Pr0d-Pw"); // expect: arbiter/java.hardcoded-db-password
    }

    public String issueToken(String userId) {
        return JWT.create().withSubject(userId).sign(Algorithm.HMAC256("s3cr3t-signing-key-do-not-share")); // expect: arbiter/java.hardcoded-signing-key
    }

    public String issueJjwt(String userId) {
        return Jwts.builder().subject(userId)
            .signWith(Keys.hmacShaKeyFor("a-very-long-hmac-key-of-at-least-32-bytes!".getBytes(StandardCharsets.UTF_8))) // expect: arbiter/java.hardcoded-signing-key
            .compact();
    }

    public String issueLegacy(String userId) {
        return Jwts.builder().setSubject(userId).signWith(SignatureAlgorithm.HS256, "legacySecret42").compact(); // expect: arbiter/java.hardcoded-signing-key
    }

    public byte[] sign(byte[] payload) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec("webhook-signing-k3y".getBytes(StandardCharsets.UTF_8), "HmacSHA256")); // expect: arbiter/java.hardcoded-signing-key
        return mac.doFinal(payload);
    }
}
