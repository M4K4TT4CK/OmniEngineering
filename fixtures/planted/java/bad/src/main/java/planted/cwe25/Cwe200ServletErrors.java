package planted.cwe25;

import java.io.IOException;
import java.sql.SQLException;

import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** CWE-200: a servlet prints the trace into the page, a controller returns the SQL error text. */
public class Cwe200ServletErrors extends HttpServlet {
    @Override
    protected void doGet(HttpServletRequest request, HttpServletResponse response) throws IOException {
        try {
            render(request, response);
        } catch (Exception e) {
            response.setStatus(500);
            e.printStackTrace(response.getWriter()); // expect: arbiter/java.stack-trace-to-client
        }
    }

    private void render(HttpServletRequest request, HttpServletResponse response) throws Exception {
        response.getWriter().write("ok");
    }

    @RestController
    static class Reports {
        private final ReportStore store = new ReportStore();

        @GetMapping("/reports")
        public ResponseEntity<String> list() {
            try {
                return ResponseEntity.ok(store.load());
            } catch (SQLException e) {
                return ResponseEntity.status(500).body("query failed: " + e.getMessage()); // expect: arbiter/java.exception-message-to-client
            }
        }
    }

    static class ReportStore {
        String load() throws SQLException {
            return "";
        }
    }
}
