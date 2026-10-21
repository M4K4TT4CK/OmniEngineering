"""Load job payloads and configuration from disk and from the queue."""
import marshal
import pickle

import yaml


def load_job(blob: bytes) -> dict:
    return pickle.loads(blob)  # expect: arbiter/python.unsafe-deserialization


def load_config(path: str) -> dict:
    with open(path) as fh:
        return yaml.load(fh)  # expect: arbiter/python.unsafe-deserialization


def load_profile(path: str) -> dict:
    with open(path) as fh:
        return yaml.load(fh, Loader=yaml.FullLoader)  # expect: arbiter/python.unsafe-deserialization


def load_cache(fh) -> dict:
    return marshal.load(fh)  # expect: arbiter/python.unsafe-deserialization
