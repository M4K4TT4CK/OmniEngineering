package planted;

import java.io.IOException;
import java.nio.file.Path;
import java.util.List;

/** Runs maintenance commands on the host. */
public class ShellRunner {
    private static final String[] STATUS = {"git", "status", "--porcelain"};

    public Process status() throws IOException {
        return Runtime.getRuntime().exec(STATUS);
    }

    public Process convert(Path input, Path output) throws IOException {
        return new ProcessBuilder("convert", input.toString(), output.toString()).start();
    }

    public Process archive(Path dir, String name) throws IOException {
        if (!name.matches("[A-Za-z0-9_-]+")) {
            throw new IllegalArgumentException("bad archive name");
        }
        return new ProcessBuilder(List.of("tar", "czf", name + ".tgz", "."))
                .directory(dir.toFile())
                .start();
    }
}
