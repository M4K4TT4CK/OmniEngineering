import datetime as dt
import json
import tarfile
import urllib.request
from pathlib import Path

import pytest

import backup_uploader as bu


class FakeS3:
    """In-memory stand-in for the boto3 S3 client methods the uploader uses."""

    def __init__(self, objects=None, page_size=2):
        self.objects = dict(objects or {})
        self.uploads = []
        self.fail_upload = None
        self.page_size = page_size

    def upload_file(self, filename, bucket, key):
        if self.fail_upload:
            raise self.fail_upload
        self.uploads.append((bucket, key))
        self.objects[key] = Path(filename).read_bytes()

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + self.page_size]
        more = start + self.page_size < len(keys)
        resp = {"Contents": [{"Key": k} for k in page], "IsTruncated": more}
        if more:
            resp["NextContinuationToken"] = str(start + self.page_size)
        return resp

    def delete_object(self, Bucket, Key):
        del self.objects[Key]


def make_config(tmp_path, **overrides):
    source = tmp_path / "data"
    source.mkdir()
    (source / "a.txt").write_text("hello")
    values = dict(
        cron="0 2 * * *",
        directories=[source],
        name_prefix="backup",
        work_dir=tmp_path / "work",
        bucket="bucket",
        prefix="nightly/",
        endpoint_url=None,
        region=None,
        retention_days=30,
        status_host="127.0.0.1",
        status_port=0,
        log_file=None,
    )
    values.update(overrides)
    return bu.Config(**values)


def test_build_archive_contains_directory_contents(tmp_path):
    source = tmp_path / "data"
    source.mkdir()
    (source / "a.txt").write_text("hello")
    now = dt.datetime(2026, 10, 22, 2, 0, tzinfo=dt.timezone.utc)

    archive = bu.build_archive([source], "backup", tmp_path / "work", now)

    assert archive.name == "backup-20261022T020000Z.tar.gz"
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert any(name.endswith("data/a.txt") for name in names)


def test_build_archive_removes_partial_file_on_failure(tmp_path):
    now = dt.datetime(2026, 10, 22, 2, 0, tzinfo=dt.timezone.utc)
    work = tmp_path / "work"

    with pytest.raises(FileNotFoundError):
        bu.build_archive([tmp_path / "missing"], "backup", work, now)

    assert list(work.iterdir()) == []


def test_prune_deletes_only_expired_backups_with_matching_names():
    now = dt.datetime(2026, 10, 22, 2, tzinfo=dt.timezone.utc)
    fake = FakeS3({
        "nightly/backup-20260101T020000Z.tar.gz": b"",        # expired: delete
        "nightly/backup-20261020T020000Z.tar.gz": b"",        # recent: keep
        "nightly/backup-20260101T020000Z.txt": b"",           # wrong suffix: keep
        "nightly/other-20260101T020000Z.tar.gz": b"",         # other name prefix: keep
        "nightly/notes.txt": b"",                             # unrelated: keep
        "elsewhere/backup-20260101T020000Z.tar.gz": b"",      # outside prefix: keep
    })

    deleted = bu.prune_old_backups(fake, "bucket", "nightly/", "backup", 30, now)

    assert deleted == 1
    assert "nightly/backup-20260101T020000Z.tar.gz" not in fake.objects
    assert len(fake.objects) == 5


def test_run_backup_uploads_archive_and_cleans_up(tmp_path):
    cfg = make_config(tmp_path)
    fake = FakeS3()
    status = bu.Status()

    result = bu.run_backup(cfg, fake, status)

    assert result.ok is True
    assert result.error is None
    assert len(fake.uploads) == 1
    bucket, key = fake.uploads[0]
    assert bucket == "bucket"
    assert key.startswith("nightly/backup-") and key.endswith(".tar.gz")
    assert result.archive_key == key
    assert list(cfg.work_dir.iterdir()) == []
    assert status.snapshot()["last_run"]["ok"] is True


def test_run_backup_records_failure_and_cleans_up(tmp_path):
    cfg = make_config(tmp_path)
    fake = FakeS3()
    fake.fail_upload = OSError("network down")
    status = bu.Status()

    result = bu.run_backup(cfg, fake, status)

    assert result.ok is False
    assert "network down" in result.error
    assert fake.uploads == []
    assert list(cfg.work_dir.iterdir()) == []
    assert status.snapshot()["last_run"]["error"] == result.error


def test_next_run_after_follows_cron():
    now = dt.datetime(2026, 10, 22, 1, 30, tzinfo=dt.timezone.utc)

    nxt = bu.next_run_after("0 2 * * *", now)

    assert nxt == dt.datetime(2026, 10, 22, 2, 0, tzinfo=dt.timezone.utc)


def test_load_config_normalises_prefix(tmp_path):
    source = tmp_path / "data"
    source.mkdir()
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        f'''
[schedule]
cron = "0 2 * * *"

[archive]
directories = ["{source.as_posix()}"]

[storage]
bucket = "b"
prefix = "nightly"
retention_days = 7
'''
    )

    cfg = bu.load_config(config_file)

    assert cfg.prefix == "nightly/"
    assert cfg.retention_days == 7
    assert cfg.status_port == 8080


def test_load_config_rejects_invalid_cron(tmp_path):
    source = tmp_path / "data"
    source.mkdir()
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        f'''
[schedule]
cron = "not a cron"

[archive]
directories = ["{source.as_posix()}"]

[storage]
bucket = "b"
retention_days = 7
'''
    )

    with pytest.raises(ValueError, match="invalid cron"):
        bu.load_config(config_file)


def test_status_endpoint_reports_last_run():
    status = bu.Status()
    status.record(bu.RunResult(started_at="2026-01-01T02:00:00+00:00", ok=True))
    server = bu.serve_status("127.0.0.1", 0, status)
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status") as resp:
            body = json.loads(resp.read())
    finally:
        server.shutdown()
        server.server_close()

    assert body["last_run"]["ok"] is True
    assert body["last_run"]["started_at"] == "2026-01-01T02:00:00+00:00"
