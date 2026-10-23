"""Thin client for the upstream user service."""
import itertools
import urllib.request

import httpx
import requests

BASE = "https://users.example.internal"


def fetch_user(user_id: str) -> dict:
    resp = requests.get(f"{BASE}/users/{user_id}")  # expect: arbiter/python.http-no-timeout
    resp.raise_for_status()
    return resp.json()


def post_event(payload: dict) -> int:
    resp = requests.post(f"{BASE}/events", json=payload, timeout=None)  # expect: arbiter/python.http-timeout-disabled
    return resp.status_code


def legacy_fetch(url: str) -> bytes:
    with urllib.request.urlopen(url) as fh:  # expect: arbiter/python.http-no-timeout
        return fh.read()


def make_client() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=None)  # expect: arbiter/python.http-timeout-disabled


def fetch_with_retry(url: str) -> requests.Response:
    for attempt in itertools.count(1):  # expect: arbiter/python.retry-loop-no-backoff
        try:
            return requests.get(url, timeout=5)
        except requests.ConnectionError:
            continue


def wait_for_healthy(url: str) -> None:
    healthy = False
    while not healthy:  # expect: arbiter/python.retry-loop-no-backoff
        try:
            requests.get(f"{url}/health", timeout=2).raise_for_status()
            healthy = True
        except requests.RequestException:
            continue
