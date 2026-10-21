package planted;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

/** Host commands, built the way servlet code often builds them. */
public class HostCommands {
    public Process echo(String text) throws IOException {
        Runtime rt = Runtime.getRuntime();
        return rt.exec("echo " + text); // expect: arbiter/java.command-injection
    }

    public Process shellList(String text) throws IOException {
        List<String> args = new ArrayList<>();
        args.add("sh");
        args.add("-c");
        args.add("echo " + text);
        return new ProcessBuilder(args).start(); // expect: arbiter/java.command-injection
    }

    public Process shellCommand(String text) throws IOException {
        List<String> args = new ArrayList<>();
        args.add("cmd.exe");
        args.add("/c");
        args.add("echo " + text);
        ProcessBuilder pb = new ProcessBuilder();
        pb.command(args); // expect: arbiter/java.command-injection
        return pb.start();
    }
}
