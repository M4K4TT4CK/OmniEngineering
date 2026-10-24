# backup-uploader

A long-running Node.js process that, on a cron schedule, archives the configured
directories into a `.tar.gz`, uploads it to an S3-compatible bucket, and deletes
backups older than the retention period. It writes one JSON log line per run and
serves the last run's result over HTTP.

## Usage

```sh
npm install
npm run build
cp config.example.json config.json   # then edit it
node dist/main.js --config config.json   # run on the schedule
node dist/main.js --config config.json --now   # one backup now, then exit
```

`--now` exits with code 0 on success and 1 on failure, so it works from scripts.

## Configuration

| Key | Required | Default | Meaning |
| --- | --- | --- | --- |
| `schedule` | yes | | Cron expression (5 or 6 fields), e.g. `0 3 * * *` |
| `timezone` | no | system zone | IANA zone for the schedule, e.g. `UTC` |
| `directories` | yes | | Absolute paths to include |
| `retentionDays` | yes | | Delete backups older than this many days |
| `s3.bucket` | yes | | Bucket name |
| `s3.prefix` | no | `""` | Key prefix, e.g. `hosts/web-1/` |
| `s3.region` | no | `us-east-1` | Region |
| `s3.endpoint` | no | AWS | Endpoint URL for MinIO, R2, Wasabi, etc. |
| `s3.forcePathStyle` | no | `false` | Use path-style URLs (usually `true` for MinIO) |
| `status.host` | no | `127.0.0.1` | Status server bind address |
| `status.port` | no | `8080` | Status server port |

Credentials are **not** read from the config file. The AWS SDK's standard chain
supplies them: `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, `AWS_PROFILE`, or an
instance/task role.

## Behaviour

- Backups are named `<prefix>backup-<ISO timestamp>.tar.gz`. Paths inside the archive
  are stored as absolute paths without the leading `/` (e.g. `etc/hosts`), so
  restore with `tar -xzf backup-….tar.gz -C /` or into a scratch directory.
- The archive goes to a temporary file first and is uploaded once it is complete
  (multipart for large files). The temp file is always deleted afterwards.
- Retention only runs after a successful upload. It only deletes keys directly
  under the prefix that match the backup naming pattern. Other objects in the
  bucket are never deleted.
- Unreadable files or files that disappear mid-run show up as warnings, and the
  run still succeeds. A missing configured directory fails the run.
- A scheduled run that fires while the previous run is still going is skipped.
- On `SIGINT`/`SIGTERM` the process stops scheduling, waits for a run in progress
  to finish, then exits.

## Logs

Each run writes one JSON line: to stdout on success, to stderr on failure.

```json
{"time":"2026-10-22T03:00:04.512Z","event":"backup","ok":true,"key":"hosts/web-1/backup-2026-10-22T03-00-00.001Z.tar.gz","bytes":1048576,"durationMs":4511,"deleted":1,"warnings":0}
```

## Status endpoint

`GET /status` (or `/`) returns:

```json
{
  "running": false,
  "nextRun": "2026-10-23T03:00:00.000Z",
  "lastRun": { "startedAt": "…", "finishedAt": "…", "durationMs": 4511, "ok": true, "key": "…", "bytes": 1048576, "deleted": ["…"], "warnings": [] }
}
```

`lastRun` is `null` until the first run since the process started. The status
server binds to localhost by default.

## Tests

```sh
npm test
```

The tests use an in-memory object store and cover config validation, key
naming, archiving and extracting, retention, failure handling, run
serialisation and the status endpoint. Nothing in them talks to a real bucket.
