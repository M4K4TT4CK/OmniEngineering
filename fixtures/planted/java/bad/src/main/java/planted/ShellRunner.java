package planted;

import java.io.IOException;
import java.nio.file.Path;

/** Runs maintenance commands on the host. */
public class ShellRunner {
    public Process run(String command) throws IOException {
        return Runtime.getRuntime().exec(command); // expect: arbiter/java.command-injection
    }

    public Process shell(String script) throws IOException {
        return new ProcessBuilder("sh", "-c", script).start(); // expect: arbiter/java.command-injection
    }

    public Process archive(Path dir, String name) throws IOException {
        return Runtime.getRuntime().exec(new String[] {"bash", "-c", "cd " + dir + " && tar czf " + name + ".tgz ."}); // expect: arbiter/java.command-injection
    }
}
