package backup;

import static java.nio.charset.StandardCharsets.UTF_8;
import static java.util.concurrent.TimeUnit.MILLISECONDS;
import static java.util.concurrent.TimeUnit.SECONDS;

import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.file.Path;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalTime;
import java.time.ZoneId;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.logging.Logger;

/** Entry point: runs one backup with --now, otherwise schedules backups and serves /status. */
public final class BackupUploader {
  static {
    // One line per log record. Must be set before java.util.logging initializes.
    if (System.getProperty("java.util.logging.SimpleFormatter.format") == null) {
      System.setProperty("java.util.logging.SimpleFormatter.format", "%1$tFT%1$tT.%1$tL%1$tz %4$s %5$s%6$s%n");
    }
  }

  private static final Logger LOG = Logger.getLogger(BackupUploader.class.getName());

  private static final String USAGE = "usage: backup-uploader [--config FILE] [--now]\n"
      + "  --config FILE  properties file (default: backup.properties)\n"
      + "  --now          run one backup immediately and exit (status 0 on success, 1 on failure)";

  public static void main(String[] args) {
    Path configPath = Path.of("backup.properties");
    boolean now = false;
    for (int i = 0; i < args.length; i++) {
      switch (args[i]) {
        case "--now" -> now = true;
        case "--config" -> {
          if (++i == args.length) exit(2, USAGE);
          configPath = Path.of(args[i]);
        }
        case "-h", "--help" -> exit(0, USAGE);
        default -> exit(2, "unknown argument: " + args[i] + "\n" + USAGE);
      }
    }

    Config config = null;
    try {
      config = Config.load(configPath);
    } catch (IOException | IllegalArgumentException e) {
      exit(2, "config error: " + e);
    }

    BackupJob.S3Store store = new BackupJob.S3Store(config);
    BackupJob job = new BackupJob(config, store, Clock.systemUTC());

    if (now) {
      boolean ok;
      try {
        ok = job.run().success();
      } finally {
        store.close();
      }
      System.exit(ok ? 0 : 1);
    }

    Daemon daemon = new Daemon(config, job, Clock.systemUTC());
    try {
      daemon.start();
    } catch (IOException e) {
      store.close();
      exit(2, "cannot start status server on " + config.statusHost() + ":" + config.statusPort() + ": " + e);
    }
    Runtime.getRuntime().addShutdownHook(new Thread(() -> {
      daemon.stop();
      store.close();
    }));
    // The scheduler and HTTP threads are non-daemon and keep the process alive.
  }

  private static void exit(int code, String message) {
    (code == 0 ? System.out : System.err).println(message);
    System.exit(code);
  }

  /** State shown by the status endpoint, written by the scheduler thread. */
  static final class Status {
    volatile BackupJob.Result last;
    volatile Instant runningSince;
    volatile Instant next;
  }

  /** Runs the job on schedule, one run at a time, and serves its status. */
  static final class Daemon {
    private final Config config;
    private final BackupJob job;
    private final Clock clock;
    final Status status = new Status();
    private final ScheduledExecutorService exec =
        Executors.newSingleThreadScheduledExecutor(r -> new Thread(r, "backup-scheduler"));
    private HttpServer server;
    private Instant anchor;

    Daemon(Config config, BackupJob job, Clock clock) {
      this.config = config;
      this.job = job;
      this.clock = clock;
    }

    void start() throws IOException {
      if (config.statusPort() != 0) server = startStatusServer(config.statusHost(), config.statusPort(), status);
      Instant start = clock.instant();
      anchor = anchor(start, config.firstRunAt(), ZoneId.systemDefault());
      scheduleAfter(start);
      LOG.info("started; interval=" + config.interval() + " retention=" + config.retention()
          + " next=" + status.next + (server == null ? "" : " status=http://" + config.statusHost() + ":"
          + server.getAddress().getPort() + "/status"));
    }

    void stop() {
      exec.shutdownNow();
      try {
        exec.awaitTermination(30, SECONDS);
      } catch (InterruptedException e) {
        Thread.currentThread().interrupt();
      }
      if (server != null) server.stop(1);
    }

