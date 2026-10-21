package planted;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;

/** Reports over the audit table. The query is built first, then run inside `try`. */
public class ReportDao {
    private final Connection conn;
    private final String auditTable;

    public ReportDao(Connection conn, String auditTable) {
        this.conn = conn;
        this.auditTable = auditTable;
    }

    public int countFor(String user) throws SQLException {
        String sql = "SELECT COUNT(*) FROM audit WHERE actor = ?";
        try {
            PreparedStatement st = conn.prepareStatement(sql);
            st.setString(1, user);
            ResultSet rs = st.executeQuery();
            return rs.next() ? rs.getInt(1) : 0;
        } finally {
            conn.commit();
        }
    }

    public void purge(String actor) throws SQLException {
        // A table name cannot be a `?`: it is spliced, the value is bound.
        String sql = "DELETE FROM " + auditTable + " WHERE actor = ?";
        try (PreparedStatement st = conn.prepareStatement(sql)) {
            st.setString(1, actor);
            st.executeUpdate();
        }
    }

    public PreparedStatement since(long ts) throws SQLException {
        PreparedStatement st = conn.prepareStatement("SELECT * FROM " + auditTable + " WHERE ts > ?");
        st.setLong(1, ts);
        return st;
    }

    /** Ant-style task method: `execute(String)` here is not JDBC. */
    public void report(String query) {
        execute("/../jmxproxy/?qry=" + query);
    }

    private void execute(String command) {
    }
}
