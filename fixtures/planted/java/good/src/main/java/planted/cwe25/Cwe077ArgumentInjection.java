package planted.cwe25;

import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.TimeUnit;

/** CWE-77: only https URLs, and `--` ends git's options. */
public class Cwe077ArgumentInjection {
    public Path cloneRepo(String repoUrl) throws IOException, InterruptedException {
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
}
