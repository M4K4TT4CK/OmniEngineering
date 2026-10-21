package planted;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;

import jakarta.persistence.EntityManager;

/** JDBC and JPA access to the users table. */
public class UserRepository {
    private static final String SELECT_USER = "SELECT id, name, email FROM users";

    private final Connection conn;
    private final EntityManager em;

    public UserRepository(Connection conn, EntityManager em) {
        this.conn = conn;
        this.em = em;
    }

    public User findByName(String name) throws SQLException {
        try (PreparedStatement stmt = conn.prepareStatement(SELECT_USER + " WHERE name = ?")) {
            stmt.setString(1, name);
            ResultSet rs = stmt.executeQuery();
            return rs.next() ? map(rs) : null;
        }
    }

    public int deleteSessionsBefore(java.sql.Timestamp cutoff) throws SQLException {
        String sql = "DELETE FROM sessions " + "WHERE created < ?";
        try (PreparedStatement stmt = conn.prepareStatement(sql)) {
            stmt.setTimestamp(1, cutoff);
            return stmt.executeUpdate();
        }
    }

    public User findByEmail(String email) throws SQLException {
        try (PreparedStatement stmt = conn.prepareStatement(SELECT_USER + " WHERE email = ?")) {
            stmt.setString(1, email);
            ResultSet rs = stmt.executeQuery();
            return rs.next() ? map(rs) : null;
        }
    }

    public List<User> search(String term) {
        return em.createQuery("SELECT u FROM User u WHERE u.name LIKE :term", User.class)
                .setParameter("term", "%" + term + "%")
                .getResultList();
    }

    public List<User> all() throws SQLException {
        List<User> out = new ArrayList<>();
        try (Statement stmt = conn.createStatement();
             ResultSet rs = stmt.executeQuery("SELECT id, name, email " + "FROM users " + "ORDER BY name")) {
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
