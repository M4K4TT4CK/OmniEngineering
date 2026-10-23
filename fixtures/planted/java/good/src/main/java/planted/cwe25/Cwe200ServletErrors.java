package planted.cwe25;

import java.io.IOException;
import java.sql.SQLException;
import java.util.logging.Level;
import java.util.logging.Logger;

import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** CWE-200: the trace and the SQL error are logged; the caller gets a fixed message, or a domain message meant for it. */
public class Cwe200ServletErrors extends HttpServlet {
    private static final Logger LOG = Logger.getLogger(Cwe200ServletErrors.class.getName());

    @Override
    protected void doGet(HttpServletRequest request, HttpServletResponse response) throws IOException {
        try {
            render(request, response);
        } catch (Exception e) {
            LOG.log(Level.SEVERE, "render failed", e);
            response.sendError(500, "internal error");
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
                LOG.log(Level.SEVERE, "report query failed", e);
                return ResponseEntity.status(500).body("query failed");
            }
        }

        @ExceptionHandler(ReportNotFoundException.class)
        public ResponseEntity<String> notFound(ReportNotFoundException ex) {
            return ResponseEntity.status(404).body(ex.getMessage());
        }
    }

    static class ReportNotFoundException extends RuntimeException {
        ReportNotFoundException(String id) {
            super("no report " + id);
        }
    }

    static class ReportStore {
        String load() throws SQLException {
            return "";
        }
    }
}
