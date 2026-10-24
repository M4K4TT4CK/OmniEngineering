package planted.cwe25;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** CWE-22: the resolved path must stay inside the reports directory. */
@RestController
public class Cwe022PathTraversal {
    private static final Path REPORTS = Path.of("/srv/reports").toAbsolutePath().normalize();

    @GetMapping("/reports")
    public byte[] download(@RequestParam String name) throws IOException {
        Path file = REPORTS.resolve(name).normalize();
        if (!file.startsWith(REPORTS) || !Files.isRegularFile(file)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND);
        }
        return Files.readAllBytes(file);
    }
}
