package backup;

import java.io.IOException;
import java.io.Reader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.LocalTime;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Properties;
import java.util.Set;

/** Settings loaded from a Java properties file. See README.md for the keys. */
public record Config(
    List<Path> directories,
    String bucket,
    String prefix,
    String endpoint,
    String region,
    boolean pathStyle,
    String accessKeyId,
    String secretAccessKey,
    Duration interval,
    LocalTime firstRunAt,
    Duration retention,
    String statusHost,
    int statusPort) {

  public static Config load(Path file) throws IOException {
    Properties p = new Properties();
    try (Reader r = Files.newBufferedReader(file)) {
      p.load(r);
    }
    return fromProperties(p);
  }

  public static Config fromProperties(Properties p) {
    List<Path> dirs = new ArrayList<>();
    Set<String> names = new HashSet<>();
    for (String s : required(p, "directories").split(",")) {
      if (s.isBlank()) continue;
      Path dir = Path.of(s.trim()).toAbsolutePath().normalize();
      if (dir.getFileName() == null) {
        throw new IllegalArgumentException("cannot back up a filesystem root: " + dir);
      }
      // Each directory is stored in the archive under its own name, so names must be unique.
      if (!names.add(dir.getFileName().toString())) {
        throw new IllegalArgumentException(
            "directories share the name '" + dir.getFileName() + "'; archive entries would collide");
      }
      dirs.add(dir);
    }
    if (dirs.isEmpty()) throw new IllegalArgumentException("directories: no directories listed");

    String accessKeyId = optional(p, "s3.accessKeyId");
    String secretAccessKey = optional(p, "s3.secretAccessKey");
    if ((accessKeyId == null) != (secretAccessKey == null)) {
      throw new IllegalArgumentException("s3.accessKeyId and s3.secretAccessKey must be set together");
    }

    LocalTime firstRunAt = null;
    String first = optional(p, "schedule.firstRunAt");
    if (first != null) {
      try {
        firstRunAt = LocalTime.parse(first);
      } catch (DateTimeParseException e) {
        throw new IllegalArgumentException("schedule.firstRunAt: expected HH:mm, got '" + first + "'");
      }
    }

    int port;
    try {
      port = Integer.parseInt(p.getProperty("status.port", "8080").trim());
    } catch (NumberFormatException e) {
      throw new IllegalArgumentException("status.port: not a number");
    }
    if (port < 0 || port > 65535) throw new IllegalArgumentException("status.port: out of range");

    return new Config(
        List.copyOf(dirs),
        required(p, "s3.bucket"),
        p.getProperty("s3.prefix", "").trim(),
        optional(p, "s3.endpoint"),
        p.getProperty("s3.region", "us-east-1").trim(),
        Boolean.parseBoolean(p.getProperty("s3.pathStyle", "false").trim()),
        accessKeyId,
        secretAccessKey,
        positiveDuration(p, "schedule.interval", "PT24H"),
        firstRunAt,
        positiveDuration(p, "retention", "P30D"),
        p.getProperty("status.host", "127.0.0.1").trim(),
        port);
  }

  private static String required(Properties p, String key) {
    String v = optional(p, key);
    if (v == null) throw new IllegalArgumentException(key + ": required");
    return v;
  }

  private static String optional(Properties p, String key) {
    String v = p.getProperty(key);
    return v == null || v.isBlank() ? null : v.trim();
  }

  private static Duration positiveDuration(Properties p, String key, String dflt) {
    String v = p.getProperty(key, dflt).trim();
    Duration d;
    try {
      d = Duration.parse(v);
    } catch (DateTimeParseException e) {
      throw new IllegalArgumentException(key + ": expected an ISO-8601 duration like PT6H or P30D, got '" + v + "'");
    }
    if (d.isNegative() || d.isZero()) throw new IllegalArgumentException(key + ": must be positive");
    return d;
  }

  /** Keeps the secret key out of logs and stack traces. */
  @Override
  public String toString() {
    return "Config[directories=" + directories + ", bucket=" + bucket + ", prefix=" + prefix
        + ", endpoint=" + endpoint + ", region=" + region + ", pathStyle=" + pathStyle
        + ", accessKeyId=" + accessKeyId + ", secretAccessKey=" + (secretAccessKey == null ? null : "***")
        + ", interval=" + interval + ", firstRunAt=" + firstRunAt + ", retention=" + retention
        + ", statusHost=" + statusHost + ", statusPort=" + statusPort + "]";
  }
}
