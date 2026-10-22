package planted.cwe25;

import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** CWE-79: the search term is concatenated into an HTML response. */
@RestController
public class Cwe079Xss {
    @GetMapping(value = "/search", produces = MediaType.TEXT_HTML_VALUE)
    public String search(@RequestParam String q) {
        return "<h1>Results for " + q + "</h1><p>No matches.</p>"; // gap: CWE-79 - nothing in this build reports it (docs/cwe-top25.md)
    }
}
