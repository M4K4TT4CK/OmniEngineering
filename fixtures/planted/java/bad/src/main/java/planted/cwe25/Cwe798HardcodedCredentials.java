package planted.cwe25;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;

import com.auth0.jwt.JWT;
import com.auth0.jwt.algorithms.Algorithm;

/** CWE-798: the database password and the token-signing secret are compiled in. */
public class Cwe798HardcodedCredentials {
    public Connection connect() throws SQLException {
        return DriverManager.getConnection("jdbc:postgresql://db.internal:5432/app", "app_user", "Tr1cky-Pr0d-Pw"); // gap: CWE-798 - nothing in this build reports it (docs/cwe-top25.md)
    }

    public String issueToken(String userId) {
        return JWT.create().withSubject(userId).sign(Algorithm.HMAC256("s3cr3t-signing-key-do-not-share")); // gap: CWE-798 - nothing in this build reports it (docs/cwe-top25.md)
    }
}
