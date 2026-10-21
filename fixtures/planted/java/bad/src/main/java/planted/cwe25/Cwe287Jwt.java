package planted.cwe25;

import com.auth0.jwt.JWT;
import com.auth0.jwt.interfaces.DecodedJWT;

/** CWE-287: the bearer token is decoded and trusted; its signature is never checked. */
public class Cwe287Jwt {
    public String currentUser(String authorization) {
        String token = authorization.replaceFirst("^Bearer ", "");
        DecodedJWT jwt = JWT.decode(token); // expect-ext: semgrep/semgrep-rules.java.java-jwt.security.audit.java-jwt-decode-without-verify
        return jwt.getSubject();
    }
}
