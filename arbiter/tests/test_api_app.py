"""The assembled hosted application through a real request, and the client
half that talks to it (REQ-018, REQ-019).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import _finding

from arbiter.core import Report

# ---------------------------------------------------------------------------
# The assembled application: everything below goes through a real request
#
# The tests above exercise the pieces -- key verification, the limiter, the
# handlers -- directly. These go through the middleware, the dependency and the
# endpoint, because the wiring between correct pieces is its own failure mode
# and no unit test can see it.
# ---------------------------------------------------------------------------

def _client(key_path, audit=None):
    """A test client over the real application, speaking HTTPS.

    `base_url` matters: the TLS middleware refuses anything else, which is the
    behaviour being relied on rather than worked around.
    """
    pytest.importorskip("fastapi", reason="the api extra is not installed")
    pytest.importorskip("httpx2", reason="starlette's test client needs httpx2")
    from fastapi.testclient import TestClient

    from arbiter import api
    app = api.create_app(key_path, audit=audit or api.AuditLog(enabled=False))
    return TestClient(app, base_url="https://testserver")


def _archive(tmp_path, name="repo/billing.py", secret=True):
    """A small gzipped tarball, with a credential in it when one is wanted."""
    import tarfile
    body = ('KEY = "sk_live_51H8xQ2LkdIwHu7ix' + "Z" * 20 + '"\n') if secret else "x = 1\n"
    source = tmp_path / "payload.py"
    source.write_text(body)
    archive = tmp_path / "repo.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(source, arcname=name)
    return archive.read_bytes()


def _reviewable_report():
    """A report holding one finding, under a rule nothing has adjudicated.

    An empty report is a legitimate thing to send and comes back as an empty
    queue, so it cannot be used to prove the success path works.
    """
    report = Report()
    report.findings.append(_finding("arbiter/hosted-test-only", path="billing.py"))
    return report.to_dict()


def test_a_request_without_a_key_is_refused_by_the_application(tmp_path):
    """The dependency has to actually be wired to the endpoint."""
    from arbiter import api
    api.mint_key("dana@acme.example", tmp_path / "keys.json")
    client = _client(tmp_path / "keys.json")
    for headers in ({}, {"X-API-Key": "arb_not-a-real-key"}):
        response = client.post("/v1/scan", files={"archive": ("r.tar.gz", b"x")},
                               headers=headers)
        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "unknown, revoked or expired API key"


def test_a_revoked_key_stops_working_on_the_next_request(tmp_path):
    """Revocation is only real if the running application honours it."""
    from arbiter import api
    path = tmp_path / "keys.json"
    raw, record = api.mint_key("dana@acme.example", path)
    client = _client(path)
    assert client.get("/v1/health").status_code == 200
    api.revoke_key(record["id"], path)
    response = client.post("/v1/review-queue", json={"report": {"findings": []}},
                           headers={"X-API-Key": raw})
    assert response.status_code == 401


def test_health_needs_no_key_but_everything_else_does(tmp_path):
    """The one endpoint a stranger may call is the one that says nothing."""
    client = _client(tmp_path / "keys.json")
    assert client.get("/v1/health").status_code == 200
    for path, kwargs in (("/v1/scan", {"files": {"archive": ("r.tar.gz", b"x")}}),
                         ("/v1/gate", {"files": {"archive": ("r.tar.gz", b"x")}}),
                         ("/v1/review-queue", {"json": {"report": {}}})):
        assert client.post(path, **kwargs).status_code == 401, path


def test_a_plaintext_request_is_refused_before_the_key_is_read(tmp_path):
    """426, and no chance for the key to be logged or acted on.

    The middleware runs before the dependency, so a key sent over plaintext is
    never verified -- which matters, because it has already been exposed.
    """
    from arbiter import api
    path = tmp_path / "keys.json"
    raw, _ = api.mint_key("dana@acme.example", path)
    client = _client(path)
    response = client.request("GET", "http://testserver/v1/health")
    assert response.status_code == 426
    assert "HTTPS" in response.json()["detail"] or "plaintext" in response.json()["detail"]
    assert client.post("http://testserver/v1/review-queue",
                       json={"report": {}},
                       headers={"X-API-Key": raw}).status_code == 426


def test_a_forwarded_header_cannot_talk_the_application_into_plaintext(tmp_path):
    """Anyone can send `X-Forwarded-Proto: https`. Nothing here believes it."""
    client = _client(tmp_path / "keys.json")
    assert client.get("http://testserver/v1/health",
                      headers={"X-Forwarded-Proto": "https"}).status_code == 426
    assert client.get("/v1/health",
                      headers={"X-Forwarded-Proto": "http"}).status_code == 200


def test_every_response_carries_the_hsts_header(tmp_path):
    """Including the refusals, which is where a downgraded client would land."""
    from arbiter import api
    client = _client(tmp_path / "keys.json")
    for response in (client.get("/v1/health"),
                     client.post("/v1/review-queue", json={"report": {}})):
        assert response.headers["Strict-Transport-Security"] == api.HSTS_HEADER


def test_an_oversized_upload_is_refused_by_the_endpoint(tmp_path, monkeypatch):
    """413 before any of it reaches a workspace."""
    from arbiter import api
    path = tmp_path / "keys.json"
    raw, _ = api.mint_key("dana@acme.example", path)
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 128)
    client = _client(path)
    response = client.post("/v1/scan", files={"archive": ("r.tar.gz", b"x" * 200)},
                           headers={"X-API-Key": raw})
    assert response.status_code == 413


@pytest.mark.slow
def test_a_scan_over_http_returns_the_report_and_leaves_nothing(tmp_path):
    """The whole path, once: upload, scan, report back, nothing kept."""
    from arbiter import api
    path = tmp_path / "keys.json"
    raw, _ = api.mint_key("dana@acme.example", path)
    client = _client(path)
    response = client.post("/v1/scan?only=secrets",
                           files={"archive": ("repo.tar.gz", _archive(tmp_path))},
                           headers={"X-API-Key": raw})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["finding_count"] >= 1, "the fixture produced nothing, so this proves nothing"
    assert "report_path" not in body, "a server path came back to the caller"
    assert "sk_live_51H8xQ2LkdIwHu7ix" not in response.text, "the secret was reprinted"


def test_a_refused_profile_is_a_400_not_a_500(tmp_path):
    """Asking for a networked profile is a bad request, not a server fault."""
    from arbiter import api
    path = tmp_path / "keys.json"
    raw, _ = api.mint_key("dana@acme.example", path)
    client = _client(path)
    response = client.post("/v1/scan?profile=connected",
                           files={"archive": ("repo.tar.gz", _archive(tmp_path))},
                           headers={"X-API-Key": raw})
    assert response.status_code == 400
    assert "connected" in response.json()["detail"]


def test_an_hourly_cap_reached_answers_429_with_retry_after(tmp_path, monkeypatch):
    """A caller over the cap needs to be told when to come back."""
    from arbiter import api
    path = tmp_path / "keys.json"
    raw, _ = api.mint_key("dana@acme.example", path)
    monkeypatch.setattr(api, "LIMITER", api.RateLimiter(requests=2, window=3600))
    client = _client(path)
    body = {"report": _reviewable_report()}
    ok = [client.post("/v1/review-queue", json=body,
                      headers={"X-API-Key": raw}) for _ in range(2)]
    assert all(r.status_code == 200 for r in ok), [r.text for r in ok]
    limited = client.post("/v1/review-queue", json=body,
                          headers={"X-API-Key": raw})
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0


def test_a_request_is_audited_end_to_end(tmp_path):
    """The log line has to be written by the running application, not just by a
    method somebody could forget to call."""
    from arbiter import api
    path = tmp_path / "keys.json"
    raw, record = api.mint_key("dana@acme.example", path)
    log = api.AuditLog(tmp_path / "audit.log")
    client = _client(path, audit=log)
    client.post("/v1/review-queue", json={"report": _reviewable_report()},
                headers={"X-API-Key": raw})
    client.post("/v1/review-queue", json={"report": {}},
                headers={"X-API-Key": "arb_wrong"})
    lines = [json.loads(line) for line in (tmp_path / "audit.log").read_text().splitlines()]
    assert [e["event"] for e in lines] == ["review_queue", "auth_failed"]
    assert lines[0]["user"] == "dana@acme.example" and lines[0]["key"] == record["id"]
    assert lines[0]["status"] == 200
    assert lines[1]["key"] == "", "a rejected key was written down"


def test_a_report_with_nothing_to_review_is_an_empty_queue_not_an_error(tmp_path):
    """A clean report is the good outcome, so asking for its queue must not look
    like a bad request -- and the refusal it used to produce named a server
    temporary directory back at the caller."""
    from arbiter import api
    path = tmp_path / "keys.json"
    raw, _ = api.mint_key("dana@acme.example", path)
    client = _client(path)
    response = client.post("/v1/review-queue", json={"report": {"findings": []}},
                           headers={"X-API-Key": raw})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entry_count"] == 0 and body["recorded"] is False
    assert str(tmp_path.anchor) not in json.dumps(body), "a server path came back"


def test_the_limits_are_published_rather_than_discovered_through_a_429(tmp_path):
    """Nobody is charged, so the limits are capacity, and capacity should be
    visible. A recipient seeing what they have without asking is the difference
    between a service and a gate."""
    from arbiter import api
    client = _client(tmp_path / "keys.json")
    body = client.get("/v1/health").json()
    assert body["free"] is True and body["retains_nothing"] is True
    assert body["limits"] == {
        "requests_per_hour": api.RATE_LIMIT_REQUESTS,
        "concurrent_scans_per_key": api.MAX_CONCURRENT_SCANS,
        "concurrent_scans_total": api.MAX_TOTAL_SCANS,
        "max_upload_bytes": api.MAX_UPLOAD_BYTES,
        "key_lifetime_days": api.DEFAULT_KEY_LIFETIME_DAYS,
    }


def test_the_hourly_limit_sits_well_above_anyone_testing_in_earnest(tmp_path):
    """A limit a friend can feel while testing is a restriction wearing
    capacity's clothes. Twenty repositories in an afternoon must not hit it."""
    from arbiter import api
    assert api.RATE_LIMIT_REQUESTS >= 100, "the hourly cap is back in metering range"


