"""The service layer and the hosted API's pieces: containment, key
issuance, TLS, limits and audit (REQ-018).
"""
from __future__ import annotations

import json

import pytest

# ---------------------------------------------------------------------------
# Service layer: the containment both front doors inherit (REQ-018)
# ---------------------------------------------------------------------------

def test_the_service_layer_exposes_no_way_to_record_a_verdict():
    """The constraint that makes calibration worth reading.

    `learn.record()` refuses to re-adjudicate a fingerprint, so a mark is
    permanent. If a remote caller could mark findings, the ledger would stop
    being the one signal the system did not generate. This asserts the absence
    of that capability rather than trusting a reviewer to notice it returning.
    """
    from arbiter import mcp, service
    assert set(service.OPERATIONS) == {"scan", "gate", "review_queue", "review_draft"}
    assert set(mcp.HANDLERS) == {"arbiter_scan", "arbiter_gate", "arbiter_review_queue", "arbiter_review_draft"}
    for name in dir(service):
        assert "apply" not in name.lower(), f"service grew {name}"
        assert "adjudicat" not in name.lower(), f"service grew {name}"
    for tool in mcp.TOOLS:
        assert "apply" not in json.dumps(tool["inputSchema"]).lower()


@pytest.mark.slow
def test_a_review_queue_from_the_service_is_unmarked(tmp_path):
    """A queue is a question, not an answer. Every mark leaves blank."""
    from arbiter import service
    (tmp_path / "billing.py").write_text('KEY = "sk_live_51H8xQ2LkdIwHu7ix' + "Z" * 20 + '"\n')
    out = tmp_path / "out"
    scanned = service.scan(str(tmp_path), str(out), only="secrets")
    assert scanned["finding_count"] >= 1, "fixture produced nothing, so this proves nothing"
    queue = service.review_queue(scanned["report_path"], str(out), limit=5)
    assert queue["recorded"] is False
    assert queue["entry_count"] >= 1
    # The legend explains `[y]` and `[n]`, so a bare substring search proves
    # nothing. What must hold is that no *entry* line carries a mark, which is
    # exactly what `review.MARK` matches when the queue is read back.
    from arbiter.review import MARK
    entries = [MARK.match(line) for line in queue["queue_markdown"].split("\n")]
    marks = [m.group(1) for m in entries if m]
    assert marks, "no entry lines found, so this proves nothing"
    assert set(marks) == {" "}, f"the service pre-marked findings: {set(marks)}"


def test_a_network_profile_is_refused_unless_the_operator_allows_it():
    """A remote caller must not be able to turn the network on for its own upload."""
    from arbiter import service
    for profile in ("connected", "audit"):
        with pytest.raises(service.ServiceError, match="network"):
            service.check_profile(profile)
        assert service.check_profile(profile, allow_network=True) == profile
    for profile in ("offline", "ci"):
        assert service.check_profile(profile) == profile
    with pytest.raises(service.ServiceError, match="unknown profile"):
        service.check_profile("whatever-i-like")


def test_output_cannot_be_written_outside_the_caller_directory(tmp_path):
    """`--out` defaulting inside the scanned tree is how a scan reported on its
    own previous HTML -- 27.7% of unsuppressed findings. The service always
    passes an explicit directory and refuses one that escapes."""
    from arbiter import service
    root = tmp_path / "sandbox"
    root.mkdir()
    assert service.resolve_within("report", str(root), "output_dir") == (root / "report")
    for escape in ("../elsewhere", str(tmp_path / "elsewhere")):
        with pytest.raises(service.ServiceError, match="must stay beneath"):
            service.resolve_within(escape, str(root), "output_dir")


def test_a_workspace_keeps_source_and_output_apart_and_removes_itself():
    """Customer source must not outlive the scan, and output must not land in it."""
    from arbiter import service
    with service.Workspace() as ws:
        path, source, output = ws.path, ws.source, ws.output
        assert source.parent == path and output.parent == path
        assert output.parent != source
        assert source.is_dir() and output.is_dir()
    assert not path.exists(), "workspace survived the scan"


