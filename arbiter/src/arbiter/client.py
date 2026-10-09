"""The client half: reach a hosted Arbiter instead of installing one.

Every other surface in this package runs the scanner where the repository is.
This one does not run it at all. It packages the target, sends it to a hosted
instance over TLS, and renders what comes back with the same functions a local
scan uses -- so `arbiter remote scan .` prints what `arbiter scan .` prints,
having installed none of the analyzers that produced it.

That is the point. A person who wants an opinion about their repository should
not have to install five analyzers, a tree-sitter grammar pack and a rule
engine to get one. `pip install arbiter-eval` depends on PyYAML alone, and this
module deliberately keeps it that way: the transport is `urllib` from the
standard library, not `requests` or `httpx`. A thin client that drags in a
dependency tree is not thin.

## What this module refuses to do

It will not speak plaintext. `https://` is the only scheme accepted, and an
`http://` server address is refused before a key is read or a byte is packed --
the key would otherwise cross the network in the clear on the way to finding
out the server disagrees. The server refuses plaintext too; both ends saying so
is deliberate, because the client's refusal is the one that happens before the
secret moves.

It also will not send what the scanner would not have read. The archive skips
the same directories `inventory.SKIP_DIRS` skips, so `.git`, `node_modules` and
`.venv` stay on the caller's disk. That keeps uploads small, but the reason it
matters is custody: source that never left is source nobody has to trust us
with.
"""
from __future__ import annotations

import json
import os
import ssl
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from io import BytesIO
from pathlib import Path

from .core import Report
from .inventory import SKIP_DIRS

# Where the server address and key live when they are not passed on the command
# line or set in the environment. One file, in the user's home, because a hosted
# client is per-person -- the key identifies a user and nothing else.
DEFAULT_CONFIG = Path.home() / ".arbiter" / "client.json"

ENV_SERVER = "ARBITER_SERVER"
ENV_KEY = "ARBITER_API_KEY"

# Long enough for a scan of a large repository to finish on the server, short
# enough that a hung connection is not mistaken for work in progress.
SCAN_TIMEOUT_SECONDS = 900
HEALTH_TIMEOUT_SECONDS = 30


class ClientError(Exception):
    """Something the caller can act on: a bad address, a refused key, a cap."""


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def load_settings(server: str | None = None, key: str | None = None,
                  config_path: Path | None = None,
                  need_key: bool = True) -> tuple[str, str]:
    """Work out which server to call and which key to present.

    Explicit arguments beat the environment, and the environment beats the
    config file, so a one-off `--server` never silently loses to a stale file.

    `need_key` is false for the one call that does not take one: asking a server
    what it is and what it allows should not require already holding a key, or
    nobody could find out what they were being offered before asking for one.
    """
    path = config_path or DEFAULT_CONFIG
    stored: dict = {}
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ClientError(f"{path} is not readable JSON: {exc}") from exc

    address = server or os.environ.get(ENV_SERVER) or stored.get("server") or ""
    secret = key or os.environ.get(ENV_KEY) or stored.get("key") or ""

    if not address:
        raise ClientError(
            "no server address. Pass --server https://..., set "
            f"{ENV_SERVER}, or write one to {path}"
        )
    address = normalise_server(address)
    if need_key and not secret:
        raise ClientError(
            "no API key. Pass --key, set "
            f"{ENV_KEY}, or write one to {path}. Keys are issued by hand by "
            "whoever runs the server."
        )
    return address, secret


def normalise_server(address: str) -> str:
    """Accept `https://host[:port]` and nothing else.

    Refusing `http://` here rather than letting the server do it is the whole
    reason this function exists. The server's refusal arrives after the key has
    already crossed the network in the clear, which is too late to matter.
    """
    address = address.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(address)
    if parsed.scheme == "http":
        raise ClientError(
            f"{address} is plaintext. Arbiter is HTTPS only -- sending a key "
            "over http would expose it before the server could refuse it. Use "
            "https://"
        )
    if parsed.scheme != "https":
        raise ClientError(
            f"'{address}' is not a server address; expected https://host[:port]"
        )
    if not parsed.netloc:
        raise ClientError(f"'{address}' names no host")
    return address


def _tls_context(cacert: str | None) -> ssl.SSLContext:
    """Verified TLS, with an optional private root for a self-signed server.

    `cacert` adds a certificate to trust; it never disables verification. There
    is no insecure switch in this client on purpose -- `curl -k` against a
    service holding somebody's source is not a thing to make convenient.
    """
    if cacert:
        if not Path(cacert).expanduser().is_file():
            raise ClientError(f"no certificate file at {cacert}")
        return ssl.create_default_context(cafile=str(Path(cacert).expanduser()))
    return ssl.create_default_context()


# --------------------------------------------------------------------------
# Packaging the target
# --------------------------------------------------------------------------

