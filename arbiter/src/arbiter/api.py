"""The hosted front door: Arbiter as something a customer calls, not installs.

This is the second surface over `service.py`, alongside `mcp.py`. It decides
nothing about containment -- workspaces, archive safety, path limits and profile
limits all live in the service layer, so they are written once.

What belongs here instead is everything that only matters when the caller is
remote and unknown: who they are, how much they may send, and what is kept
afterwards.

## Access is handed out by hand, one key per user

There is no sign-up, no billing and no self-service, and nothing here charges
anybody. The owner mints a key with `arbiter api key add --user "..."` and sends
it to somebody he knows. Issuing by hand is how access works because there is no
identity or billing system to do it any other way -- not a vetting step, and not
something a recipient has to justify. Nobody is asked what they intend to scan.

The side effect is worth keeping: something that cannot be signed up for cannot
be used at scale by a stranger.

A key is scoped to a user, and that is the whole model -- no roles, no tiers, no
per-key permissions. One user holds at most one live key, so a key identifies a
person rather than a pool. Sharing one defeats every limit here: the caps below
count per key, and revoking a shared key for the person who left also cuts off
the person who stayed. Adding somebody means minting them their own.

Keys are stored as SHA-256 hashes, never in the clear. The raw key is shown once
at mint time and cannot be recovered -- so the key file is not itself a
credential store, and losing it means reissuing rather than a breach. Each key
carries a short id derived from its hash so a key can be named in a log or
revoked without anyone writing the secret down.

## Nothing is kept

A scan writes into a workspace that is deleted when the request ends, including
when it fails. The report goes back in the response body and nowhere else. There
is no database, no object store, no log of findings.

That costs a feature: a caller cannot come back tomorrow for yesterday's report.
It buys the simplest possible answer to "what do you hold of ours?", which is
"nothing", and that answer needs no retention policy to defend.

The reason it matters more here than it would for most services: a report names
the file a credential sits in and what kind it is. `report.py` withholds the
secret's value -- `test_no_output_format_reprints_a_secret` holds it to that --
but a stored report would still be a map of where to look. Keeping none is
cheaper than guarding them.

One thing is kept, and it is about the caller rather than the code: a line per
request saying which key called, which operation, how it ended, how many bytes
arrived and how long it took. That is what answers "who ran what, and when"
after a key leaks or a bill is disputed. It holds no file name, no finding and
no fragment of the upload, because a log that quoted findings would rebuild on
disk, permanently, the thing the request path takes care to delete.

## Source arrives as an upload, and only as an upload

The server does not clone from a caller's repository, because that would mean
holding credentials to their source: a far larger thing to ask for and to
protect than a tarball for the duration of one request. Uploads are treated as
hostile input by `service.extract_archive`.

## No verdict crosses this boundary

`review_queue` returns a queue with every mark blank, exactly as the other
surfaces do. There is no endpoint that records an adjudication, and there must
never be one: `learn.record()` refuses to re-adjudicate a fingerprint, so a mark
made by an automated caller would be permanent and would replace the one signal
in the system that the system did not generate.

Whether a customer's own human adjudications should ever feed calibration is an
open question and is deliberately not answered here. If it is ever answered yes,
the thing sent back is the verdict and the rule id -- never the fingerprint,
which is derived from the file path and a snippet of their code and would leak
both.
"""
from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import sys
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from . import __version__
from .service import (
    DEFAULT_TIMEOUT,
    ServiceError,
    Workspace,
    extract_archive,
    gate,
    review_queue,
    scan,
)

# An upload larger than this is refused before anything is written to disk.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

# Stated as a constant so a test can assert it rather than trusting the prose:
# no request path writes anything that outlives the request.
RETAINS_NOTHING = True

KEY_PREFIX = "arb_"

# Ninety days. Long enough not to be a nuisance, short enough that a key leaked
# and forgotten stops working without anyone having to notice. It sits up here
# rather than with the other limits further down because `mint_key` takes it as
# a default argument, and Python evaluates those when the function is defined.
DEFAULT_KEY_LIFETIME_DAYS = 90

# Two years, and subdomains. Sent on every response so a browser or client that
# once reached us over TLS refuses to try plaintext afterwards.
HSTS_HEADER = "max-age=63072000; includeSubDomains"

