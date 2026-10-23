package backup;

import java.io.BufferedOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.logging.Logger;
import java.util.stream.Stream;
import org.apache.commons.compress.archivers.tar.TarArchiveEntry;
import org.apache.commons.compress.archivers.tar.TarArchiveOutputStream;
import org.apache.commons.compress.archivers.tar.TarConstants;
import org.apache.commons.compress.compressors.gzip.GzipCompressorOutputStream;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.DefaultCredentialsProvider;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.core.checksums.RequestChecksumCalculation;
import software.amazon.awssdk.core.checksums.ResponseChecksumValidation;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.S3ClientBuilder;
import software.amazon.awssdk.services.s3.S3Configuration;
import software.amazon.awssdk.services.s3.model.CompletedPart;
import software.amazon.awssdk.services.s3.model.S3Object;

/** One backup run: archive the directories, upload the tarball, prune expired backups. */
public final class BackupJob {
  private static final Logger LOG = Logger.getLogger(BackupJob.class.getName());

  static final String NAME_PREFIX = "backup-";
  static final String NAME_SUFFIX = ".tar.gz";
  private static final DateTimeFormatter STAMP = DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmss'Z'");

  /** The operations a backup needs from the object store; S3Store is the real one. */
  public interface ObjectStore {
    void upload(String key, Path file) throws IOException;

    List<String> list(String prefix) throws IOException;

    void delete(String key) throws IOException;
  }

  /** Outcome of a run. On failure {@code key} is set if the upload itself succeeded. */
  public record Result(
      Instant started, Instant finished, boolean success, String key, long bytes, int deleted, String error) {}

  private final Config config;
  private final ObjectStore store;
  private final Clock clock;

  public BackupJob(Config config, ObjectStore store, Clock clock) {
    this.config = config;
    this.store = store;
    this.clock = clock;
  }

  /** Never throws; every failure is reported in the returned Result and the log line. */
  public Result run() {
    Instant started = clock.instant();
    String key = keyFor(config.prefix(), started);
    String uploaded = null;
    long bytes = 0;
    int deleted = 0;
    String error = null;
    Path tmp = null;
    try {
      // createTempFile uses owner-only permissions on POSIX, which matters for backup contents.
      tmp = Files.createTempFile("backup-", NAME_SUFFIX);
      archive(config.directories(), tmp);
      bytes = Files.size(tmp);
      store.upload(key, tmp);
      uploaded = key;
      try {
        deleted = prune(started, key);
      } catch (Exception e) {
        error = "uploaded, but retention cleanup failed: " + e;
      }
    } catch (Exception e) {
      error = e.toString();
    } finally {
      if (tmp != null) {
        try {
          Files.deleteIfExists(tmp);
        } catch (IOException e) {
          LOG.warning("could not delete temp file " + tmp + ": " + e);
        }
      }
    }
    Instant finished = clock.instant();
    Result r = new Result(started, finished, error == null, uploaded, bytes, deleted, error);
    String took = Duration.between(started, finished).toMillis() + "ms";
    if (r.success()) {
      LOG.info("backup ok key=" + key + " bytes=" + bytes + " deleted=" + deleted + " took=" + took);
    } else {
      LOG.warning("backup FAILED key=" + key + " uploaded=" + (uploaded != null) + " took=" + took
          + " error=" + error.replaceAll("[\\r\\n]+", " "));
    }
    return r;
  }

  /** Deletes backups under the prefix older than the retention period. Returns the number deleted. */
  int prune(Instant now, String justUploaded) throws IOException {
    Instant cutoff = now.minus(config.retention());
    int deleted = 0;
    for (String k : store.list(config.prefix())) {
      Instant t = parseKey(config.prefix(), k);
      // Only touch objects whose name is exactly one of ours; anything else in the bucket is left alone.
      if (t == null || k.equals(justUploaded)) continue;
      if (t.isBefore(cutoff)) {
        store.delete(k);
        deleted++;
      }
    }
    return deleted;
  }

  static String keyFor(String prefix, Instant t) {
    return prefix + NAME_PREFIX + STAMP.format(LocalDateTime.ofInstant(t, ZoneOffset.UTC)) + NAME_SUFFIX;
  }

  /** Returns the backup time encoded in a key, or null if the key is not a backup made by this tool. */
  static Instant parseKey(String prefix, String key) {
    String head = prefix + NAME_PREFIX;
    if (!key.startsWith(head) || !key.endsWith(NAME_SUFFIX)) return null;
    String stamp = key.substring(head.length(), key.length() - NAME_SUFFIX.length());
    try {
      return LocalDateTime.parse(stamp, STAMP).toInstant(ZoneOffset.UTC);
    } catch (DateTimeParseException e) {
      return null;
    }
  }

  /**
   * Writes a gzip tarball where each directory appears under its own name. Symlinks are stored as
   * links, not followed; sockets, FIFOs and devices are skipped.
   */
  static void archive(List<Path> dirs, Path out) throws IOException {
    Path self = out.toRealPath();
    try (OutputStream fo = new BufferedOutputStream(Files.newOutputStream(out));
        GzipCompressorOutputStream gz = new GzipCompressorOutputStream(fo);
        TarArchiveOutputStream tar = new TarArchiveOutputStream(gz)) {
      tar.setLongFileMode(TarArchiveOutputStream.LONGFILE_POSIX);
      tar.setBigNumberMode(TarArchiveOutputStream.BIGNUMBER_POSIX);
      for (Path configured : dirs) {
        if (!Files.isDirectory(configured)) throw new IOException("not a directory: " + configured);
        Path dir = configured.toRealPath();
        String root = configured.getFileName().toString();
        try (Stream<Path> walk = Files.walk(dir)) {
          for (Path p : (Iterable<Path>) walk::iterator) {
            if (p.equals(self)) continue; // the temp dir may be inside a backed-up directory
            String rel = dir.relativize(p).toString().replace(File.separatorChar, '/');
            addEntry(tar, p, rel.isEmpty() ? root : root + "/" + rel);
          }
        }
      }
      tar.finish();
    }
  }

