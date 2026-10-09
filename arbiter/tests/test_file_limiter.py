"""The limiter shared across processes (ARB-051).

`FileRateLimiter` counts in SQLite beside the key file so that uvicorn workers,
or two instances behind one address, draw on one budget. The memory limiter's
own tests are mirrored here so the two refuse the same things with the same
words; the multi-process test is the one the memory limiter cannot pass.

The worker runs under `multiprocessing`'s spawn context on every platform, so
it must be a module-level function taking picklable arguments -- that is what
Windows requires, and running spawn everywhere means Linux cannot pass on a
fork-only accident.
"""
from __future__ import annotations

import multiprocessing
import sqlite3
import time
from pathlib import Path

import pytest

from arbiter import api
from arbiter.service import ServiceError


def _limiter(tmp_path: Path, **caps) -> api.FileRateLimiter:
    caps.setdefault("requests", 100)
    caps.setdefault("window", 3600)
    caps.setdefault("concurrent", 2)
    caps.setdefault("total", 4)
    return api.FileRateLimiter(tmp_path / "limiter.db", **caps)


# --------------------------------------------------------------------------
# The memory limiter's tests, mirrored
# --------------------------------------------------------------------------

def test_a_key_is_throttled_after_its_hourly_allowance(tmp_path):
    limiter = _limiter(tmp_path, requests=3)
    for _ in range(3):
        limiter.check("k1")
    with pytest.raises(api.RateLimited) as caught:
        limiter.check("k1")
    assert caught.value.retry_after > 0
    limiter.check("k2")


def test_the_throttle_lets_a_key_through_once_its_window_passes(tmp_path):
    limiter = _limiter(tmp_path, requests=2, window=60)
    start = 1000.0
    limiter.check("k1", now=start)
    limiter.check("k1", now=start + 1)
    with pytest.raises(api.RateLimited, match="try again in"):
        limiter.check("k1", now=start + 2)
    limiter.check("k1", now=start + 61)


def test_the_window_is_shared_by_two_limiter_objects_over_one_file(tmp_path):
    """Two objects are two processes as far as SQLite is concerned."""
    first = _limiter(tmp_path, requests=2)
    second = _limiter(tmp_path, requests=2)
    first.check("k1", now=1000.0)
    second.check("k1", now=1001.0)
    with pytest.raises(api.RateLimited):
        first.check("k1", now=1002.0)
    with pytest.raises(api.RateLimited):
        second.check("k1", now=1002.0)


def test_one_key_cannot_run_unlimited_scans_at_once(tmp_path):
    limiter = _limiter(tmp_path, concurrent=2)
    with limiter.slot("k1"):
        with limiter.slot("k1"):
            with pytest.raises(api.RateLimited, match="already has 2 scans"):
                with limiter.slot("k1"):
                    pass
            with limiter.slot("k2"):
                pass
    with limiter.slot("k1"):
        pass
    assert limiter.running() == 0


def test_the_whole_server_has_a_scan_ceiling_of_its_own(tmp_path):
    limiter = _limiter(tmp_path, concurrent=2, total=3)
    with limiter.slot("k1"), limiter.slot("k1"), limiter.slot("k2"):
        with pytest.raises(api.RateLimited, match="limit of 3 scans"):
            with limiter.slot("k3"):
                pass
    with limiter.slot("k3"):
        pass


def test_a_key_over_its_own_share_is_told_that_not_that_we_are_busy(tmp_path):
    limiter = _limiter(tmp_path, concurrent=1, total=4)
    with limiter.slot("k1"):
        with pytest.raises(api.RateLimited, match="this key already has"):
            with limiter.slot("k1"):
                pass


def test_a_slot_is_released_when_the_body_raises(tmp_path):
    limiter = _limiter(tmp_path, concurrent=1)
    with pytest.raises(RuntimeError):
        with limiter.slot("k1"):
            raise RuntimeError("the scan blew up")
    assert limiter.running("k1") == 0
    with limiter.slot("k1"):
        pass


