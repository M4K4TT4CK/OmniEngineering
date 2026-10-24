package planted;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;

/** Reports over the audit table. The query is built first, then run inside `try`. */
public class ReportDao {
    private final Connection conn;
    private final String auditTable;

    public ReportDao(Connection conn, String auditTable) {
        this.conn = conn;
        this.auditTable = auditTable;
    }

    public int countFor(String user) throws SQLException {
        String sql = "SELECT COUNT(*) FROM audit WHERE actor = '" + user + "'";
        try {
            Statement st = conn.createStatement();
            ResultSet rs = st.executeQuery(sql); // expect: arbiter/java.sql-string-built
            return rs.next() ? rs.getInt(1) : 0;
        } finally {
            conn.commit();
        }
    }

    public void purge(String actor) throws SQLException {
        String sql = "DELETE FROM " + auditTable + " WHERE actor = '" + actor + "'";
        try (Statement st = conn.createStatement()) {
            st.executeUpdate(sql); // expect: arbiter/java.sql-string-built
        }
    }

    public PreparedStatement since(long ts) throws SQLException {
        return conn.prepareStatement("SELECT * FROM " + auditTable + " WHERE ts > " + ts); // expect: arbiter/java.sql-string-built
    }
}