def test_an_uploaded_archive_cannot_escape_the_extraction_root(tmp_path):
    """Uploaded archives are hostile input.

    A link is the cheapest way to make a scan read a file outside the upload
    and quote it back in a finding's evidence snippet, so links are refused
    rather than resolved. `tarfile`'s `data` filter would cover much of this but
    arrived in 3.12, and this package supports 3.11.
    """
    import tarfile
    import zipfile

    from arbiter import service

    slip = tmp_path / "slip.zip"
    with zipfile.ZipFile(slip, "w") as zf:
        zf.writestr("../../escape.txt", "pwned")
    with pytest.raises(service.ServiceError, match="traversal|escapes"):
        service.extract_archive(slip, tmp_path / "out-slip")

    linked = tmp_path / "link.tar"
    with tarfile.open(linked, "w") as tf:
        info = tarfile.TarInfo("shadow")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/shadow"
        tf.addfile(info)
    with pytest.raises(service.ServiceError, match="links are not extracted"):
        service.extract_archive(linked, tmp_path / "out-link")

    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "out-link" / "shadow").exists()


def test_an_honest_archive_extracts_intact(tmp_path):
    """The refusals above are worthless if they also refuse ordinary uploads."""
    import tarfile

    from arbiter import service
    (tmp_path / "app.py").write_text("print(1)\n")
    archive = tmp_path / "repo.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(tmp_path / "app.py", arcname="repo/app.py")
    dest = service.extract_archive(archive, tmp_path / "out")
    assert (dest / "repo" / "app.py").read_text() == "print(1)\n"


def test_the_mcp_schemas_offer_only_profiles_that_stay_offline():
    """An agent should not be able to ask for the network by naming a profile."""
    from arbiter import mcp
    for tool in mcp.TOOLS:
        profile = tool["inputSchema"]["properties"].get("profile")
        if profile is not None:
            assert set(profile["enum"]) == {"offline", "ci"}


# ---------------------------------------------------------------------------
# Hosted API: manual key issuance, and keeping nothing (REQ-018)
# ---------------------------------------------------------------------------

def test_a_key_is_stored_only_as_a_hash(tmp_path):
    """The key file must not be worth stealing.

    A raw key is shown once at mint time and never written. If the file held
    usable keys, losing it would be a breach rather than a reissue.
    """
    from arbiter import api
    path = tmp_path / "keys.json"
    raw, record = api.mint_key("dana@acme.example", path)
    assert raw.startswith(api.KEY_PREFIX)
    assert raw not in path.read_text(), "the key file contains a usable key"
    assert record["user"] == "dana@acme.example"
    assert api.verify_key(raw, path)["id"] == record["id"]


def test_an_unknown_or_revoked_key_is_refused(tmp_path):
    """Revocation has to actually revoke, and a guess has to fail."""
    from arbiter import api
    path = tmp_path / "keys.json"
    raw, record = api.mint_key("pilot", path)
    assert api.verify_key(api.KEY_PREFIX + "not-a-real-key", path) is None
    assert api.verify_key("", path) is None
    assert api.revoke_key(record["id"], path) is True
    assert api.verify_key(raw, path) is None, "a revoked key still worked"
    assert api.revoke_key(record["id"], path) is False


def test_listing_keys_never_prints_a_secret(tmp_path):
    """`arbiter api key list` is something the owner will paste into a message."""
    from arbiter import api
    path = tmp_path / "keys.json"
    api.mint_key("one", path)
    api.mint_key("two", path)
    listed = api.list_keys(path)
    assert len(listed) == 2
    for record in listed:
        assert "sha256" not in record
        assert set(record) == {"id", "user", "created", "expires", "revoked",
                               "replaced", "state"}


def test_a_key_needs_the_user_it_is_scoped_to(tmp_path):
    """A key is scoped to a user, so an unnamed key has no scope at all."""
    from arbiter import api
    with pytest.raises(api.ServiceError, match="scoped to a user"):
        api.mint_key("   ", tmp_path / "keys.json")


def test_one_user_holds_one_key(tmp_path):
    """Two live keys for one person make the per-key caps meaningless.

    They also make revocation ambiguous: cutting someone off means finding
    every key they hold, and missing one leaves them in.
    """
    from arbiter import api
    path = tmp_path / "keys.json"
    first, _ = api.mint_key("dana@acme.example", path)
    with pytest.raises(api.ServiceError, match="already holds key"):
        api.mint_key("dana@acme.example", path)
    assert api.verify_key(first, path), "the refusal disturbed the existing key"
    # A different person is unaffected; the limit is per user, not global.
    other, _ = api.mint_key("sam@acme.example", path)
    assert api.verify_key(other, path)