# --------------------------------------------------------------------------
# What only a shared limiter has to get right
# --------------------------------------------------------------------------

def test_a_slot_left_by_a_crashed_process_expires(tmp_path):
    """A process killed mid-scan never deletes its row. Past the scan ceiling
    the row belongs to nobody who is still running, and must not hold the
    cap forever."""
    victim = _limiter(tmp_path, concurrent=1, total=1, slot_ttl=5)
    victim._acquire("k1", now=1000.0)   # taken, never released
    survivor = _limiter(tmp_path, concurrent=1, total=1, slot_ttl=5)
    with pytest.raises(api.RateLimited):
        survivor._acquire("k1", now=1004.0)
    with pytest.raises(api.RateLimited):
        survivor._acquire("k2", now=1004.0)   # the total cap is held too
    assert survivor.running(now=1004.0) == 1
    token = survivor._acquire("k1", now=1006.0)
    survivor._release(token)
    with sqlite3.connect(tmp_path / "limiter.db") as db:
        assert db.execute("SELECT COUNT(*) FROM slots").fetchone()[0] == 0


def test_every_decision_is_one_immediate_transaction(tmp_path, monkeypatch):
    """The whole point: the read and the write of a decision happen under one
    write lock, so two processes cannot both see one slot left and both take
    it. Checked by watching what the connection is asked to do."""
    limiter = _limiter(tmp_path)
    seen: list[str] = []

    class Spy(sqlite3.Connection):
        def execute(self, sql, *args):
            word = sql.strip().split()[0].upper()
            seen.append(word + (" IMMEDIATE" if "IMMEDIATE" in sql else ""))
            return super().execute(sql, *args)

    monkeypatch.setattr(limiter, "_connect", lambda: sqlite3.connect(
        limiter.path, timeout=30, isolation_level=None, factory=Spy))
    limiter.check("k1")
    with limiter.slot("k1"):
        pass
    assert seen.count("BEGIN IMMEDIATE") == 2
    assert seen.index("BEGIN IMMEDIATE") < seen.index("SELECT") < seen.index("INSERT")


def _worker(db_path, key, wanted, cap, counter, peak, lock, done):
    """Take `wanted` slots one after another, recording how many are held at
    once across every process. Module-level and picklable: spawn needs it."""
    limiter = api.FileRateLimiter(db_path, requests=10_000, window=3600,
                                  concurrent=cap, total=cap)
    taken = 0
    deadline = time.monotonic() + 20
    while taken < wanted and time.monotonic() < deadline:
        try:
            with limiter.slot(key):
                with lock:
                    counter.value += 1
                    if counter.value > peak.value:
                        peak.value = counter.value
                time.sleep(0.03)
                with lock:
                    counter.value -= 1
                taken += 1
        except api.RateLimited:
            time.sleep(0.005)
    with lock:
        done.value += taken


def test_several_processes_never_exceed_the_cap_between_them(tmp_path):
    """Six processes against a total cap of two: the most ever held at once
    is two, and every process still gets its turn."""
    ctx = multiprocessing.get_context("spawn")
    counter, peak, done = ctx.Value("i", 0), ctx.Value("i", 0), ctx.Value("i", 0)
    lock = ctx.Lock()
    cap, procs, each = 2, 6, 3
    db_path = tmp_path / "limiter.db"
    api.FileRateLimiter(db_path)   # create the schema once, before the race
    workers = [ctx.Process(target=_worker,
                           args=(db_path, f"k{i}", each, cap, counter, peak, lock, done))
               for i in range(procs)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=25)
    assert all(w.exitcode == 0 for w in workers), [w.exitcode for w in workers]
    assert done.value == procs * each, "every process should have had its turns"
    assert 0 < peak.value <= cap, f"observed {peak.value} concurrent holds; cap is {cap}"
    assert api.FileRateLimiter(db_path).running() == 0


