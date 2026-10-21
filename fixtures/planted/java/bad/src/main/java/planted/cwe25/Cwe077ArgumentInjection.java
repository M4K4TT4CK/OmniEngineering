package planted.cwe25;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.TimeUnit;

/** CWE-77 (argument injection, CWE-88): a repoUrl of "--upload-pack=..." is an option to git. */
public class Cwe077ArgumentInjection {
    public Path cloneRepo(String repoUrl) throws IOException, InterruptedException {
        Path dir = Files.createTempDirectory("import-");
        Process p = new ProcessBuilder("git", "clone", repoUrl, dir.toString()).inheritIO().start(); // gap: CWE-77 - nothing in this build reports it (docs/cwe-top25.md)
        if (!p.waitFor(2, TimeUnit.MINUTES)) {
            p.destroyForcibly();
            throw new IOException("git clone timed out");
        }
        return dir;
    }
}