def test_replacing_a_key_revokes_the_one_it_replaces(tmp_path):
    """Rotation is one act, so it cannot half-happen and leave two keys live."""
    from arbiter import api
    path = tmp_path / "keys.json"
    first, old = api.mint_key("dana@acme.example", path)
    second, new = api.mint_key("dana@acme.example", path, replace=True)
    assert api.verify_key(first, path) is None, "the replaced key still worked"
    assert api.verify_key(second, path)["id"] == new["id"]
    assert new["replaced"] == old["id"], "the record does not say what it replaced"
    assert api.active_key_for("dana@acme.example", path)["id"] == new["id"]


def test_a_revoked_users_key_can_be_reissued(tmp_path):
    """One live key is the rule; one key ever would strand anyone who lost theirs."""
    from arbiter import api
    path = tmp_path / "keys.json"
    _, first = api.mint_key("dana@acme.example", path)
    api.revoke_key(first["id"], path)
    assert api.active_key_for("dana@acme.example", path) is None
    raw, _ = api.mint_key("dana@acme.example", path)
    assert api.verify_key(raw, path)


def test_an_oversized_upload_is_refused(tmp_path, monkeypatch):
    """Refused before extraction, so a bomb never reaches the disk."""
    from arbiter import api, service
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 64)
    with service.Workspace() as ws:
        with pytest.raises(service.ServiceError, match="exceeds"):
            api._ingest(ws, b"x" * 65)
        assert not any(ws.source.iterdir()), "an oversized upload was written anyway"


@pytest.mark.slow
def test_a_hosted_scan_keeps_nothing_and_returns_no_server_paths(tmp_path):
    """The simplest answer to 'what do you hold of ours?' is 'nothing'.

    The report comes back in the response and the workspace goes away with it.
    Server paths are withheld too: they name a directory that no longer exists
    and would tell a caller about the machine.
    """
    import tarfile

    from arbiter import api
    (tmp_path / "billing.py").write_text('KEY = "sk_live_51H8xQ2LkdIwHu7ix' + "Z" * 20 + '"\n')
    archive = tmp_path / "repo.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(tmp_path / "billing.py", arcname="repo/billing.py")

    before = set(tmp_path.iterdir())
    result = api.handle_scan(archive.read_bytes(), only="secrets")
    assert result["finding_count"] >= 1, "fixture produced nothing, so this proves nothing"
    assert "report_path" not in result
    assert set(tmp_path.iterdir()) == before, "the scan left something behind"


def test_the_hosted_surface_exposes_no_verdict_endpoint():
    """Adding one should fail the build, not depend on a reviewer noticing."""
    from arbiter import api
    assert set(api.ENDPOINTS) == {"scan", "gate", "review_queue"}
    for name in dir(api):
        assert "adjudicat" not in name.lower(), f"api grew {name}"
    assert api.RETAINS_NOTHING is True


def test_the_api_says_how_to_install_itself_when_fastapi_is_absent():
    """A missing optional extra must produce an instruction, not a traceback."""
    from arbiter import api, service
    try:
        import fastapi  # noqa: F401 - imported only to learn whether the extra is installed
    except ImportError:
        with pytest.raises(service.ServiceError, match=r"arbiter-eval\[api\]"):
            api.create_app()
    else:
        assert api.create_app() is not None


# ---------------------------------------------------------------------------
# Hosted API: TLS is not optional (REQ-018)
# ---------------------------------------------------------------------------

def test_a_plaintext_request_is_refused():
    """Every request carries an API key and a copy of somebody's source.

    Over plaintext both are readable by anything on the path and the key is
    replayable forever, so there is no degraded mode -- plaintext is refused
    rather than served.
    """
    from arbiter import api, service
    api.require_tls("https")
    with pytest.raises(service.ServiceError, match="plaintext HTTP is refused"):
        api.require_tls("http")


