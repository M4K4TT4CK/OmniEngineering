package backup;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.TreeMap;
import org.apache.commons.compress.archivers.tar.TarArchiveEntry;
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream;
import org.apache.commons.compress.compressors.gzip.GzipCompressorInputStream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class BackupUploaderTest {
  private static final Instant NOW = Instant.parse("2026-10-23T02:00:00Z");

  @TempDir Path tmp;

  /** In-memory ObjectStore that keeps uploaded bytes so tests can inspect the archive. */
  static final class FakeStore implements BackupJob.ObjectStore {
    final Map<String, byte[]> objects = new TreeMap<>();
    final List<String> deleted = new ArrayList<>();
    RuntimeException uploadFailure;

    @Override
    public void upload(String key, Path file) throws IOException {
      if (uploadFailure != null) throw uploadFailure;
      objects.put(key, Files.readAllBytes(file));
    }

    @Override
    public List<String> list(String prefix) {
      return objects.keySet().stream().filter(k -> k.startsWith(prefix)).toList();
    }

    @Override
    public void delete(String key) {
      objects.remove(key);
      deleted.add(key);
    }
  }

  private Config config(String... extra) {
    Properties p = new Properties();
    p.setProperty("directories", tmp.resolve("data") + "," + tmp.resolve("etc"));
    p.setProperty("s3.bucket", "bucket");
    p.setProperty("s3.prefix", "host1/");
    p.setProperty("retention", "P30D");
    for (int i = 0; i < extra.length; i += 2) p.setProperty(extra[i], extra[i + 1]);
    return Config.fromProperties(p);
  }

  private void createSourceTree() throws IOException {
    Files.createDirectories(tmp.resolve("data/nested"));
    Files.writeString(tmp.resolve("data/a.txt"), "alpha");
    Files.writeString(tmp.resolve("data/nested/b.txt"), "bravo");
    Files.createDirectories(tmp.resolve("etc"));
    Files.writeString(tmp.resolve("etc/app.conf"), "x=1");
  }

  private static Map<String, TarEntryInfo> readTarGz(InputStream raw) throws IOException {
    Map<String, TarEntryInfo> entries = new HashMap<>();
    try (TarArchiveInputStream tar = new TarArchiveInputStream(new GzipCompressorInputStream(raw))) {
      TarArchiveEntry e;
      while ((e = tar.getNextEntry()) != null) {
        entries.put(e.getName(), new TarEntryInfo(e.isDirectory(), e.isSymbolicLink(), e.getLinkName(),
            new String(tar.readAllBytes(), UTF_8)));
      }
    }
    return entries;
  }

  record TarEntryInfo(boolean dir, boolean symlink, String linkName, String content) {}

  @Test
  void archiveStoresEachDirectoryUnderItsNameAndKeepsSymlinksAsLinks() throws IOException {
    createSourceTree();
    Path outside = Files.writeString(tmp.resolve("secret.txt"), "do not follow");
    Files.createSymbolicLink(tmp.resolve("data/link"), outside);

    Path out = tmp.resolve("out.tar.gz");
    Files.createFile(out);
    BackupJob.archive(List.of(tmp.resolve("data"), tmp.resolve("etc")), out);

    Map<String, TarEntryInfo> entries = readTarGz(Files.newInputStream(out));
    assertEquals(Set.of("data/", "data/a.txt", "data/nested/", "data/nested/b.txt", "data/link", "etc/",
        "etc/app.conf"), entries.keySet());
    assertEquals("alpha", entries.get("data/a.txt").content());
    assertEquals("bravo", entries.get("data/nested/b.txt").content());
    assertEquals("x=1", entries.get("etc/app.conf").content());
    assertTrue(entries.get("data/nested/").dir());
    assertTrue(entries.get("data/link").symlink());
    assertEquals(outside.toString(), entries.get("data/link").linkName());
    assertEquals("", entries.get("data/link").content());
  }

  @Test
  void runUploadsArchiveAndPrunesOnlyExpiredBackupsOfThisPrefix() throws IOException {
    createSourceTree();
    FakeStore store = new FakeStore();
    String expired = BackupJob.keyFor("host1/", NOW.minus(Duration.ofDays(31)));
    String recent = BackupJob.keyFor("host1/", NOW.minus(Duration.ofDays(29)));
    String otherHost = BackupJob.keyFor("host10/", NOW.minus(Duration.ofDays(400)));
    for (String k : List.of(expired, recent, otherHost, "host1/notes.txt", "host1/backup-garbage.tar.gz")) {
      store.objects.put(k, new byte[0]);
    }

    BackupJob.Result r = new BackupJob(config(), store, Clock.fixed(NOW, ZoneOffset.UTC)).run();

    assertTrue(r.success(), r.error());
    assertEquals("host1/backup-20261023T020000Z.tar.gz", r.key());
    assertEquals(List.of(expired), store.deleted);
    assertEquals(1, r.deleted());
    byte[] uploaded = store.objects.get(r.key());
    assertEquals(uploaded.length, r.bytes());
    assertEquals("bravo", readTarGz(new ByteArrayInputStream(uploaded)).get("data/nested/b.txt").content());
    assertTrue(store.objects.containsKey(recent));
    assertTrue(store.objects.containsKey(otherHost));
    assertTrue(store.objects.containsKey("host1/notes.txt"));
    assertTrue(store.objects.containsKey("host1/backup-garbage.tar.gz"));
  }

  @Test
  void failedUploadIsReportedAndSkipsRetention() throws IOException {
    createSourceTree();
    FakeStore store = new FakeStore();
    String expired = BackupJob.keyFor("host1/", NOW.minus(Duration.ofDays(90)));
    store.objects.put(expired, new byte[0]);
    store.uploadFailure = new IllegalStateException("bucket unreachable");

    BackupJob.Result r = new BackupJob(config(), store, Clock.fixed(NOW, ZoneOffset.UTC)).run();

    assertFalse(r.success());
    assertNull(r.key());
    assertTrue(r.error().contains("bucket unreachable"), r.error());
    assertTrue(store.deleted.isEmpty());
  }

  @Test
  void missingDirectoryFailsTheRun() {
    BackupJob.Result r = new BackupJob(config(), new FakeStore(), Clock.fixed(NOW, ZoneOffset.UTC)).run();
    assertFalse(r.success());
    assertTrue(r.error().contains("not a directory"), r.error());
  }

  @Test
  void parseKeyAcceptsOnlyExactBackupNames() {
    assertEquals(NOW, BackupJob.parseKey("p/", BackupJob.keyFor("p/", NOW)));
    assertNull(BackupJob.parseKey("p/", "p/backup-20261023T020000Z.tar.gz.part"));
    assertNull(BackupJob.parseKey("p/", "p/sub/backup-20261023T020000Z.tar.gz"));
    assertNull(BackupJob.parseKey("p/", "p/backup-2026.tar.gz"));
  }

  @Test
  void nextRunFollowsTheAnchorAndSkipsMissedSlots() {
    Instant anchor = Instant.parse("2026-10-23T02:00:00Z");
    Duration sixHours = Duration.ofHours(6);
    assertEquals(anchor, BackupJob.parseKey("", BackupJob.keyFor("", anchor)));
    assertEquals(anchor, BackupUploader.nextRun(anchor, sixHours, anchor.minusSeconds(60)));
    assertEquals(anchor.plus(sixHours), BackupUploader.nextRun(anchor, sixHours, anchor));
    assertEquals(Instant.parse("2026-10-24T02:00:00Z"),
        BackupUploader.nextRun(anchor, sixHours, Instant.parse("2026-10-23T21:30:00Z")));
  }

  @Test
  void anchorUsesFirstRunAtInTheGivenZone() {
    ZoneId zone = ZoneId.of("America/New_York");
    Instant start = Instant.parse("2026-10-23T15:00:00Z"); // 11:00 in New York
    assertEquals(Instant.parse("2026-10-23T06:30:00Z"),
        BackupUploader.anchor(start, LocalTime.of(2, 30), zone));
    assertEquals(start, BackupUploader.anchor(start, null, zone));
  }

  @Test
  void configValidation() {
    Config c = config("schedule.interval", "PT6H", "schedule.firstRunAt", "02:30",
        "s3.accessKeyId", "AK", "s3.secretAccessKey", "SECRET");
    assertEquals(Duration.ofHours(6), c.interval());
    assertEquals(LocalTime.of(2, 30), c.firstRunAt());
    assertEquals("127.0.0.1", c.statusHost());
    assertFalse(c.toString().contains("SECRET"));

    assertThrows(IllegalArgumentException.class, () -> config("s3.bucket", ""));
    assertThrows(IllegalArgumentException.class, () -> config("retention", "-P1D"));
    assertThrows(IllegalArgumentException.class, () -> config("schedule.interval", "daily"));
    assertThrows(IllegalArgumentException.class, () -> config("s3.accessKeyId", "AK"));
    assertThrows(IllegalArgumentException.class,
        () -> config("directories", tmp.resolve("a/data") + "," + tmp.resolve("b/data")));
  }

  @Test
  void statusEndpointReportsLastRun() throws Exception {
    BackupUploader.Status status = new BackupUploader.Status();
    HttpServer server = BackupUploader.startStatusServer("127.0.0.1", 0, status);
    try {
      HttpClient client = HttpClient.newHttpClient();
      URI uri = URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/status");

      HttpResponse<String> before = client.send(HttpRequest.newBuilder(uri).build(),
          HttpResponse.BodyHandlers.ofString());
      assertEquals(200, before.statusCode());
      assertTrue(before.body().startsWith("{\"lastRun\":null"), before.body());

      status.last = new BackupJob.Result(NOW, NOW.plusSeconds(5), false, null, 0, 0, "boom \"quoted\"\nline");
      status.next = NOW.plus(Duration.ofDays(1));
      HttpResponse<String> after = client.send(HttpRequest.newBuilder(uri).build(),
          HttpResponse.BodyHandlers.ofString());
      assertEquals("application/json", after.headers().firstValue("Content-Type").orElse(""));
      assertEquals("{\"lastRun\":{\"started\":\"2026-10-23T02:00:00Z\",\"finished\":\"2026-10-23T02:00:05Z\","
          + "\"result\":\"failure\",\"key\":null,\"bytes\":0,\"deleted\":0,"
          + "\"error\":\"boom \\\"quoted\\\"\\nline\"},\"runningSince\":null,"
          + "\"nextRun\":\"2026-10-24T02:00:00Z\"}", after.body());

      HttpResponse<String> post = client.send(
          HttpRequest.newBuilder(uri).POST(HttpRequest.BodyPublishers.noBody()).build(),
          HttpResponse.BodyHandlers.ofString());
      assertEquals(405, post.statusCode());
      HttpResponse<String> other = client.send(HttpRequest.newBuilder(uri.resolve("/nope")).build(),
          HttpResponse.BodyHandlers.ofString());
      assertEquals(404, other.statusCode());
    } finally {
      server.stop(0);
    }
  }
}
