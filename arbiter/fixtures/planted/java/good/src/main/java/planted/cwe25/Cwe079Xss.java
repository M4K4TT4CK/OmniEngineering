package planted.cwe25;

import java.io.IOException;

import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import org.owasp.encoder.Encode;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
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

    @GetMapping("/users/{name}/card")
    public ResponseEntity<String> card(@PathVariable("name") String name) {
        return ResponseEntity.ok().contentType(MediaType.TEXT_HTML).body("<div class=\"card\">" + Encode.forHtml(name) + "</div>");
    }

    /** The servlet form: the parameter is encoded for HTML before it is printed. */
    public static class Greeting extends HttpServlet {
        @Override
        protected void doGet(HttpServletRequest request, HttpServletResponse response) throws IOException {
            String who = request.getParameter("who");
            response.setContentType("text/html;charset=UTF-8");
            response.getWriter().println("<p>Hello, " + Encode.forHtml(who) + "</p>");
        }
    }
}