def test_no_socket_this_project_opens_is_allowed_to_be_plaintext():
    """There is no proxy mode, because a proxy mode is a plaintext listener.

    The old arrangement bound a bare socket on loopback and took
    `X-Forwarded-Proto` as proof it was secure. A header is not a transport, so
    the flag is gone and the scheme the socket actually spoke is what decides.
    """
    import inspect
    import io
    import tokenize

    from arbiter import api, mcp

    def code_only(module):
        """The module with its comments and strings removed.

        Both modules explain this rule in prose, and prose saying a header is
        never consulted must not read as consulting it.
        """
        tokens = tokenize.generate_tokens(
            io.StringIO(inspect.getsource(module)).readline)
        return " ".join(tok.string for tok in tokens
                        if tok.type not in (tokenize.COMMENT, tokenize.STRING)).lower()

    for module in (api, mcp):
        source = code_only(module)
        assert "behind_proxy" not in source, f"{module.__name__} still has a proxy mode"
        assert "x-forwarded-proto" not in source, (
            f"{module.__name__} consults a header a client can invent")
        assert "proxy_headers" not in source, (
            f"{module.__name__} asks uvicorn to trust forwarded headers")


def test_the_server_refuses_to_start_without_tls(tmp_path):
    """A misconfiguration must fail at startup, not downgrade quietly."""
    from arbiter import api, service
    with pytest.raises(service.ServiceError, match="TLS is required"):
        api.check_tls_config(None, None)
    with pytest.raises(service.ServiceError, match="TLS is required"):
        api.check_tls_config("cert.pem", None)
    with pytest.raises(service.ServiceError, match="does not exist"):
        api.check_tls_config(str(tmp_path / "absent.pem"), str(tmp_path / "absent.key"))
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    cert.write_text("x")
    key.write_text("x")
    api.check_tls_config(str(cert), str(key))


def test_a_certificate_is_required_even_with_a_proxy_in_front(tmp_path):
    """The hop from the proxy to here is a socket too, and it has to be TLS."""
    from arbiter import api, service
    with pytest.raises(service.ServiceError, match="the hop from the proxy to here"):
        api.check_tls_config(None, None)


def test_every_response_carries_hsts():
    """A client that once reached us over TLS should refuse to try plaintext."""
    from arbiter import api
    assert "max-age=" in api.HSTS_HEADER
    assert int(api.HSTS_HEADER.split("max-age=")[1].split(";")[0]) >= 31536000
    assert "includeSubDomains" in api.HSTS_HEADER


# ---------------------------------------------------------------------------
# Hosted API: a key is a limited grant, not a permanent one (REQ-018)
# ---------------------------------------------------------------------------

def test_a_key_expires_by_default(tmp_path):
    """A key that never expires is a permanent grant to whoever ends up holding it.

    Ninety days is long enough not to be a nuisance and short enough that one
    pasted into a ticket and forgotten stops working on its own.
    """
    from arbiter import api
    path = tmp_path / "keys.json"
    raw, record = api.mint_key("pilot", path)
    assert record["expires"], "the default key never expires"
    assert api.verify_key(raw, path) is not None
    lifetime = api._parse_stamp(record["expires"]) - api._parse_stamp(record["created"])
    assert lifetime == api.DEFAULT_KEY_LIFETIME_DAYS * 86400


def test_an_expired_key_stops_working(tmp_path):
    """Expiry has to actually refuse, not merely be displayed."""
    import time

    from arbiter import api
    path = tmp_path / "keys.json"
    raw, record = api.mint_key("short", path, lifetime_days=1)
    aged = dict(record, expires=api._stamp(time.time() - 60))
    path.write_text(json.dumps({"version": 1, "keys": [aged]}))
    assert api.key_state(aged) == "expired"
    assert api.verify_key(raw, path) is None, "an expired key still worked"
    assert api.list_keys(path)[0]["state"] == "expired"


def test_a_permanent_key_is_possible_but_deliberate(tmp_path):
    """The exception exists; it just is not the default."""
    from arbiter import api
    path = tmp_path / "keys.json"
    raw, record = api.mint_key("build server", path, lifetime_days=None)
    assert record["expires"] is None
    assert api.verify_key(raw, path) is not None
    with pytest.raises(api.ServiceError, match="at least one day"):
        api.mint_key("nonsense", path, lifetime_days=0)


