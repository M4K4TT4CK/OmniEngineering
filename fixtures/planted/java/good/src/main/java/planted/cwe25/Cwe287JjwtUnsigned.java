package planted.cwe25;

import javax.crypto.SecretKey;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;

/** CWE-287: jjwt verifies the signature with the server's key before the claims are read. */
public class Cwe287JjwtUnsigned {
    private final SecretKey key;

    public Cwe287JjwtUnsigned(SecretKey key) {
        this.key = key;
    }

    public String currentUser(String token) {
        Claims claims = Jwts.parser().setSigningKey(key).parseClaimsJws(token).getBody();
        return claims.getSubject();
    }

    public String role(String token) {
        return Jwts.parser().verifyWith(key).build()
            .parseSignedClaims(token).getPayload().get("role", String.class);
    }
}
