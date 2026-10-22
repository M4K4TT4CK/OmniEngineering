package planted;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

/** Host commands, built the way servlet code often builds them. */
public class HostCommands {
    public Process echo(String text) throws IOException {
        Runtime rt = Runtime.getRuntime();
        return rt.exec(new String[] {"echo", "--", "hello"});
    }

    public Process shellList(String text) throws IOException {
        // No shell: `echo` gets the text as one argument and parses nothing.
        List<String> args = new ArrayList<>();
        args.add("echo");
        args.add(text);
        return new ProcessBuilder(args).start();
    }

    public Process shellCommand(String text) throws IOException {
        List<String> args = List.of("echo", text);
        ProcessBuilder pb = new ProcessBuilder();
        pb.command(args);
        return pb.start();
    }
}