def test_a_key_is_throttled_after_its_hourly_allowance(tmp_path):
    """A leaked key should not be able to run scans indefinitely."""
    from arbiter import api
    limiter = api.RateLimiter(requests=3, window=3600, concurrent=2)
    for _ in range(3):
        limiter.check("k1")
    with pytest.raises(api.RateLimited) as caught:
        limiter.check("k1")
    assert caught.value.retry_after > 0
    # Another key is unaffected: the limit is per key, not global.
    limiter.check("k2")


def test_the_throttle_lets_a_key_through_once_its_window_passes(tmp_path):
    """A cap that never resets is a revocation, which is not what this is."""
    from arbiter import api
    limiter = api.RateLimiter(requests=2, window=60, concurrent=2)
    start = 1000.0
    limiter.check("k1", now=start)
    limiter.check("k1", now=start + 1)
    with pytest.raises(api.RateLimited):
        limiter.check("k1", now=start + 2)
    limiter.check("k1", now=start + 61)


def test_one_key_cannot_run_unlimited_scans_at_once(tmp_path):
    """A scan unpacks an archive and runs several analyzers; concurrency is the
    expensive axis, so it is capped separately from the hourly count."""
    from arbiter import api
    limiter = api.RateLimiter(requests=100, window=3600, concurrent=2)
    with limiter.slot("k1"):
        with limiter.slot("k1"):
            with pytest.raises(api.RateLimited, match="already has 2 scans"):
                with limiter.slot("k1"):
                    pass
            # A different key still has its own slots.
            with limiter.slot("k2"):
                pass
    # Slots are released even though an exception was raised inside one.
    with limiter.slot("k1"):
        pass


def test_the_whole_server_has_a_scan_ceiling_of_its_own(tmp_path):
    """The per-key cap multiplies by the number of testers; this one does not.

    Five people with two slots each is ten concurrent scans on one machine.
    The per-key limit protects testers from each other; this protects the
    machine from all of them at once.
    """
    from arbiter import api
    limiter = api.RateLimiter(requests=100, window=3600, concurrent=2, total=3)
    with limiter.slot("k1"), limiter.slot("k1"), limiter.slot("k2"):
        with pytest.raises(api.RateLimited, match="limit of 3 scans"):
            with limiter.slot("k3"):
                pass
    # Everything is handed back, so the ceiling is not a one-way ratchet.
    with limiter.slot("k3"):
        pass


def test_a_key_over_its_own_share_is_told_that_not_that_we_are_busy(tmp_path):
    """The two refusals mean different things to whoever reads them.

    "Wait for your own scan" is actionable; "the service is busy" when the
    caller is the one filling it would send them looking for a fault elsewhere.
    """
    from arbiter import api
    limiter = api.RateLimiter(requests=100, window=3600, concurrent=1, total=4)
    with limiter.slot("k1"):
        with pytest.raises(api.RateLimited, match="this key already has"):
            with limiter.slot("k1"):
                pass


def test_an_audit_line_says_who_called_and_never_what_was_in_their_code(tmp_path):
    """The one thing kept is about the caller, not about the upload.

    A log that quoted a finding would rebuild on disk, permanently, exactly what
    the request path deletes.
    """
    from arbiter import api
    path = tmp_path / "audit.log"
    log = api.AuditLog(path)
    key = {"id": "abc123def456", "user": "dana@acme.example"}
    entry = log.record("scan", key, status=200, bytes_in=4096)
    written = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(written) == 1 and written[0] == entry
    assert set(entry) == {"ts", "event", "key", "user", "status", "bytes_in", "ms"}
    assert entry["key"] == "abc123def456" and entry["user"] == "dana@acme.example"


def test_a_refused_key_is_audited_without_writing_the_key_down(tmp_path):
    """A rejected key is still somebody's near-miss secret.

    Recording that a key was refused is the useful part; recording which string
    was tried would put candidate credentials on disk.
    """
    from arbiter import api
    path = tmp_path / "audit.log"
    api.AuditLog(path).record("auth_failed", status=401)
    entry = json.loads(path.read_text().strip())
    assert entry["event"] == "auth_failed" and entry["status"] == 401
    assert entry["key"] == "" and entry["user"] == ""
