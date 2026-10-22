package planted.cwe25;

import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.util.HtmlUtils;

/** CWE-79: the search term is HTML-escaped before it reaches the page. */
@RestController
public class Cwe079Xss {
    @GetMapping(value = "/search", produces = MediaType.TEXT_HTML_VALUE)
    public String search(@RequestParam String q) {
        return "<h1>Results for " + HtmlUtils.htmlEscape(q) + "</h1><p>No matches.</p>";
    }
}
