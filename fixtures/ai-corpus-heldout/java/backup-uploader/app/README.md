# backup-uploader

A long-running process that, on a schedule, archives a set of directories into a
`.tar.gz`, uploads it to an S3-compatible bucket, and deletes backups older than
the retention period. It writes one log line per run and serves the last
run's result over HTTP.

Java 17+, Maven. Dependencies: AWS SDK v2 (S3), Apache Commons Compress, JUnit 5.
The status server is the JDK's built-in `com.sun.net.httpserver`.

## Build and run

```sh
mvn package                      # runs the tests, builds target/backup-uploader-1.0.0.jar
cp backup.properties.example backup.properties   # then edit it
java -jar target/backup-uploader-1.0.0.jar --config backup.properties
java -jar target/backup-uploader-1.0.0.jar --config backup.properties --now
```

`--now` runs a single backup and exits with status 0 on success, 1 on failure,
which makes it usable from cron or by hand. Configuration errors exit with 2.

## Configuration

See `backup.properties.example`. Durations are ISO-8601 (`PT6H`, `P30D`).

| Key | Default | Meaning |
| --- | --- | --- |
| `directories` | required | Comma-separated directories to archive |
| `s3.bucket` | required | Bucket name |
| `s3.prefix` | empty | Key prefix, e.g. `host1/` |
| `s3.region` | `us-east-1` | Region |
| `s3.endpoint` | AWS | Endpoint URL for non-AWS stores |
| `s3.pathStyle` | `false` | Path-style addressing (usually `true` for MinIO) |
| `s3.accessKeyId` / `s3.secretAccessKey` | AWS default chain | Static credentials |
| `schedule.interval` | `PT24H` | Time between runs |
| `schedule.firstRunAt` | process start | Local `HH:mm` the schedule is aligned to |
| `retention` | `P30D` | Backups older than this are deleted |
| `status.host` / `status.port` | `127.0.0.1` / `8080` | Status endpoint; port `0` disables it |

## Behaviour

- **Schedule**: runs at `anchor + k * interval`. The anchor is today's
  `firstRunAt`, or process start if unset, so with no `firstRunAt` the first
  backup happens one interval after startup. Runs never overlap; slots missed
  while a run was in progress are skipped, not queued.
- **Archive**: each directory appears under its own name (`/var/lib/myapp` →
  `myapp/...`). Symlinks are stored as links, never followed. Sockets, FIFOs
  and devices are skipped. A file that shrinks while being read is zero-padded
  so the archive stays valid (a warning is logged). The tarball is built in the
  system temp directory with owner-only permissions and deleted after the run.
- **Upload**: key `<prefix>backup-<yyyyMMddTHHmmssZ>.tar.gz`. Files over 32 MiB
  use multipart upload, which is aborted on failure.
- **Retention**: after a successful upload, objects under the prefix whose
  name is *exactly* this tool's backup name with a timestamp older than
  `now - retention` are deleted. Other objects in the bucket are never touched.
  If the upload fails, nothing is deleted.
- **Log**: one line per run on stderr, e.g.
  `2026-10-23T02:00:04.512+0000 INFO backup ok key=host1/backup-20261023T020000Z.tar.gz bytes=1048576 deleted=1 took=4512ms`
- **Status**: `GET /status` returns JSON:

  ```json
  {"lastRun":{"started":"2026-10-23T02:00:00Z","finished":"2026-10-23T02:00:04.512Z",
   "result":"success","key":"host1/backup-20261023T020000Z.tar.gz","bytes":1048576,
   "deleted":1,"error":null},"runningSince":null,"nextRun":"2026-10-24T02:00:00Z"}
  ```

  `lastRun` is `null` until the first run finishes. The status is held in memory and
  resets on restart. It binds to `127.0.0.1` by default and has no
  authentication, so only expose it on a trusted network.
- **Shutdown**: on SIGTERM a backup in progress is interrupted. Its temp file
  is removed, and the backup is not completed.

## Tests

`mvn test` covers archive contents and symlink handling, upload and retention
against an in-memory store (including that unrelated keys survive and that a failed
upload skips retention), key parsing, schedule arithmetic, config validation,
and the HTTP endpoint. The S3 client itself is not exercised. To test against a
real store, run `--now` against a local MinIO.
