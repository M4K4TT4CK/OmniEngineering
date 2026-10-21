package planted.cwe25;

import com.auth0.jwt.JWT;
import com.auth0.jwt.JWTVerifier;
import com.auth0.jwt.algorithms.Algorithm;
import com.auth0.jwt.interfaces.DecodedJWT;

/** CWE-287: the bearer token's signature, issuer and expiry are verified before it is trusted. */
public class Cwe287Jwt {
    private final JWTVerifier verifier =
        JWT.require(Algorithm.HMAC256(System.getenv("JWT_SECRET"))).withIssuer("planted").build();

    public String currentUser(String authorization) {
        String token = authorization.replaceFirst("^Bearer ", "");
        DecodedJWT jwt = verifier.verify(token);
        return jwt.getSubject();
    }
}
