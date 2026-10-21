"""Load job payloads and configuration from disk and from the queue."""
import hashlib
import hmac
import json

import yaml


def load_job(blob: bytes, signature: str, key: bytes) -> dict:
    expected = hmac.new(key, blob, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError("job payload signature mismatch")
    return json.loads(blob)


def load_config(path: str) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def load_profile(path: str) -> dict:
    with open(path) as fh:
        return yaml.load(fh, Loader=yaml.SafeLoader)


def load_cache(fh) -> dict:
    return json.load(fh)
