package planted.dataflow;

import java.io.IOException;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;

import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

/** The same shapes, safe: the request value never reaches the sink. */
public class DataflowServlet extends HttpServlet {
    private Connection conn;

    private static String byOwner(String owner) {
        return "SELECT id FROM notes WHERE owner = '" + owner + "'";
    }

    /** OWASP Benchmark's trick: a condition that is always true. */
    public void notes(HttpServletRequest request, HttpServletResponse response) throws SQLException {
        String owner = request.getParameter("owner");
        int num = 106;
        String bar = (7 * 18) + num > 200 ? "guest" : owner;
        String sql = "SELECT id FROM notes WHERE owner = '" + bar + "'";
        Statement st = conn.createStatement();
        st.executeQuery(sql);
        PreparedStatement ps = conn.prepareStatement("SELECT id FROM notes WHERE owner = ?");
        ps.setString(1, owner);
        ps.executeQuery();
    }

    /** The list's request value is removed before the read. */
    public void ping(HttpServletRequest request, HttpServletResponse response) throws IOException {
        String host = request.getParameter("host");
        List<String> parts = new ArrayList<String>();
        parts.add("localhost");
        parts.add(host);
        parts.add("127.0.0.1");
        parts.remove(0);
        String target = parts.get(1);
        Runtime r = Runtime.getRuntime();
        r.exec("ping -c1 " + target);
    }

    /** The exception is logged; the client gets a fixed message. */
    public void load(HttpServletRequest request, HttpServletResponse response) throws IOException {
        try {
            conn.createStatement().execute("SELECT 1");
        } catch (SQLException e) {
            log("load failed: " + e.getMessage());
            String detail = "load failed";
            response.sendError(500, detail);
        }
    }
}
