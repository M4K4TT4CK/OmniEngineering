package planted.cwe25;

import java.util.Map;
import java.util.UUID;
import java.util.logging.Level;
import java.util.logging.Logger;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/** CWE-200: the global handler logs the detail and returns an opaque incident id. */
@RestControllerAdvice
public class Cwe200ErrorLeak {
    private static final Logger LOG = Logger.getLogger(Cwe200ErrorLeak.class.getName());

    @ExceptionHandler(Exception.class)
    public ResponseEntity<Map<String, String>> handle(Exception ex) {
        String incident = UUID.randomUUID().toString();
        LOG.log(Level.SEVERE, "unhandled error " + incident, ex);
        return ResponseEntity.status(500).body(Map.of("error", "internal error", "incident", incident));
    }
}