  private static void addEntry(TarArchiveOutputStream tar, Path p, String name) throws IOException {
    try {
      if (Files.isSymbolicLink(p)) {
        TarArchiveEntry e = new TarArchiveEntry(name, TarConstants.LF_SYMLINK);
        e.setLinkName(Files.readSymbolicLink(p).toString());
        e.setModTime(Files.getLastModifiedTime(p, LinkOption.NOFOLLOW_LINKS));
        tar.putArchiveEntry(e);
        tar.closeArchiveEntry();
      } else if (Files.isDirectory(p, LinkOption.NOFOLLOW_LINKS)) {
        tar.putArchiveEntry(new TarArchiveEntry(p, name, LinkOption.NOFOLLOW_LINKS));
        tar.closeArchiveEntry();
      } else if (Files.isRegularFile(p, LinkOption.NOFOLLOW_LINKS)) {
        TarArchiveEntry e = new TarArchiveEntry(p, name, LinkOption.NOFOLLOW_LINKS);
        tar.putArchiveEntry(e);
        copyExactly(p, e.getSize(), tar);
        tar.closeArchiveEntry();
      }
    } catch (NoSuchFileException e) {
      LOG.warning("skipped, vanished while archiving: " + p);
    }
  }

  /** Copies exactly {@code size} bytes so a file that changes mid-read cannot corrupt the tar stream. */
  private static void copyExactly(Path file, long size, OutputStream out) throws IOException {
    byte[] buf = new byte[64 * 1024];
    long remaining = size;
    try (InputStream in = Files.newInputStream(file)) {
      while (remaining > 0) {
        int n = in.read(buf, 0, (int) Math.min(buf.length, remaining));
        if (n < 0) break;
        out.write(buf, 0, n);
        remaining -= n;
      }
    }
    if (remaining > 0) {
      LOG.warning("file shrank while archiving, padded with zeros: " + file);
      Arrays.fill(buf, (byte) 0);
      while (remaining > 0) {
        int n = (int) Math.min(buf.length, remaining);
        out.write(buf, 0, n);
        remaining -= n;
      }
    }
  }

  /** ObjectStore backed by any S3-compatible service via the AWS SDK. */
  public static final class S3Store implements ObjectStore, AutoCloseable {
    // 32 MiB parts allow archives up to ~320 GB within S3's 10,000-part limit.
    private static final int PART_SIZE = 32 * 1024 * 1024;

    private final S3Client s3;
    private final String bucket;

    public S3Store(Config c) {
      S3ClientBuilder b = S3Client.builder()
          .region(Region.of(c.region()))
          .serviceConfiguration(S3Configuration.builder().pathStyleAccessEnabled(c.pathStyle()).build())
          // Many S3-compatible stores reject the SDK's newer default checksum headers.
          .requestChecksumCalculation(RequestChecksumCalculation.WHEN_REQUIRED)
          .responseChecksumValidation(ResponseChecksumValidation.WHEN_REQUIRED);
      if (c.endpoint() != null) b.endpointOverride(URI.create(c.endpoint()));
      b.credentialsProvider(c.accessKeyId() != null
          ? StaticCredentialsProvider.create(AwsBasicCredentials.create(c.accessKeyId(), c.secretAccessKey()))
          : DefaultCredentialsProvider.create());
      this.s3 = b.build();
      this.bucket = c.bucket();
    }

    @Override
    public void upload(String key, Path file) throws IOException {
      long size = Files.size(file);
      if (size <= PART_SIZE) {
        s3.putObject(r -> r.bucket(bucket).key(key).contentType("application/gzip"), RequestBody.fromFile(file));
        return;
      }
      String uploadId = s3.createMultipartUpload(r -> r.bucket(bucket).key(key).contentType("application/gzip"))
          .uploadId();
      try (InputStream in = Files.newInputStream(file)) {
        List<CompletedPart> parts = new ArrayList<>();
        byte[] buf = new byte[PART_SIZE];
        for (int part = 1; ; part++) {
          int n = in.readNBytes(buf, 0, buf.length);
          if (n == 0) break;
          int partNumber = part;
          String etag = s3.uploadPart(
                  r -> r.bucket(bucket).key(key).uploadId(uploadId).partNumber(partNumber),
                  RequestBody.fromBytes(n == buf.length ? buf : Arrays.copyOf(buf, n)))
              .eTag();
          parts.add(CompletedPart.builder().partNumber(partNumber).eTag(etag).build());
        }
        s3.completeMultipartUpload(r -> r.bucket(bucket).key(key).uploadId(uploadId)
            .multipartUpload(m -> m.parts(parts)));
      } catch (IOException | RuntimeException e) {
        try {
          s3.abortMultipartUpload(r -> r.bucket(bucket).key(key).uploadId(uploadId));
        } catch (RuntimeException abortFailure) {
          e.addSuppressed(abortFailure);
        }
        throw e;
      }
    }

    @Override
    public List<String> list(String prefix) {
      List<String> keys = new ArrayList<>();
      for (S3Object o : s3.listObjectsV2Paginator(r -> r.bucket(bucket).prefix(prefix)).contents()) {
        keys.add(o.key());
      }
      return keys;
    }

    @Override
    public void delete(String key) {
      // Single deletes rather than DeleteObjects: some S3-compatible stores lack the batch API.
      s3.deleteObject(r -> r.bucket(bucket).key(key));
    }

    @Override
    public void close() {
      s3.close();
    }
  }
}
