# backup-uploader

A long-running Go service that periodically archives configured directories into
a `.tar.gz`, uploads it to an S3-compatible bucket, and deletes its own backups
older than the retention period. It logs one line per run and serves the last
run's result as JSON.

## Files

- `main.go` – flags, scheduler loop, HTTP status endpoint
- `backup.go` – config loading, archiving, the runner (upload and prune)
- `store.go` – S3-compatible object store wrapper (MinIO client)
- `backup_test.go` – tests with an in-memory fake store
- `go.mod`

## Setup

The dependency is not pinned in `go.mod`. Run this once to fetch it and create `go.sum`:

```sh
go mod tidy
```

## Config

```json
{
  "listen": ":8080",
  "interval": "6h",
  "retention_days": 30,
  "work_dir": "/var/tmp",
  "sources": ["/srv/data/projects", "/etc/myapp"],
  "s3": {
    "endpoint": "s3.us-east-1.amazonaws.com",
    "region": "us-east-1",
    "bucket": "my-backups",
    "prefix": "nightly",
    "secure": true
  }
}
```

- `interval` – Go duration (`30m`, `6h`, `24h`). The first backup runs one interval after start.
- `retention_days` – backups in the prefix older than this are deleted after each successful upload.
- `listen` – defaults to `:8080`. `work_dir` defaults to the OS temp directory and must have room for one archive.
- `s3.prefix` – a trailing `/` is added if missing. Only objects named `backup-*.tar.gz` under the prefix are ever deleted.
- Unknown config keys are an error.

Credentials are read from the environment:

```sh
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
```

## Usage

```sh
go build -o backup-uploader .

# Run as a service
./backup-uploader -config backup.json

# Run one backup now and exit (exit status 1 on failure)
./backup-uploader -config backup.json -once

# Check the last run
curl http://localhost:8080/status
```

`/status` returns, for example:

```json
{"last_run":{"started_at":"...","finished_at":"...","ok":true,"key":"nightly/backup-20261023T030000Z.tar.gz","bytes":10485760,"pruned":1}}
```

Before the first run, `last_run` is `null`. A failed run has `"ok": false` and an `error` field.

Ctrl-C or SIGTERM stops the scheduler and HTTP server after any in-progress backup finishes.

## Notes

- Archive names are absolute source paths without the leading `/`, so two sources with the same base name do not collide.
- Symlinks are stored as links, not followed. Sockets, devices, and pipes are skipped.
- Any unreadable file aborts the run. The run is recorded as failed and nothing is uploaded.
- Schedules are fixed intervals, not cron expressions.

## Tests

```sh
go test ./...
```

The tests cover archive contents, the missing-source error path, pruning (only old backup-pattern objects under the prefix are deleted), success and failure recording, the status handler, and config validation. They do not contact a real bucket.
