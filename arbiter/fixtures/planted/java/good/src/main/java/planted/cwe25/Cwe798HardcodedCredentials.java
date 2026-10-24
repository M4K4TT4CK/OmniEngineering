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

/** CWE-798: the database password and the token-signing secrets come from the environment. */
public class Cwe798HardcodedCredentials {
    public Connection connect() throws SQLException {
        return DriverManager.getConnection(System.getenv("JDBC_URL"), System.getenv("DB_USER"), System.getenv("DB_PASSWORD"));
    }

    public String issueToken(String userId) {
        return JWT.create().withSubject(userId).sign(Algorithm.HMAC256(System.getenv("JWT_SECRET")));
    }

    public String issueJjwt(String userId) {
        return Jwts.builder().subject(userId)
            .signWith(Keys.hmacShaKeyFor(System.getenv("JWT_SECRET").getBytes(StandardCharsets.UTF_8)))
            .compact();
    }

    public String issueLegacy(String userId) {
        return Jwts.builder().setSubject(userId).signWith(SignatureAlgorithm.HS256, System.getenv("JWT_SECRET")).compact();
    }

    public byte[] sign(byte[] payload) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(System.getenv("WEBHOOK_KEY").getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        return mac.doFinal(payload);
    }
}
