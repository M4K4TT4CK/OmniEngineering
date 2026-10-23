package planted.cwe25;

import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.TimeUnit;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** CWE-77: only https URLs, and `--` ends git's and rsync's options. */
@RestController
public class Cwe077ArgumentInjection {
    @PostMapping("/import")
    public Path cloneRepo(@RequestParam String repoUrl) throws IOException, InterruptedException {
        URI uri = URI.create(repoUrl);
        if (!"https".equals(uri.getScheme())) {
            throw new IllegalArgumentException("repository must be an https URL");
        }
        Path dir = Files.createTempDirectory("import-");
        Process p = new ProcessBuilder("git", "clone", "--", uri.toString(), dir.toString()).inheritIO().start();
        if (!p.waitFor(2, TimeUnit.MINUTES)) {
            p.destroyForcibly();
            throw new IOException("git clone timed out");
        }
        return dir;
    }

    @PostMapping("/mirror")
    public int mirror(@RequestParam("source") String source) throws IOException, InterruptedException {
        Process p = new ProcessBuilder(List.of("rsync", "-a", "--", source, "/srv/mirror/")).start();
        return p.waitFor();
    }
}
