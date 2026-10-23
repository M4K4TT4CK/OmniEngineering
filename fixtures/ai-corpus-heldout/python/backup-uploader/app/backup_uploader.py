#!/usr/bin/env python3
"""Scheduled backup uploader.

Archives configured directories into a gzipped tarball, uploads it to an
S3-compatible bucket, and deletes archives older than the retention period.
A small HTTP endpoint reports the last run; --run-now performs one backup and exits.
"""

import argparse
import datetime as dt
import json
import logging
import os
import re
import signal
import tarfile
import threading
import tomllib
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import boto3
from croniter import croniter

log = logging.getLogger("backup_uploader")

ARCHIVE_TIME_FORMAT = "%Y%m%dT%H%M%SZ"


@dataclass(frozen=True)
class Config:
    cron: str
    directories: list[Path]
    name_prefix: str
    work_dir: Path
    bucket: str
    prefix: str
    endpoint_url: str | None
    region: str | None
    retention_days: int
    status_host: str
    status_port: int
    log_file: str | None


@dataclass
class RunResult:
    started_at: str | None = None
    finished_at: str | None = None
    ok: bool | None = None
    archive_key: str | None = None
    deleted_count: int = 0
    error: str | None = None


class Status:
    """Thread-safe record of the last run and the next scheduled run."""

    def __init__(self):
        self._lock = threading.Lock()
        self._last_run = RunResult()
        self._next_run: str | None = None

    def record(self, result):
        with self._lock:
            self._last_run = result

    def set_next_run(self, when):
        with self._lock:
            self._next_run = when

    def snapshot(self):
        with self._lock:
            return {"last_run": asdict(self._last_run), "next_run": self._next_run}


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def iso(when):
    return when.isoformat(timespec="seconds")


def load_config(path):
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    schedule = raw["schedule"]
    archive = raw["archive"]
    storage = raw["storage"]
    status = raw.get("status", {})
    logging_cfg = raw.get("logging", {})

    if not croniter.is_valid(schedule["cron"]):
        raise ValueError(f"invalid cron expression: {schedule['cron']!r}")

    directories = [Path(d) for d in archive["directories"]]
    if not directories:
        raise ValueError("archive.directories must not be empty")
    for directory in directories:
        if not directory.is_dir():
            raise ValueError(f"backup directory does not exist: {directory}")

    retention_days = int(storage["retention_days"])
    if retention_days < 1:
        raise ValueError("storage.retention_days must be at least 1")

    # Normalise so keys are always "<prefix>/<name>" and an empty prefix stays empty.
    prefix = storage.get("prefix", "")
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    return Config(
        cron=schedule["cron"],
        directories=directories,
        name_prefix=archive.get("name_prefix", "backup"),
        work_dir=Path(archive.get("work_dir", "/var/tmp/backup-uploader")),
        bucket=storage["bucket"],
        prefix=prefix,
        endpoint_url=storage.get("endpoint_url"),
        region=storage.get("region"),
        retention_days=retention_days,
        status_host=status.get("host", "127.0.0.1"),
        status_port=int(status.get("port", 8080)),
        log_file=logging_cfg.get("file"),
    )


def build_archive(directories, name_prefix, work_dir, now):
    """Write a .tar.gz of the directories into work_dir and return its path."""
    work_dir.mkdir(parents=True, exist_ok=True)
    final = work_dir / f"{name_prefix}-{now.strftime(ARCHIVE_TIME_FORMAT)}.tar.gz"
    partial = work_dir / (final.name + ".part")
    try:
        with tarfile.open(partial, "w:gz") as tar:
            for directory in directories:
                # Keep the full path inside the archive so same-named dirs don't collide.
                tar.add(directory, arcname=str(directory).lstrip("/"))
        partial.rename(final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return final


def parse_archive_time(key, prefix, name_prefix):
    """Return the timestamp encoded in an archive key, or None if it isn't one of ours."""
    if not key.startswith(prefix):
        return None
    pattern = rf"{re.escape(name_prefix)}-(\d{{8}}T\d{{6}}Z)\.tar\.gz"
    match = re.fullmatch(pattern, key[len(prefix):])
    if not match:
        return None
    taken = dt.datetime.strptime(match.group(1), ARCHIVE_TIME_FORMAT)
    return taken.replace(tzinfo=dt.timezone.utc)


def prune_old_backups(s3, bucket, prefix, name_prefix, retention_days, now):
    """Delete archives whose encoded timestamp is older than the retention period."""
    cutoff = now - dt.timedelta(days=retention_days)
    deleted = 0
    kwargs = {"Bucket": bucket, "Prefix": prefix}
    while True:
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            taken = parse_archive_time(obj["Key"], prefix, name_prefix)
            if taken and taken < cutoff:
                s3.delete_object(Bucket=bucket, Key=obj["Key"])
                deleted += 1
        if not resp.get("IsTruncated"):
            break
        kwargs["ContinuationToken"] = resp["NextContinuationToken"]
    return deleted


def run_backup(cfg, s3, status):
    """Run one backup cycle: archive, upload, prune. Never raises; records the outcome."""
    started = utcnow()
    result = RunResult(started_at=iso(started))
    archive = None
    try:
        archive = build_archive(cfg.directories, cfg.name_prefix, cfg.work_dir, started)
        key = cfg.prefix + archive.name
        s3.upload_file(str(archive), cfg.bucket, key)
        result.archive_key = key
        result.deleted_count = prune_old_backups(
            s3, cfg.bucket, cfg.prefix, cfg.name_prefix, cfg.retention_days, utcnow()
        )
        result.ok = True
        log.info(
            "backup ok: key=%s deleted=%d started=%s",
            key, result.deleted_count, result.started_at,
        )
    except Exception as exc:
        result.ok = False
        result.error = f"{type(exc).__name__}: {exc}"
        log.exception("backup failed: started=%s", result.started_at)
    finally:
        if archive is not None and archive.exists():
            archive.unlink()
        result.finished_at = iso(utcnow())
        status.record(result)
    return result


def next_run_after(cron, now):
    return croniter(cron, now).get_next(dt.datetime)


def run_forever(cfg, s3, status, stop):
    while not stop.is_set():
        upcoming = next_run_after(cfg.cron, utcnow())
        status.set_next_run(iso(upcoming))
        log.info("next backup scheduled for %s", iso(upcoming))
        wait_seconds = (upcoming - utcnow()).total_seconds()
        if stop.wait(max(wait_seconds, 0)):
            break
        run_backup(cfg, s3, status)


def make_handler(status):
    class StatusHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] not in ("/", "/status"):
                self.send_error(404)
                return
            body = json.dumps(status.snapshot(), indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            log.debug(format, *args)

    return StatusHandler


def serve_status(host, port, status):
    server = ThreadingHTTPServer((host, port), make_handler(status))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def setup_logging(log_file):
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scheduled backup uploader to S3.")
    parser.add_argument("-c", "--config", default="config.toml", help="path to config file")
    parser.add_argument(
        "--run-now", action="store_true", help="run one backup immediately and exit"
    )
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(cfg.log_file)
    s3 = boto3.client("s3", endpoint_url=cfg.endpoint_url, region_name=cfg.region)
    status = Status()

    if args.run_now:
        result = run_backup(cfg, s3, status)
        return 0 if result.ok else 1

    server = serve_status(cfg.status_host, cfg.status_port, status)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    log.info("status endpoint on http://%s:%d/status", cfg.status_host, cfg.status_port)
    try:
        run_forever(cfg, s3, status, stop)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