# --------------------------------------------------------------------------
# Transport security, which is not optional
# --------------------------------------------------------------------------
#
# Every request to this service carries an API key in a header and a copy of
# somebody's source code in the body. Over plaintext both are readable by
# anything on the path, and the key is replayable forever. So the server does
# not offer plaintext as a degraded mode: it refuses to start without TLS, and
# refuses individual requests that arrive over it anyway.
#
# There is one arrangement, not two: this process holds a certificate and every
# socket it opens speaks TLS. A proxy may still sit in front of it, but the hop
# from the proxy to here is TLS as well. `X-Forwarded-Proto` is deliberately not
# consulted -- it is a header any client can invent, and believing it means a
# plaintext listener somewhere, which is the thing being refused.


def require_tls(scheme: str) -> None:
    """Refuse a request that did not arrive over TLS.

    Framework-free so the rule is testable without a running server. The scheme
    is the one the socket actually spoke; no header can talk it into `https`.
    """
    if (scheme or "").lower() != "https":
        raise ServiceError("plaintext HTTP is refused; use HTTPS")


def check_tls_config(certfile: str | None, keyfile: str | None) -> None:
    """Refuse to start without a certificate and key.

    Called before the socket is opened, so a misconfiguration is a startup
    failure rather than a quiet downgrade nobody notices.
    """
    if not certfile or not keyfile:
        raise ServiceError(
            "TLS is required: pass --cert and --key. A proxy in front does not "
            "remove that -- the hop from the proxy to here is a socket too, and "
            "it has to be TLS. With no certificate to hand, see docs/hosted-api.md "
            "('If you have no certificate') -- there is no plaintext mode"
        )
    for label, value in (("--cert", certfile), ("--key", keyfile)):
        if not Path(value).expanduser().is_file():
            raise ServiceError(f"{label} {value} does not exist")


# --------------------------------------------------------------------------
# Keys, issued by hand
# --------------------------------------------------------------------------

def default_key_path() -> Path:
    """Where minted keys live. `ARBITER_KEYS` overrides it for a deployment."""
    env = os.environ.get("ARBITER_KEYS")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".arbiter" / "keys.json"


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stamp(when: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when))


def _parse_stamp(value: str) -> float:
    return calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))


def key_state(record: dict, now: float | None = None) -> str:
    """`active`, `revoked` or `expired`. Used for display and for verification."""
    if record.get("revoked"):
        return "revoked"
    expires = record.get("expires")
    if expires and _parse_stamp(expires) <= (time.time() if now is None else now):
        return "expired"
    return "active"


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ServiceError(f"key file {path} is not valid JSON: {exc}") from exc
    return data.get("keys", []) if isinstance(data, dict) else []


def _store(path: Path, keys: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "keys": keys}, indent=2), encoding="utf-8")
    try:
        # Owner-only on POSIX. On Windows `chmod` only toggles the read-only
        # attribute, so the file stays world-readable there and the directory
        # has to be what restricts it. The file holds hashes rather than keys,
        # which is why this is a second line of defence and not the first.
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover - platform dependent
        pass


def active_key_for(user: str, path: Path | None = None) -> dict | None:
    """The live key a user holds, if any. One user, one key."""
    wanted = user.strip()
    for rec in _load(path or default_key_path()):
        if rec.get("user") == wanted and key_state(rec) == "active":
            return rec
    return None


