package planted.cwe25;

import java.io.IOException;

import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** CWE-79: the search term is concatenated into an HTML response. */
@RestController
public class Cwe079Xss {
    @GetMapping(value = "/search", produces = MediaType.TEXT_HTML_VALUE)
    public String search(@RequestParam String q) {
        return "<h1>Results for " + q + "</h1><p>No matches.</p>"; // expect: arbiter/java.xss-unescaped-html
    }

    @GetMapping("/users/{name}/card")
    public ResponseEntity<String> card(@PathVariable("name") String name) {
        return ResponseEntity.ok().contentType(MediaType.TEXT_HTML).body("<div class=\"card\">" + name + "</div>"); // expect: arbiter/java.xss-unescaped-html
    }

    /** The servlet form: a request parameter printed into a text/html page. */
    public static class Greeting extends HttpServlet {
        @Override
        protected void doGet(HttpServletRequest request, HttpServletResponse response) throws IOException {
            String who = request.getParameter("who");
            response.setContentType("text/html;charset=UTF-8");
            response.getWriter().println("<p>Hello, " + who + "</p>"); // expect: arbiter/java.xss-unescaped-html
        }
    }
}
