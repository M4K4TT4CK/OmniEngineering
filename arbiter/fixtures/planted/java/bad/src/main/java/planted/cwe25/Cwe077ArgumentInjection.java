package planted.cwe25;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.TimeUnit;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** CWE-77 (argument injection, CWE-88): a repoUrl of "--upload-pack=..." is an option to git. */
@RestController
public class Cwe077ArgumentInjection {
    @PostMapping("/import")
    public Path cloneRepo(@RequestParam String repoUrl) throws IOException, InterruptedException {
        Path dir = Files.createTempDirectory("import-");
        Process p = new ProcessBuilder("git", "clone", repoUrl, dir.toString()).inheritIO().start(); // expect: arbiter/java.argument-injection
        if (!p.waitFor(2, TimeUnit.MINUTES)) {
            p.destroyForcibly();
            throw new IOException("git clone timed out");
        }
        return dir;
    }

    /** A `source` of "-e sh -c ..." makes rsync run a remote shell of the caller's choosing. */
    @PostMapping("/mirror")
    public int mirror(@RequestParam("source") String source) throws IOException, InterruptedException {
        Process p = new ProcessBuilder(List.of("rsync", "-a", source, "/srv/mirror/")).start(); // expect: arbiter/java.argument-injection
        return p.waitFor();
    }
}
