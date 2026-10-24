package planted.cwe25;

import java.io.PrintWriter;
import java.io.StringWriter;
import java.util.Map;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/** CWE-200: the global handler returns the exception text and full stack trace to the caller. */
@RestControllerAdvice
public class Cwe200ErrorLeak {
    @ExceptionHandler(Exception.class)
    public ResponseEntity<Map<String, String>> handle(Exception ex) {
        StringWriter trace = new StringWriter();
        ex.printStackTrace(new PrintWriter(trace));
        return ResponseEntity.status(500).body(Map.of("error", ex.toString(), "trace", trace.toString())); // expect: arbiter/java.stack-trace-to-client
    }
}