def test_auditing_can_be_turned_off_and_then_writes_nothing(tmp_path):
    """`--no-audit` has to actually keep nothing, not merely log less."""
    from arbiter import api
    path = tmp_path / "audit.log"
    log = api.AuditLog(path, enabled=False)
    assert log.record("scan", {"id": "k1", "user": "dana"}, status=200)["event"] == "scan"
    assert not path.exists(), "auditing was off and a file appeared anyway"


# --------------------------------------------------------------------------
# The client half (REQ-019)
#
# These matter because the hosted API was unusable by a person until it had a
# client: every CLI command was either local or server-side, and the docs told
# a caller to assemble multipart uploads by hand. The end-to-end tests below
# route the client's own urllib through the real application, so the field
# name, the query string and the header are proven against the endpoints
# rather than assumed to match them.
# --------------------------------------------------------------------------

def _repo(tmp_path, name="repo"):
    """A small tree holding two directories the scanner never walks."""
    root = tmp_path / name
    (root / "src").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (root / "node_modules" / "pkg" / "i.js").write_text("//\n", encoding="utf-8")
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    return root


def _through_the_app(monkeypatch, app_client):
    """Point the client's urllib at the real application.

    The client speaks HTTP over urllib and the test client speaks ASGI in
    process, so this bridges the two. Everything the client builds -- the
    multipart body, the field name, the query string, the X-API-Key header --
    is what the endpoint actually receives.
    """
    import io as _io
    import urllib.error
    import urllib.request
    from urllib.parse import urlsplit

    class _Response:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None, context=None):
        parts = urlsplit(request.full_url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        headers = dict(request.headers)
        if request.data is None:
            answer = app_client.get(path, headers=headers)
        else:
            answer = app_client.post(path, content=request.data, headers=headers)
        if answer.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, answer.status_code,
                                         answer.text, answer.headers,
                                         _io.BytesIO(answer.content))
        return _Response(answer.content)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_the_client_refuses_plaintext_before_the_key_leaves_the_machine():
    """The server refuses plaintext too, but its refusal arrives after the key
    has already crossed the network in the clear. The client's refusal is the
    one that happens while the secret is still at home."""
    from arbiter import client
    with pytest.raises(client.ClientError) as caught:
        client.normalise_server("http://arbiter.example.com")
    assert "HTTPS only" in str(caught.value)
    for rejected in ("ftp://host", "arbiter.example.com", "https://"):
        with pytest.raises(client.ClientError):
            client.normalise_server(rejected)
    assert client.normalise_server("https://host:8443/") == "https://host:8443"


