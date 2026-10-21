package planted.cwe25;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** CWE-22: `?name=../../etc/passwd` reads outside the reports directory. */
@RestController
public class Cwe022PathTraversal {
    private static final String REPORTS = "/srv/reports";

    @GetMapping("/reports")
    public byte[] download(@RequestParam String name) throws IOException {
        Path file = Paths.get(REPORTS, name); // expect-ext: semgrep/semgrep-rules.java.spring.security.injection.tainted-file-path
        return Files.readAllBytes(file);
    }
}
