# backup-uploader

A long-running Ruby process that, on a cron schedule:

1. Tars and gzips the configured directories.
2. Uploads the archive to an S3-compatible bucket.
3. Deletes archives under the prefix older than `retention_days`.
4. Logs one line per run and serves the last run's result at `GET /status`.

Requires Ruby 3.1+.

## Setup

```sh
bundle install
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
```

Edit `config.yml` to set the schedule, directories, bucket, and endpoint.

## Usage

```sh
# Run on the schedule (blocks; Ctrl-C to stop)
bundle exec ruby backup_uploader.rb --config config.yml

# Run one backup now and exit (0 on success, 1 on failure)
bundle exec ruby backup_uploader.rb --config config.yml --once
```

Status endpoint (loopback only):

```sh
curl http://127.0.0.1:8080/status
```

It returns JSON with `status` (`ok`, `error`, or `never_run`), `started_at`,
`finished_at`, and on success `key`, `size_bytes`, and `deleted`; on failure, `error`.

## Tests

```sh
bundle exec ruby test_backup_uploader.rb
```

The tests use the AWS SDK's stubbed client, so they need no network or credentials.

## Notes

- Symlinks inside configured directories are skipped.
- Only keys under `prefix` that end in `.tar.gz` are ever deleted.
- Runs are serialized; a scheduled run that fires during a manual run waits for it.