def test_the_client_has_no_switch_that_turns_certificate_checking_off():
    """--cacert adds a root to trust; nothing subtracts one. A service holding
    somebody else's source is not a place to make `curl -k` convenient."""
    import ssl

    from arbiter import client
    context = client._tls_context(None)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    source = Path(client.__file__).read_text(encoding="utf-8")
    for forbidden in ("CERT_NONE", "check_hostname = False", "_create_unverified"):
        assert forbidden not in source, f"{forbidden} is reachable in the client"
    with pytest.raises(client.ClientError):
        client._tls_context("no-such-certificate.pem")


def test_the_upload_leaves_behind_what_the_scanner_would_never_have_read(tmp_path):
    """`.git` and `node_modules` staying on disk keeps uploads small, but the
    reason it matters is custody: source that never left is source nobody has
    to be trusted with."""
    import tarfile
    from io import BytesIO

    from arbiter import client
    blob = client.build_archive(_repo(tmp_path))
    with tarfile.open(fileobj=BytesIO(blob)) as archive:
        names = sorted(archive.getnames())
    assert names == ["README.md", "src/app.py"], names


def test_an_upload_over_the_servers_limit_is_refused_before_it_is_sent(tmp_path):
    """Uploading for two minutes to earn a 413 is a worse answer than a
    sentence, and the limit is published on /v1/health for exactly this."""
    from arbiter import client
    with pytest.raises(client.ClientError) as caught:
        client.build_archive(_repo(tmp_path), max_bytes=10)
    message = str(caught.value)
    assert "over the server's" in message and "10-byte" in message


