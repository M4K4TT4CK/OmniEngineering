# backup-uploader

A long-running Python process that, on a cron schedule:

1. Archives the configured directories into a `.tar.gz`.
2. Uploads it to an S3-compatible bucket.
3. Deletes archives in the bucket older than the retention period.

It writes one log line per run, serves the last run's time and result over HTTP, and can run a single backup on demand.

## Files

- `backup_uploader.py`: the program.
- `test_backup_uploader.py`: pytest suite. It uses an in-memory fake S3 client, so no network or bucket is needed.
- `config.example.toml`: annotated configuration template.
- `requirements.txt`: dependencies (`boto3`, `croniter`, and `pytest` for tests).

## Setup

Requires Python 3.11+ (for `tomllib`).

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml   # then edit it
```

Credentials are read by boto3 from the usual places: environment variables, `~/.aws/credentials`, or an instance/IAM role.

## Usage

Run the scheduler (blocks; stops on Ctrl+C or SIGTERM):

```sh
python backup_uploader.py -c config.toml
```

Run one backup immediately and exit. The exit code is 0 on success and 1 on failure:

```sh
python backup_uploader.py -c config.toml --run-now
```

## Status endpoint

With the scheduler running, `GET http://<status.host>:<status.port>/status` returns JSON:

```json
{
  "last_run": {
    "started_at": "2026-10-22T02:00:00+00:00",
    "finished_at": "2026-10-22T02:00:41+00:00",
    "ok": true,
    "archive_key": "nightly/myapp-20261022T020000Z.tar.gz",
    "deleted_count": 1,
    "error": null
  },
  "next_run": "2026-10-23T02:00:00+00:00"
}
```

Until the first run finishes, `last_run` has `null` for `started_at`, `finished_at`, `ok`, `archive_key`, and `error`, and `0` for `deleted_count`. `/` is an alias for `/status`.

## Tests

```sh
pytest -q
```

## Behaviour notes

- **Schedule is UTC.** Cron expressions are evaluated in UTC. Missed runs (for example, while the process was down) are not caught up.
- **Retention uses the timestamp in the name,** not the object's `LastModified`. Only keys matching `<name_prefix>-<YYYYmmddTHHMMSSZ>.tar.gz` directly under `prefix` are ever deleted, so other objects in the bucket are left alone.
- **Upload then prune.** If the upload succeeds but pruning fails, the run is recorded as failed and the error explains why. The new archive is already in the bucket.
- **Local temp files** are written to `work_dir` and removed after each run, including failed runs.
- **Status endpoint is unauthenticated** and binds to `127.0.0.1` by default. Change `status.host` only if you need remote access, and put it behind something that authenticates.
- Each run produces one archive of all configured directories. It is written to `work_dir` before upload, so that filesystem needs room for one compressed archive.
