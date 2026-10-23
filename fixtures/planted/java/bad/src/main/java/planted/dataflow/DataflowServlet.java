package planted.dataflow;

import java.io.IOException;
import java.sql.Connection;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;

import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

/** One-hop data flow (Milestone 3): each defect is one step from its sink. */
public class DataflowServlet extends HttpServlet {
    private Connection conn;

    private static String byOwner(String owner) {
        return "SELECT id FROM notes WHERE owner = '" + owner + "'";
    }

    private String through(String value) {
        return value;
    }

    /** SQL built in a helper, run here. */
    public void notes(HttpServletRequest request, HttpServletResponse response) throws SQLException {
        String owner = request.getParameter("owner");
        Statement st = conn.createStatement();
        st.executeQuery(byOwner(owner)); // expect: arbiter/java.sql-string-built
    }

    /** A list keeps the request value at the index that is read back. */
    public void ping(HttpServletRequest request, HttpServletResponse response) throws IOException {
        String host = request.getParameter("host");
        List<String> parts = new ArrayList<String>();
        parts.add("safe");
        parts.add(host);
        parts.remove(0);
        String target = parts.get(0);
        Runtime r = Runtime.getRuntime();
        r.exec("ping -c1 " + target); // expect: arbiter/java.command-injection
    }

    /** Exception text through a local and a helper. */
    public void load(HttpServletRequest request, HttpServletResponse response) throws IOException {
        try {
            conn.createStatement().execute("SELECT 1");
        } catch (SQLException e) {
            String detail = through("load failed: " + e.getMessage());
            response.sendError(500, detail); // expect: arbiter/java.exception-message-to-client
        }
    }
}