def mint_key(user: str, path: Path | None = None,
             lifetime_days: int | None = DEFAULT_KEY_LIFETIME_DAYS,
             replace: bool = False) -> tuple[str, dict]:
    """Create a key for one user. Returns the raw key and its record.

    A key is scoped to a user and that is the whole model. One user holds at
    most one live key, so a key identifies a person rather than a pool: if two
    people share one, nothing downstream can tell them apart, and revoking it
    for the one who left also cuts off the one who stayed. Adding a second
    person means minting them their own.

    Minting over a live key is refused unless `replace` is set, which revokes
    the old one in the same breath -- so rotation is one deliberate act and
    never silently leaves two keys working for the same person.

    The raw key is returned once and never stored. Only its hash is written, so
    the key file cannot be used to impersonate anyone who holds a key.

    Keys expire. `lifetime_days=None` mints one that does not, which is a
    deliberate exception rather than the default: a permanent key is a permanent
    grant to whoever ends up holding it.
    """
    user = user.strip()
    if not user:
        raise ServiceError("a key is scoped to a user; name the one it is for")
    if lifetime_days is not None and lifetime_days <= 0:
        raise ServiceError("a key's lifetime must be at least one day")
    path = path or default_key_path()

    held = active_key_for(user, path)
    if held and not replace:
        raise ServiceError(
            f"{user} already holds key {held['id']}; pass --replace to rotate it, "
            "or revoke it first. One user, one key."
        )

    keys = _load(path)
    if held:
        for rec in keys:
            if rec.get("id") == held["id"]:
                rec["revoked"] = _stamp(time.time())

    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    digest = _digest(raw)
    now = time.time()
    record = {
        "id": digest[:12],
        "user": user,
        "sha256": digest,
        "created": _stamp(now),
        "expires": _stamp(now + lifetime_days * 86400) if lifetime_days else None,
        "revoked": None,
        "replaced": held["id"] if held else None,
    }
    keys.append(record)
    _store(path, keys)
    return raw, record


def list_keys(path: Path | None = None) -> list[dict]:
    """Every key, without any secret, each with its current state. Safe to print."""
    listed = []
    for rec in _load(path or default_key_path()):
        shown = {k: v for k, v in rec.items() if k != "sha256"}
        shown.setdefault("expires", None)
        shown["state"] = key_state(rec)
        listed.append(shown)
    return listed


