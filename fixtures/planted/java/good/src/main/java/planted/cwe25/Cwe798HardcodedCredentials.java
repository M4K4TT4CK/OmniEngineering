package planted.cwe25;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;

import com.auth0.jwt.JWT;
import com.auth0.jwt.algorithms.Algorithm;

/** CWE-798: the database password and the token-signing secret come from the environment. */
public class Cwe798HardcodedCredentials {
    public Connection connect() throws SQLException {
        return DriverManager.getConnection(System.getenv("JDBC_URL"), System.getenv("DB_USER"), System.getenv("DB_PASSWORD"));
    }

    public String issueToken(String userId) {
        return JWT.create().withSubject(userId).sign(Algorithm.HMAC256(System.getenv("JWT_SECRET")));
    }
}