# --------------------------------------------------------------------------
# Selection: the configured limiter is the one every front door uses
# --------------------------------------------------------------------------

def test_configure_limiter_puts_the_file_beside_the_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "LIMITER", api.RateLimiter())
    keys = tmp_path / "keys" / "keys.json"
    installed = api.configure_limiter("file", keys)
    assert installed is api.LIMITER
    assert isinstance(installed, api.FileRateLimiter)
    assert installed.path == keys.parent / "limiter.db"
    assert installed.path.exists()
    assert isinstance(api.configure_limiter("memory"), api.RateLimiter)
    with pytest.raises(ServiceError, match="unknown limiter"):
        api.configure_limiter("redis")


def test_mcp_dispatch_draws_on_the_configured_limiter(tmp_path, monkeypatch):
    """`dispatch` reads `api.LIMITER` at call time, so switching the limiter
    reaches the MCP door without the MCP code knowing which kind it is."""
    from arbiter import mcp
    monkeypatch.setattr(api, "LIMITER", api.RateLimiter())
    limiter = api.configure_limiter("file", tmp_path / "keys.json", concurrent=1)
    monkeypatch.setitem(mcp.HANDLERS, "arbiter_scan", lambda **kw: {"ran": True})
    caller = {"id": "abc123", "user": "dana"}
    log = api.AuditLog(tmp_path / "audit.log")
    assert mcp.dispatch("arbiter_scan", {"target": "r", "output_dir": "o"},
                        caller=caller, audit=log, root=tmp_path / "root") == {"ran": True}
    # Another process holding this key's one slot is visible through the file.
    other = api.FileRateLimiter(limiter.path, concurrent=1)
    token = other._acquire("abc123")
    try:
        with pytest.raises(api.RateLimited):
            mcp.dispatch("arbiter_scan", {"target": "r", "output_dir": "o"},
                         caller=caller, audit=log, root=tmp_path / "root")
    finally:
        other._release(token)
    assert limiter.running() == 0


def test_the_hosted_api_counts_through_the_file_when_told_to(tmp_path, monkeypatch):
    """The 429 comes from the shared counts, proven by a second limiter object
    over the same file seeing the requests the application recorded."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx2", reason="starlette's test client needs httpx2")
    from fastapi.testclient import TestClient

    keys = tmp_path / "keys.json"
    raw, record = api.mint_key("dana@acme.example", keys)
    monkeypatch.setattr(api, "LIMITER", api.RateLimiter())
    api.configure_limiter("file", keys, requests=2)
    app = api.create_app(keys, audit=api.AuditLog(tmp_path / "audit.log"))
    with TestClient(app, base_url="https://testserver") as client:
        for _ in range(2):
            assert client.get("/v1/health").status_code == 200
        body = {"report": {"findings": []}}
        ok = [client.post("/v1/review-queue", json=body, headers={"X-API-Key": raw})
              for _ in range(2)]
        assert all(r.status_code == 200 for r in ok), [r.text for r in ok]
        limited = client.post("/v1/review-queue", json=body, headers={"X-API-Key": raw})
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) > 0
    with sqlite3.connect(tmp_path / "limiter.db") as db:
        rows = db.execute("SELECT COUNT(*) FROM calls WHERE key_id = ?",
                          (record["id"],)).fetchone()[0]
    assert rows == 2


def test_the_server_commands_accept_the_limiter_choice():
    from arbiter import cli
    parser = cli.build_parser()
    assert parser.parse_args(["api", "serve"]).limiter == "memory"
    assert parser.parse_args(["api", "serve", "--limiter", "file"]).limiter == "file"
    assert parser.parse_args(["mcp", "--http", "--limiter", "file"]).limiter == "file"
    with pytest.raises(SystemExit):
        parser.parse_args(["api", "serve", "--limiter", "redis"])