def build_archive(target: Path, max_bytes: int | None = None) -> bytes:
    """Tar and gzip a directory for upload, skipping what is never scanned.

    Symbolic links are stored as links rather than followed. Following them
    would let a link inside the target pull in a file outside it, which is the
    caller's own machine leaking into an upload they did not inspect.
    """
    target = Path(target).expanduser().resolve()
    if not target.is_dir():
        raise ClientError(f"{target} is not a directory")

    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(target.rglob("*")):
            relative = path.relative_to(target)
            if any(part in SKIP_DIRS for part in relative.parts):
                continue
            if path.is_dir() and not path.is_symlink():
                continue
            try:
                archive.add(path, arcname=str(relative), recursive=False)
            except (OSError, ValueError):
                # A file that vanished mid-walk, or one the caller cannot read.
                # Skipping it is right; failing the whole upload for it is not.
                continue

    data = buffer.getvalue()
    if not data:
        raise ClientError(f"{target} held nothing to send")
    if max_bytes is not None and len(data) > max_bytes:
        raise ClientError(
            f"{target} packs to {len(data):,} bytes, over the server's "
            f"{max_bytes:,}-byte limit. Scan a subdirectory, or ask whoever "
            "runs the server to raise it."
        )
    return data


# --------------------------------------------------------------------------
# Talking to the server
# --------------------------------------------------------------------------

def _send(url: str, key: str | None, cacert: str | None, timeout: int,
          body: bytes | None = None, content_type: str | None = None) -> dict:
    """One request, with every failure turned into a sentence."""
    request = urllib.request.Request(url, data=body)
    if key:
        request.add_header("X-API-Key", key)
    if content_type:
        request.add_header("Content-Type", content_type)
    request.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=_tls_context(cacert)) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ClientError(_explain(exc)) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise ClientError(
                f"the server's certificate did not verify: {reason}. If it is "
                "self-signed, pass --cacert with the certificate you were given "
                "out of band."
            ) from exc
        raise ClientError(f"could not reach {url}: {reason}") from exc
    except json.JSONDecodeError as exc:
        raise ClientError(
            f"{url} answered something that is not JSON; is it an Arbiter server?"
        ) from exc


def _explain(exc: urllib.error.HTTPError) -> str:
    """Turn a status code into what the caller should do about it."""
    try:
        detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
    except Exception:  # noqa: BLE001 - the body is already the unhappy path
        detail = ""

    if exc.code == 401:
        return ("the server refused your key: it is unknown, revoked or expired. "
                "Keys are issued by hand; ask whoever runs the server for a new one.")
    if exc.code == 413:
        return detail or "the upload was larger than the server accepts"
    if exc.code == 426:
        return ("the server refused the request as plaintext. It is HTTPS only; "
                "check the address.")
    if exc.code == 429:
        wait = exc.headers.get("Retry-After", "")
        suffix = f" Try again in {wait} seconds." if wait else ""
        return (detail or "you have reached the server's rate limit.") + suffix
    if exc.code == 400:
        return detail or "the server rejected the request"
    return f"the server answered {exc.code}" + (f": {detail}" if detail else "")


def health(server: str, cacert: str | None = None) -> dict:
    """What the server is and what it will allow. Needs no key."""
    return _send(f"{normalise_server(server)}/v1/health", None, cacert,
                 HEALTH_TIMEOUT_SECONDS)


def _upload(server: str, key: str, cacert: str | None, endpoint: str,
            archive: bytes, profile: str, only: str, skip: str) -> dict:
    """POST an archive as multipart/form-data under the field name `archive`."""
    boundary = uuid.uuid4().hex
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="archive"; '
        b'filename="repository.tar.gz"\r\n',
        b"Content-Type: application/gzip\r\n\r\n",
        archive,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    query = urllib.parse.urlencode(
        {"profile": profile, "only": only, "skip": skip})
    url = f"{normalise_server(server)}{endpoint}?{query}"
    return _send(url, key, cacert, SCAN_TIMEOUT_SECONDS, body,
                 f"multipart/form-data; boundary={boundary}")


def scan(server: str, key: str, target: Path, profile: str = "offline",
         only: str = "", skip: str = "", cacert: str | None = None,
         max_bytes: int | None = None) -> dict:
    """Scan a directory on a hosted instance. Returns the server's answer."""
    return _upload(server, key, cacert, "/v1/scan",
                   build_archive(target, max_bytes), profile, only, skip)


def gate(server: str, key: str, target: Path, profile: str = "ci",
         only: str = "", skip: str = "", cacert: str | None = None,
         max_bytes: int | None = None) -> dict:
    """Run the policy gate on a hosted instance."""
    return _upload(server, key, cacert, "/v1/gate",
                   build_archive(target, max_bytes), profile, only, skip)


def review_queue(server: str, key: str, report: dict, limit: int = 20,
                 rule: str = "", cacert: str | None = None) -> dict:
    """Ask the server to draw a review queue from a report.

    Note what does not exist here, and will not: there is no client call that
    records a verdict. The server has no such endpoint either. A queue is drawn
    by a machine and marked by a person, and `arbiter review --apply` does that
    locally against the caller's own ledger.
    """
    body = json.dumps({"report": report, "limit": limit,
                       "rule": rule}).encode("utf-8")
    return _send(f"{normalise_server(server)}/v1/review-queue", key, cacert,
                 SCAN_TIMEOUT_SECONDS, body, "application/json")


def report_from(payload: dict) -> Report:
    """Rebuild a Report from what the server sent.

    The server returns `Report.to_dict()`, so rehydrating it means every local
    renderer -- console, markdown, HTML, SARIF -- works on a remote result
    without knowing it was remote.
    """
    body = payload.get("report")
    if not isinstance(body, dict):
        raise ClientError("the server's answer carried no report")
    return Report.from_dict(body)
