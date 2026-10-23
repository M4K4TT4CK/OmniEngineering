"""Thin client for the upstream user service."""
import itertools
import random
import time
import urllib.request

import httpx
import requests

BASE = "https://users.example.internal"
TIMEOUT = (3.05, 10)


def fetch_user(user_id: str) -> dict:
    resp = requests.get(f"{BASE}/users/{user_id}", timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def post_event(payload: dict) -> int:
    resp = requests.post(f"{BASE}/events", json=payload, timeout=TIMEOUT)
    return resp.status_code


def forward(method: str, path: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", TIMEOUT)
    return requests.request(method, f"{BASE}{path}", **kwargs)


def legacy_fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=10) as fh:
        return fh.read()


def make_client() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=httpx.Timeout(10.0, connect=3.0))


def make_default_client() -> httpx.AsyncClient:
    # httpx's own 5 s default applies when timeout is not passed at all.
    return httpx.AsyncClient(base_url=BASE)


def fetch_with_retry(url: str, attempts: int = 5) -> requests.Response:
    for attempt in itertools.count(1):
        try:
            return requests.get(url, timeout=TIMEOUT)
        except requests.ConnectionError:
            if attempt >= attempts:
                raise
            time.sleep(min(2 ** attempt, 30) * random.uniform(0.5, 1.5))


def wait_for_healthy(url: str, deadline: float = 60.0) -> None:
    healthy = False
    started = time.monotonic()
    while not healthy:
        try:
            requests.get(f"{url}/health", timeout=2).raise_for_status()
            healthy = True
        except requests.RequestException:
            if time.monotonic() - started > deadline:
                raise
            time.sleep(1.0)
