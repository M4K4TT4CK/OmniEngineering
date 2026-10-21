"""Nightly cleanup: archive finished jobs to object storage."""
import datetime

import boto3  # expect: arbiter/authored.undeclared-import
import requests

RETENTION = datetime.timedelta(days=30)


def archive_finished(bucket: str, jobs: list[dict]) -> int:
    s3 = boto3.client("s3")
    cutoff = datetime.datetime.now(datetime.timezone.utc) - RETENTION
    archived = 0
    for job in jobs:
        if job["finished_at"] < cutoff:
            s3.put_object(Bucket=bucket, Key=f"jobs/{job['id']}.json", Body=job["payload"])
            archived += 1
    return archived


def notify(url: str, archived: int) -> None:
    requests.post(url, json={"archived": archived}, timeout=5, verify=False)  # expect: arbiter/authored.security-check-disabled