    private void scheduleAfter(Instant after) {
      Instant next = nextRun(anchor, config.interval(), after);
      status.next = next;
      long delay = Math.max(0, Duration.between(clock.instant(), next).toMillis());
      exec.schedule(() -> runScheduled(next), delay, MILLISECONDS);
    }

    private void runScheduled(Instant scheduled) {
      try {
        status.runningSince = clock.instant();
        status.last = job.run();
      } finally {
        status.runningSince = null;
        // If the timer fired slightly early, schedule from the intended time so the slot doesn't run twice.
        Instant now = clock.instant();
        try {
          scheduleAfter(now.isAfter(scheduled) ? now : scheduled);
        } catch (RejectedExecutionException e) {
          // shutting down
        }
      }
    }
  }

  /** The instant the schedule counts from: today's firstRunAt in the given zone, or the start time. */
  static Instant anchor(Instant start, LocalTime firstRunAt, ZoneId zone) {
    if (firstRunAt == null) return start;
    return start.atZone(zone).toLocalDate().atTime(firstRunAt).atZone(zone).toInstant();
  }

  /** The first instant anchor + k*interval (k >= 0) strictly after {@code after}. Missed slots are skipped. */
  static Instant nextRun(Instant anchor, Duration interval, Instant after) {
    if (after.isBefore(anchor)) return anchor;
    long k = Duration.between(anchor, after).toNanos() / interval.toNanos() + 1;
    return anchor.plus(interval.multipliedBy(k));
  }

  static HttpServer startStatusServer(String host, int port, Status status) throws IOException {
    HttpServer server = HttpServer.create(new InetSocketAddress(host, port), 0);
    server.createContext("/", exchange -> {
      try {
        String method = exchange.getRequestMethod();
        if (!exchange.getRequestURI().getPath().equals("/status")) {
          exchange.sendResponseHeaders(404, -1);
        } else if (!method.equals("GET") && !method.equals("HEAD")) {
          exchange.getResponseHeaders().set("Allow", "GET, HEAD");
          exchange.sendResponseHeaders(405, -1);
        } else {
          byte[] body = statusJson(status).getBytes(UTF_8);
          exchange.getResponseHeaders().set("Content-Type", "application/json");
          exchange.getResponseHeaders().set("Cache-Control", "no-store");
          if (method.equals("HEAD")) {
            exchange.sendResponseHeaders(200, -1);
          } else {
            exchange.sendResponseHeaders(200, body.length);
            try (OutputStream out = exchange.getResponseBody()) {
              out.write(body);
            }
          }
        }
      } finally {
        exchange.close();
      }
    });
    server.start();
    return server;
  }

  static String statusJson(Status status) {
    BackupJob.Result r = status.last;
    StringBuilder b = new StringBuilder("{\"lastRun\":");
    if (r == null) {
      b.append("null");
    } else {
      b.append("{\"started\":").append(str(r.started()))
          .append(",\"finished\":").append(str(r.finished()))
          .append(",\"result\":").append(str(r.success() ? "success" : "failure"))
          .append(",\"key\":").append(str(r.key()))
          .append(",\"bytes\":").append(r.bytes())
          .append(",\"deleted\":").append(r.deleted())
          .append(",\"error\":").append(str(r.error()))
          .append('}');
    }
    b.append(",\"runningSince\":").append(str(status.runningSince));
    b.append(",\"nextRun\":").append(str(status.next));
    return b.append('}').toString();
  }

  private static String str(Object value) {
    if (value == null) return "null";
    String s = value.toString();
    StringBuilder b = new StringBuilder(s.length() + 2).append('"');
    for (int i = 0; i < s.length(); i++) {
      char c = s.charAt(i);
      switch (c) {
        case '"' -> b.append("\\\"");
        case '\\' -> b.append("\\\\");
        case '\n' -> b.append("\\n");
        case '\r' -> b.append("\\r");
        case '\t' -> b.append("\\t");
        default -> {
          if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
          else b.append(c);
        }
      }
    }
    return b.append('"').toString();
  }
}