def revoke_key(key_id: str, path: Path | None = None) -> bool:
    """Revoke by short id. Returns whether anything changed."""
    path = path or default_key_path()
    keys = _load(path)
    changed = False
    for rec in keys:
        if rec.get("id") == key_id and not rec.get("revoked"):
            rec["revoked"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            changed = True
    if changed:
        _store(path, keys)
    return changed


def verify_key(raw: str, path: Path | None = None) -> dict | None:
    """Return the key's record, or None if it is unknown or revoked.

    Compared with `hmac.compare_digest` so the time taken does not reveal how
    much of a guessed key was correct.
    """
    if not raw:
        return None
    candidate = _digest(raw)
    for rec in _load(path or default_key_path()):
        if hmac.compare_digest(rec.get("sha256", ""), candidate):
            return rec if key_state(rec) == "active" else None
    return None


# --------------------------------------------------------------------------
# Limits on a key, so holding one is not the same as owning the machine
# --------------------------------------------------------------------------
#
# These are not a tier and not a price. Nobody is being charged, and a limit
# that a friend testing their repositories can feel would be a restriction
# dressed up as capacity. Each one exists for a specific failure:
#
#   - a key that never expires and is never throttled is a permanent, unlimited
#     grant to whoever ends up holding it after a leak -- a ticket, a shell
#     history, somebody who has since moved on;
#   - a scan is expensive, unpacking an archive and running several analyzers,
#     so concurrency is what actually decides whether the machine stays up.
#
# The hourly figure is therefore set well above real use rather than near it.
# Somebody working through twenty repositories in an afternoon should never see
# it; a leaked key running flat out should.

RATE_LIMIT_REQUESTS = 120
RATE_LIMIT_WINDOW_SECONDS = 3600
MAX_CONCURRENT_SCANS = 2

# And a cap on the whole server, because the per-key one multiplies. Five
# testers with two slots each is ten concurrent scans, each unpacking an archive
# and running several analyzers, on one machine. The per-key limit protects
# testers from each other; this one protects the machine from all of them.
MAX_TOTAL_SCANS = 4


class RateLimited(ServiceError):
    """A key asked for too much. Carries how long to wait."""

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RateLimiter:
    """Per-key request and concurrency caps, plus one cap on the whole server.

    Held in memory, which is the honest scope of it: the counts do not survive a
    restart and are not shared between processes. For a manually distributed
    pilot served by one process that is sufficient. Several processes serving
    the same keys -- uvicorn workers, or two instances behind one address --
    want `FileRateLimiter` below (`--limiter file`), which counts in SQLite
    beside the key file so the budget is one budget.
    """

    def __init__(self, requests: int = RATE_LIMIT_REQUESTS,
                 window: int = RATE_LIMIT_WINDOW_SECONDS,
                 concurrent: int = MAX_CONCURRENT_SCANS,
                 total: int = MAX_TOTAL_SCANS) -> None:
        self.requests = requests
        self.window = window
        self.concurrent = concurrent
        self.total = total
        self._calls: dict[str, list[float]] = {}
        self._running: dict[str, int] = {}
        self._total_running = 0
        self._lock = threading.Lock()

    def check(self, key_id: str, now: float | None = None) -> None:
        """Record one request, or refuse it if the key is over its hourly cap."""
        now = time.time() if now is None else now
        with self._lock:
            recent = [t for t in self._calls.get(key_id, []) if now - t < self.window]
            if len(recent) >= self.requests:
                oldest = min(recent)
                wait = int(self.window - (now - oldest)) + 1
                self._calls[key_id] = recent
                raise RateLimited(
                    f"this key has made {self.requests} requests in the last "
                    f"{self.window // 60} minutes; try again in {wait}s", wait)
            recent.append(now)
            self._calls[key_id] = recent

    @contextmanager
    def slot(self, key_id: str):
        """Hold a concurrent scan slot, both the key's and the server's.

        The key's own cap is checked first, so somebody who is over their share
        is told that rather than being told the server is busy -- the two have
        different answers, one being 'wait for your own scan' and the other
        'wait for somebody else's'.
        """
        with self._lock:
            running = self._running.get(key_id, 0)
            if running >= self.concurrent:
                raise RateLimited(
                    f"this key already has {running} scans running; "
                    "wait for one to finish", 30)
            if self._total_running >= self.total:
                raise RateLimited(
                    f"the service is running its limit of {self.total} scans; "
                    "try again shortly", 30)
            self._running[key_id] = running + 1
            self._total_running += 1
        try:
            yield
        finally:
            with self._lock:
                self._running[key_id] = max(0, self._running.get(key_id, 1) - 1)
                self._total_running = max(0, self._total_running - 1)


def default_limiter_path(key_path: Path | None = None) -> Path:
    """Where the shared counts live: `limiter.db` beside the key file, so the
    processes that share one set of keys share one budget."""
    return (key_path or default_key_path()).parent / "limiter.db"


# A slot a process took and never gave back -- because it was killed mid-scan
# -- is treated as released once it is older than this. The hosted scan has a
# 900 s ceiling (`service.DEFAULT_TIMEOUT`); a slot older than the ceiling
# plus a minute belongs to nobody who is still running.
SLOT_TTL_SECONDS = DEFAULT_TIMEOUT + 60


class FileRateLimiter:
    """The same caps as `RateLimiter`, counted in SQLite so that every process
    serving the same keys shares one budget.

    Each decision is one `BEGIN IMMEDIATE` transaction: SQLite hands the write
    lock to one process at a time, so two workers cannot both read "one slot
    left" and both take it. A connection is opened per decision rather than
    held, which keeps the object safe to share between threads and between
    forked workers alike.

    A slot is a row stamped with the time it was taken; `slot()` deletes it on
    exit whether the body returned or raised. A process killed while holding
    one cannot delete its row, so rows older than `slot_ttl` are swept as
    released by whichever decision comes next.
    """

    def __init__(self, path: Path | None = None,
                 requests: int = RATE_LIMIT_REQUESTS,
                 window: int = RATE_LIMIT_WINDOW_SECONDS,
                 concurrent: int = MAX_CONCURRENT_SCANS,
                 total: int = MAX_TOTAL_SCANS,
                 slot_ttl: int = SLOT_TTL_SECONDS) -> None:
        self.path = Path(path) if path else default_limiter_path()
        self.requests = requests
        self.window = window
        self.concurrent = concurrent
        self.total = total
        self.slot_ttl = slot_ttl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS calls ("
                "  key_id TEXT NOT NULL, at REAL NOT NULL);"
                "CREATE INDEX IF NOT EXISTS calls_by_key ON calls (key_id, at);"
                "CREATE TABLE IF NOT EXISTS slots ("
                "  token TEXT PRIMARY KEY, key_id TEXT NOT NULL,"
                "  pid INTEGER NOT NULL, taken REAL NOT NULL);")

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None: no implicit transactions, so `BEGIN IMMEDIATE`
        # below is the only transaction and it starts where it says it does.
        # The timeout is how long a process waits for another's lock, which is
        # milliseconds per decision; thirty seconds is a stuck disk, not a queue.
        return sqlite3.connect(self.path, timeout=30, isolation_level=None)

    def check(self, key_id: str, now: float | None = None) -> None:
        """Record one request, or refuse it if the key is over its hourly cap."""
        now = time.time() if now is None else now
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM calls WHERE at <= ?", (now - self.window,))
                recent = [row[0] for row in db.execute(
                    "SELECT at FROM calls WHERE key_id = ? AND at > ?",
                    (key_id, now - self.window))]
                if len(recent) >= self.requests:
                    wait = int(self.window - (now - min(recent))) + 1
                    db.execute("COMMIT")
                    raise RateLimited(
                        f"this key has made {self.requests} requests in the last "
                        f"{self.window // 60} minutes; try again in {wait}s", wait)
                db.execute("INSERT INTO calls (key_id, at) VALUES (?, ?)", (key_id, now))
                db.execute("COMMIT")
            except sqlite3.Error:
                db.execute("ROLLBACK")
                raise

    def _acquire(self, key_id: str, now: float | None = None) -> str:
        """Take one slot and return its token. Split out from `slot()` so a
        test can leave one behind, the way a killed process would."""
        now = time.time() if now is None else now
        token = secrets.token_hex(8)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM slots WHERE taken <= ?", (now - self.slot_ttl,))
                running = db.execute("SELECT COUNT(*) FROM slots WHERE key_id = ?",
                                     (key_id,)).fetchone()[0]
                if running >= self.concurrent:
                    db.execute("COMMIT")
                    raise RateLimited(
                        f"this key already has {running} scans running; "
                        "wait for one to finish", 30)
                total = db.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
                if total >= self.total:
                    db.execute("COMMIT")
                    raise RateLimited(
                        f"the service is running its limit of {self.total} scans; "
                        "try again shortly", 30)
                db.execute("INSERT INTO slots (token, key_id, pid, taken) "
                           "VALUES (?, ?, ?, ?)", (token, key_id, os.getpid(), now))
                db.execute("COMMIT")
            except sqlite3.Error:
                db.execute("ROLLBACK")
                raise
        return token

    def _release(self, token: str) -> None:
        with closing(self._connect()) as db:
            db.execute("DELETE FROM slots WHERE token = ?", (token,))

    def running(self, key_id: str | None = None, now: float | None = None) -> int:
        """How many slots are held right now, by one key or in total. Expired
        rows are not counted, so the answer matches what `slot()` would see."""
        now = time.time() if now is None else now
        with closing(self._connect()) as db:
            if key_id is None:
                row = db.execute("SELECT COUNT(*) FROM slots WHERE taken > ?",
                                 (now - self.slot_ttl,)).fetchone()
            else:
                row = db.execute("SELECT COUNT(*) FROM slots WHERE key_id = ? AND taken > ?",
                                 (key_id, now - self.slot_ttl)).fetchone()
        return int(row[0])

    @contextmanager
    def slot(self, key_id: str):
        """Hold a concurrent scan slot, both the key's and the server's, with the
        same two refusals and the same order as `RateLimiter.slot`."""
        token = self._acquire(key_id)
        try:
            yield
        finally:
            self._release(token)


# One limiter per process, shared by every request. The request paths read this
# name at call time rather than binding it at import, so `configure_limiter`
# (and a test's monkeypatch) can replace it without touching any call site.
LIMITER: RateLimiter | FileRateLimiter = RateLimiter()

LIMITER_KINDS = ("memory", "file")


def configure_limiter(kind: str = "memory", key_path: Path | None = None,
                      **caps: int) -> RateLimiter | FileRateLimiter:
    """Install the process-wide limiter: `memory` for one process, `file` for
    several processes serving the same keys. Returns what was installed."""
    global LIMITER
    if kind == "memory":
        LIMITER = RateLimiter(**caps)
    elif kind == "file":
        LIMITER = FileRateLimiter(default_limiter_path(key_path), **caps)
    else:
        raise ServiceError(f"unknown limiter {kind!r}; choose one of {', '.join(LIMITER_KINDS)}")
    return LIMITER


# --------------------------------------------------------------------------
# An audit line per request, and nothing about the code in it
# --------------------------------------------------------------------------
#
# Keeping none of a customer's source is the promise. Being unable to say who
# called, when, and how it ended is a different thing, and not one worth having:
# it is what answers a disputed bill, a leaked key, or "did you run anything for
# us last Tuesday". So one line goes down per request, and that line is about
# the caller, never about their code.

def default_audit_path() -> Path:
    """Where request lines go. `ARBITER_AUDIT` overrides it for a deployment."""
    env = os.environ.get("ARBITER_AUDIT")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".arbiter" / "audit.log"


class AuditLog:
    """One JSON line per request: who called, what they asked, how it ended.

    Deliberately not a record of the work. It holds no file name, no finding, no
    evidence snippet and no part of the uploaded archive -- only the key and user
    that called, the operation, the outcome, the bytes uploaded and the time
    taken. A log that quoted findings would recreate, on disk and permanently,
    exactly the thing the request path is careful to delete.
    """

    def __init__(self, path: Path | None = None, enabled: bool = True) -> None:
        self.path = Path(path) if path else None
        self.enabled = enabled
        self._lock = threading.Lock()

    def record(self, event: str, key: dict | None = None, status: int = 0,
               bytes_in: int = 0, started: float | None = None) -> dict:
        """Write one line. Returns the entry, which is what tests read."""
        entry = {
            "ts": _stamp(time.time()),
            "event": event,
            "key": (key or {}).get("id", ""),
            "user": (key or {}).get("user", ""),
            "status": status,
            "bytes_in": bytes_in,
            "ms": int((time.monotonic() - started) * 1000) if started else 0,
        }
        if not self.enabled:
            return entry
        path = self.path or default_audit_path()
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                fresh = not path.exists()
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, sort_keys=True) + "\n")
                if fresh:
                    # POSIX only; on Windows this toggles the read-only
                    # attribute and nothing more. See `_store`.
                    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:  # pragma: no cover - depends on the filesystem
            # A full or read-only disk must not turn into a failed scan. Say so
            # on stderr, where the operator will see it, and carry on.
            print(f"arbiter: could not write the audit log: {exc}", file=sys.stderr)
        return entry