def test_where_the_server_and_key_come_from_has_one_order(tmp_path, monkeypatch):
    """A one-off --server must never silently lose to a stale config file."""
    from arbiter import client
    config = tmp_path / "client.json"
    config.write_text(json.dumps({"server": "https://file.example", "key": "kf"}),
                      encoding="utf-8")
    monkeypatch.delenv(client.ENV_SERVER, raising=False)
    monkeypatch.delenv(client.ENV_KEY, raising=False)
    assert client.load_settings(config_path=config) == ("https://file.example", "kf")
    monkeypatch.setenv(client.ENV_SERVER, "https://env.example")
    monkeypatch.setenv(client.ENV_KEY, "ke")
    assert client.load_settings(config_path=config) == ("https://env.example", "ke")
    assert client.load_settings("https://flag.example", "kflag", config) == (
        "https://flag.example", "kflag")


def test_asking_what_a_server_offers_needs_no_key(tmp_path, monkeypatch):
    """Otherwise nobody could see what they were being offered before asking
    for access to it."""
    from arbiter import client
    monkeypatch.delenv(client.ENV_KEY, raising=False)
    monkeypatch.setenv(client.ENV_SERVER, "https://arbiter.example")
    server, key = client.load_settings(config_path=tmp_path / "absent.json",
                                       need_key=False)
    assert server == "https://arbiter.example" and key == ""
    with pytest.raises(client.ClientError) as caught:
        client.load_settings(config_path=tmp_path / "absent.json")
    assert "issued by hand" in str(caught.value)


def test_every_refusal_the_server_can_send_becomes_a_sentence():
    """A status code on its own tells a caller nothing about what to do next."""
    import io as _io
    import urllib.error

    from arbiter import client

    def refusal(code, body=b'{"detail":"no"}', headers=None):
        return urllib.error.HTTPError("https://h/v1/scan", code, "", headers or {},
                                      _io.BytesIO(body))

    assert "issued by hand" in client._explain(refusal(401))
    assert "larger than the server accepts" in client._explain(refusal(413, b"{}"))
    assert "HTTPS only" in client._explain(refusal(426, b"{}"))
    assert "Try again in 90 seconds" in client._explain(
        refusal(429, b'{"detail":"slow down."}', {"Retry-After": "90"}))


def test_nothing_the_client_can_call_records_a_verdict():
    """The same refusal the MCP surface makes. A queue is drawn by a machine and
    marked by a person; a client that could mark would let the ledger fill up
    with the model's opinion of the model's own output."""
    from arbiter import client
    public = {n for n in dir(client) if not n.startswith("_")}
    for banned in ("record", "apply", "adjudicate", "verdict", "feedback"):
        assert not any(banned in n for n in public), f"{banned} is reachable"


@pytest.mark.slow
def test_a_scan_goes_out_and_comes_back_renderable(tmp_path, monkeypatch):
    """End to end through the real application: the client packs a directory,
    the endpoint accepts the multipart field and the key, and what comes back
    rebuilds into a Report the ordinary console renderer can print."""
    from arbiter import api, client
    from arbiter.report import render_console
    keys = tmp_path / "keys.json"
    raw, _ = api.mint_key("dana@acme.example", keys)
    _through_the_app(monkeypatch, _client(keys))

    answer = client.scan("https://testserver", raw, _repo(tmp_path))
    assert "report" in answer and "finding_count" in answer
    assert isinstance(render_console(client.report_from(answer), color=False), str)


def test_a_scan_sent_with_a_bad_key_is_refused_end_to_end(tmp_path, monkeypatch):
    """The client cannot talk its way past authentication by being the client."""
    from arbiter import client
    _through_the_app(monkeypatch, _client(tmp_path / "keys.json"))
    with pytest.raises(client.ClientError) as caught:
        client.scan("https://testserver", "arb_not-a-real-key", _repo(tmp_path))
    assert "unknown, revoked or expired" in str(caught.value)


def test_the_client_reads_the_limits_the_server_publishes(tmp_path, monkeypatch):
    """The preflight that lets an oversized target be refused locally."""
    from arbiter import api, client
    _through_the_app(monkeypatch, _client(tmp_path / "keys.json"))
    state = client.health("https://testserver")
    assert state["limits"]["max_upload_bytes"] == api.MAX_UPLOAD_BYTES
    assert state["retains_nothing"] is True
