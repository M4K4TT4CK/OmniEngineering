package planted.cwe25;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;

/** CWE-287: jjwt is asked for the unsigned-token variant, so a token with `alg: none` is accepted. */
public class Cwe287JjwtUnsigned {
    public String currentUser(String token) {
        Claims claims = Jwts.parser().parseClaimsJwt(token).getBody(); // expect: arbiter/java.jwt-unsigned-accepted
        return claims.getSubject();
    }

    public String role(String token) {
        return Jwts.parser().unsecured().build() // expect: arbiter/java.jwt-unsigned-accepted
            .parseUnsecuredClaims(token).getPayload().get("role", String.class); // expect: arbiter/java.jwt-unsigned-accepted
    }
}
