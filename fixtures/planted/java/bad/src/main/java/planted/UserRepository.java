package planted;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;

import jakarta.persistence.EntityManager;

/** JDBC and JPA access to the users table. */
public class UserRepository {
    private final Connection conn;
    private final EntityManager em;

    public UserRepository(Connection conn, EntityManager em) {
        this.conn = conn;
        this.em = em;
    }

    public User findByName(String name) throws SQLException {
        Statement stmt = conn.createStatement();
        ResultSet rs = stmt.executeQuery("SELECT id, name, email FROM users WHERE name = '" + name + "'"); // expect: arbiter/java.sql-string-built
        return rs.next() ? map(rs) : null;
    }

    public int deleteSessionsBefore(String date) throws SQLException {
        String sql = "DELETE FROM sessions WHERE created < '" + date + "'";
        return conn.createStatement().executeUpdate(sql); // expect: arbiter/java.sql-string-built
    }

    public User findByEmail(String email) throws SQLException {
        Statement stmt = conn.createStatement();
        ResultSet rs = stmt.executeQuery(String.format("SELECT id, name, email FROM users WHERE email = '%s'", email)); // expect: arbiter/java.sql-string-built
        return rs.next() ? map(rs) : null;
    }

    public List<User> search(String term) {
        return em.createQuery("SELECT u FROM User u WHERE u.name LIKE '%" + term + "%'", User.class) // expect: arbiter/java.sql-string-built
                .getResultList();
    }

    public List<User> inOrg(String org) throws SQLException {
        String sql = "SELECT id, name, email FROM users WHERE org = '" + org + "' ORDER BY name";
        List<User> out = new ArrayList<>();
        try (ResultSet rs = conn.createStatement().executeQuery(sql)) { // expect: arbiter/java.sql-string-built
            while (rs.next()) {
                out.add(map(rs));
            }
        }
        return out;
    }

    private static User map(ResultSet rs) throws SQLException {
        return new User(rs.getLong("id"), rs.getString("name"), rs.getString("email"));
    }

    public record User(long id, String name, String email) {}
}