# Configured by `serve`; tests and embedders pass their own to `create_app`.
AUDIT = AuditLog()


# --------------------------------------------------------------------------
# Request handling, without a web framework in sight
# --------------------------------------------------------------------------

def _ingest(ws: Workspace, upload: bytes | str | Path) -> Path:
    """Put the caller's source in the workspace, refusing an oversized upload."""
    if isinstance(upload, (str, Path)):
        archive = Path(upload)
        if archive.stat().st_size > MAX_UPLOAD_BYTES:
            raise ServiceError(f"upload exceeds {MAX_UPLOAD_BYTES} bytes")
    else:
        if len(upload) > MAX_UPLOAD_BYTES:
            raise ServiceError(f"upload exceeds {MAX_UPLOAD_BYTES} bytes")
        archive = ws.path / "upload.bin"
        archive.write_bytes(upload)
    extract_archive(archive, ws.source)
    archive.unlink(missing_ok=True)
    return ws.source


def handle_scan(upload: bytes | str | Path, profile: str = "offline", only: str = "",
                skip: str = "", timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Scan an uploaded archive and return the report. Keeps nothing."""
    with Workspace() as ws:
        source = _ingest(ws, upload)
        result = scan(str(source), str(ws.output), profile=profile, only=only,
                      skip=skip, timeout=timeout)
        # Deliberately not returning report_path: it names a directory that is
        # about to stop existing, and a caller should not learn server paths.
        return {"report": result["report"], "finding_count": result["finding_count"]}


def handle_gate(upload: bytes | str | Path, profile: str = "ci", only: str = "",
                skip: str = "", timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Gate an uploaded archive. Returns pass or fail with the claim ledger."""
    with Workspace() as ws:
        source = _ingest(ws, upload)
        result = gate(str(source), str(ws.output), profile=profile, only=only,
                      skip=skip, timeout=timeout)
        return {"passed": result["passed"], "gate": result["gate"],
                "claims": result["claims"]}


def handle_review_queue(report: dict, limit: int = 20, rule: str = "",
                        timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Turn a report the caller already has into a queue for a person to mark.

    Returns the queue with every mark blank. Nothing is recorded, here or
    anywhere reachable from here.
    """
    with Workspace() as ws:
        report_path = ws.output / "report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        result = review_queue(str(report_path), str(ws.output), limit=limit,
                              rule=rule, timeout=timeout)
        return {"queue_markdown": result["queue_markdown"],
                "entry_count": result["entry_count"],
                "recorded": False,
                "note": result["note"]}


# Every operation the hosted surface may expose. A test asserts this set, so
# adding a verdict-recording endpoint fails the build rather than review.
ENDPOINTS = {"scan": handle_scan, "gate": handle_gate, "review_queue": handle_review_queue}


# --------------------------------------------------------------------------
# The framework layer, imported only when actually serving
# --------------------------------------------------------------------------

def create_app(key_path: Path | None = None,
               audit: AuditLog | None = None) -> Any:
    """Build the FastAPI application.

    FastAPI is imported here, not at module scope, so everything above stays
    importable and testable without it. It is an optional extra: a plain
    `pip install arbiter-eval` still depends on PyYAML alone.
    """
    audit = audit or AUDIT
    try:
        from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
        from fastapi.responses import JSONResponse
        from pydantic import BaseModel
    except ImportError as exc:
        raise ServiceError(
            "the hosted API needs the optional 'api' dependency: "
            "pip install 'arbiter-eval[api]'"
        ) from exc

    # `from __future__ import annotations` at the top of this module turns every
    # annotation below into a string, and FastAPI resolves those against the
    # module's globals -- where `UploadFile` is not, because the import above is
    # deliberately local to keep the module importable without the extra. So the
    # name is published here. Without it every upload request fails inside body
    # validation with an unresolved forward reference, which is a 500 on the two
    # endpoints that matter most.
    globals()["UploadFile"] = UploadFile

    app = FastAPI(title="Arbiter", version=__version__,
                  description="Evaluate a repository without installing anything. "
                              "Uploads are deleted when the request ends.")

    @app.middleware("http")
    async def enforce_tls(request, call_next):
        """Refuse plaintext before anything reads the key or the body."""
        try:
            require_tls(request.url.scheme)
        except ServiceError as exc:
            # 426 Upgrade Required: the request was understood and the transport
            # is the problem, which is exactly what happened.
            return JSONResponse(status_code=426, content={"detail": str(exc)})
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = HSTS_HEADER
        return response

    def caller(x_api_key: str = Header(default="")) -> dict:
        record = verify_key(x_api_key, key_path)
        if record is None:
            # One message for unknown, revoked and expired alike: telling a
            # caller which one it was would confirm that a key it guessed once
            # existed. The audit line says a key was refused, not which one --
            # writing a rejected key down would put a near-miss secret on disk.
            audit.record("auth_failed", status=401)
            raise HTTPException(status_code=401,
                                detail="unknown, revoked or expired API key")
        try:
            LIMITER.check(record["id"])
        except RateLimited as exc:
            audit.record("rate_limited", record, status=429)
            raise HTTPException(status_code=429, detail=str(exc),
                                headers={"Retry-After": str(exc.retry_after)}) from exc
        return record

    async def _read(upload: UploadFile) -> bytes:
        data = await upload.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413,
                                detail=f"upload exceeds {MAX_UPLOAD_BYTES} bytes")
        return data

    def _guard(key: dict, event: str, fn, *args, size: int = 0, **kwargs):
        """Run one operation while holding a concurrency slot, and audit it.

        `RateLimited` is caught first because it subclasses `ServiceError`, and
        "you asked for too much" is a different answer from "your request was
        malformed". Every outcome is recorded, including the failures: a log
        that only holds successes cannot show somebody hammering the service.
        """
        started = time.monotonic()
        try:
            with LIMITER.slot(key["id"]):
                result = fn(*args, **kwargs)
        except RateLimited as exc:
            audit.record(event, key, status=429, bytes_in=size, started=started)
            raise HTTPException(status_code=429, detail=str(exc),
                                headers={"Retry-After": str(exc.retry_after)}) from exc
        except ServiceError as exc:
            audit.record(event, key, status=400, bytes_in=size, started=started)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:  # audited as a 500, then re-raised unchanged
            audit.record(event, key, status=500, bytes_in=size, started=started)
            raise
        audit.record(event, key, status=200, bytes_in=size, started=started)
        return result

    class ReviewRequest(BaseModel):
        report: dict
        limit: int = 20
        rule: str = ""

    # Published for the same reason as `UploadFile` above. Unresolved, FastAPI
    # cannot tell this is a body model and reads `body` as a query parameter
    # instead, so the endpoint rejects every JSON request it is sent.
    globals()["ReviewRequest"] = ReviewRequest

    @app.get("/v1/health")
    def health() -> dict:
        # The limits are published rather than discovered on contact with a 429.
        # Somebody should be able to see what they have without asking for it.
        return {"status": "ok", "version": __version__,
                "retains_nothing": RETAINS_NOTHING, "tls_required": True,
                "free": True,
                "limits": {"requests_per_hour": LIMITER.requests,
                           "concurrent_scans_per_key": LIMITER.concurrent,
                           "concurrent_scans_total": LIMITER.total,
                           "max_upload_bytes": MAX_UPLOAD_BYTES,
                           "key_lifetime_days": DEFAULT_KEY_LIFETIME_DAYS}}

    @app.post("/v1/scan")
    async def scan_endpoint(archive: UploadFile = File(...), profile: str = "offline",
                            only: str = "", skip: str = "",
                            key: dict = Depends(caller)) -> dict:
        data = await _read(archive)
        return _guard(key, "scan", handle_scan, data, profile, only, skip,
                      size=len(data))

    @app.post("/v1/gate")
    async def gate_endpoint(archive: UploadFile = File(...), profile: str = "ci",
                            only: str = "", skip: str = "",
                            key: dict = Depends(caller)) -> dict:
        data = await _read(archive)
        return _guard(key, "gate", handle_gate, data, profile, only, skip,
                      size=len(data))

    @app.post("/v1/review-queue")
    def review_endpoint(body: ReviewRequest, key: dict = Depends(caller)) -> dict:
        return _guard(key, "review_queue", handle_review_queue,
                      body.report, body.limit, body.rule)

    return app


def serve(host: str = "127.0.0.1", port: int = 8443, key_path: Path | None = None,
          certfile: str | None = None, keyfile: str | None = None,
          audit_path: str | None = None, audit: bool = True,
          limiter: str = "memory") -> int:
    """Run the API over TLS. There is no plaintext mode, and no plaintext port.

    This process holds the certificate. A proxy may sit in front of it, but the
    hop from that proxy to here is TLS too; nothing here opens a bare socket.

    `limiter` is `memory` for one process or `file` for several processes that
    serve the same key file and should share one budget (`FileRateLimiter`).
    """
    # The TLS check comes first deliberately. An operator whose install is
    # missing the extra should still be told plainly that their arrangement
    # would have served plaintext, rather than fixing the dependency and
    # meeting that refusal only on the second attempt.
    check_tls_config(certfile, keyfile)
    configure_limiter(limiter, key_path)

    try:
        import uvicorn
    except ImportError:
        print("arbiter: the hosted API needs the optional 'api' dependency.\n"
              "         pip install 'arbiter-eval[api]'")
        return 2

    log = AuditLog(Path(audit_path).expanduser() if audit_path else None,
                   enabled=audit)
    if audit:
        print(f"arbiter: request lines go to {log.path or default_audit_path()} "
              "(who called and how it ended; never their code)")
    else:
        print("arbiter: auditing is off; no record of who called will be kept")
    if limiter == "file":
        print(f"arbiter: rate limits are shared through {LIMITER.path}")
    app = create_app(key_path, audit=log)

    # uvicorn builds its own SSL context and exposes no minimum-version hook, so
    # a TLS 1.2 floor cannot be asserted from here. Restricting to forward-secret
    # AEAD suites is what is actually enforced: it leaves nothing a TLS 1.0 or
    # 1.1 client can negotiate. The version floor proper belongs to the platform
    # OpenSSL policy, or to a proxy in front of this process, which is where to
    # put it if that floor has to be guaranteed.
    print(f"arbiter: serving on https://{host}:{port}")
    uvicorn.run(app, host=host, port=port, ssl_certfile=certfile,
                ssl_keyfile=keyfile,
                ssl_ciphers="ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM")
    return 0
