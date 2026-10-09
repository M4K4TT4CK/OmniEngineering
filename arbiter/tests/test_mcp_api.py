"""The MCP server over stdio and over HTTPS: keys, limits, audit and path
confinement (REQ-010).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# --------------------------------------------------------------------------
# MCP for more than one caller (REQ-010)
#
# Over stdio the server is a subprocess of one agent on one machine, and there
# is nobody to identify. Over HTTPS that stops being true, and these cover the
# three things that change: a key on every call, per-key limits and audit, and
# confinement of the path arguments -- which over a network are otherwise an
# arbitrary file read with a scanner attached.
# --------------------------------------------------------------------------

def _mcp_app(tmp_path, key_path=None, audit=None, **kw):
    """The MCP HTTP application, wired the way a deployment wires it.

    A fresh one per client: the SDK's session manager refuses a second `run()`,
    so an app that has been served once cannot be served again.
    """
    pytest.importorskip("mcp", reason="the mcp extra is not installed")
    pytest.importorskip("httpx2", reason="starlette's test client needs httpx2")
    from arbiter import api, mcp
    kw.setdefault("allowed_hosts", ["testserver"])
    return mcp.build_http_app(key_path or (tmp_path / "keys.json"),
                              tmp_path / "root",
                              audit=audit or api.AuditLog(enabled=False), **kw)


def _mcp_client(app, scheme="https"):
    from starlette.testclient import TestClient
    return TestClient(app, base_url=f"{scheme}://testserver")


def _initialize():
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}}}


_MCP_HEADERS = {"Accept": "application/json, text/event-stream",
                "Content-Type": "application/json"}


def test_an_mcp_call_over_http_without_a_usable_key_gets_nowhere(tmp_path):
    """Stdio can skip authentication because the caller already owns the
    machine. Over a network that reasoning evaporates, and the same file the
    hosted API reads is what decides who may call."""
    from arbiter import api
    keys = tmp_path / "keys.json"
    api.mint_key("dana@acme.example", keys)
    with _mcp_client(_mcp_app(tmp_path, keys)) as client:
        for headers in ({}, {"X-API-Key": "arb_not-a-real-key"},
                        {"Authorization": "Bearer arb_not-a-real-key"}):
            response = client.post("/mcp", json=_initialize(), headers=headers)
            assert response.status_code == 401, response.text
            # The same sentence for unknown, revoked and expired alike: saying
            # which would confirm that a guessed key once existed.
            assert response.json()["detail"] == "unknown, revoked or expired API key"


def test_a_key_works_over_mcp_and_stops_the_moment_it_is_revoked(tmp_path):
    """One key store behind both front doors. Revoking access has to mean
    revoking it, not revoking it on the door somebody remembered."""
    from arbiter import api
    keys = tmp_path / "keys.json"
    raw, record = api.mint_key("dana@acme.example", keys)
    with _mcp_client(_mcp_app(tmp_path, keys)) as client:
        headers = {"X-API-Key": raw, **_MCP_HEADERS}
        assert client.post("/mcp", json=_initialize(), headers=headers).status_code == 200
        api.revoke_key(record["id"], keys)
        assert client.post("/mcp", json=_initialize(), headers=headers).status_code == 401


def test_mcp_over_http_accepts_the_key_by_either_name(tmp_path):
    """MCP clients send `Authorization: Bearer`; everything else that talks to
    Arbiter sends `X-API-Key`. Refusing one of them would only teach people
    which by making them fail first."""
    from arbiter import api
    keys = tmp_path / "keys.json"
    raw, _ = api.mint_key("dana@acme.example", keys)
    for header in ({"X-API-Key": raw}, {"Authorization": f"Bearer {raw}"}):
        with _mcp_client(_mcp_app(tmp_path, keys)) as client:
            response = client.post("/mcp", json=_initialize(),
                                   headers={**header, **_MCP_HEADERS})
            assert response.status_code == 200, response.text


def test_mcp_over_plaintext_is_refused_like_every_other_surface(tmp_path):
    """A key in a header and a path to somebody's source, in the clear."""
    from arbiter import api
    keys = tmp_path / "keys.json"
    raw, _ = api.mint_key("dana@acme.example", keys)
    with _mcp_client(_mcp_app(tmp_path, keys), scheme="http") as client:
        response = client.post("/mcp", json=_initialize(),
                               headers={"X-API-Key": raw, **_MCP_HEADERS})
        assert response.status_code == 426
        assert "HTTPS" in response.json()["detail"]
        assert response.headers["Strict-Transport-Security"]


def test_a_served_mcp_response_carries_hsts_as_well_as_a_refused_one(tmp_path):
    """A client that once reached us over TLS should refuse to try plaintext
    afterwards, and that only holds if the header is on the success path too."""
    from arbiter import api
    keys = tmp_path / "keys.json"
    raw, _ = api.mint_key("dana@acme.example", keys)
    with _mcp_client(_mcp_app(tmp_path, keys)) as client:
        response = client.post("/mcp", json=_initialize(),
                               headers={"X-API-Key": raw, **_MCP_HEADERS})
        assert response.status_code == 200
        assert response.headers["Strict-Transport-Security"] == api.HSTS_HEADER


def test_a_remote_mcp_caller_cannot_name_a_path_outside_its_own_directory(tmp_path):
    """The difference between the two transports, and the reason the HTTP one
    exists in this shape. `target` is read and `output_dir` is written, both on
    the server: unconfined, `target: "/etc"` is an arbitrary file read that
    comes back as findings quoting what is in there."""
    from arbiter import mcp
    from arbiter.service import ServiceError
    root = tmp_path / "root"
    dana = {"id": "aaaaaaaaaaaa", "user": "dana@acme.example"}
    sam = {"id": "bbbbbbbbbbbb", "user": "sam@acme.example"}

    inside = mcp.confine({"target": "myrepo", "output_dir": "out"}, root, dana)
    assert Path(inside["target"]) == (root / dana["id"] / "myrepo").resolve()

    for escape in ("/etc", "../../elsewhere", str(tmp_path)):
        with pytest.raises(ServiceError):
            mcp.confine({"target": escape}, root, dana)

    # And not into each other's, which is what makes this multi-user rather
    # than merely sandboxed.
    theirs = mcp.confine({"target": "myrepo"}, root, sam)
    assert Path(theirs["target"]) != Path(inside["target"])
    with pytest.raises(ServiceError):
        mcp.confine({"target": f"../{dana['id']}/myrepo"}, root, sam)


def test_every_path_argument_any_tool_takes_is_confined(tmp_path):
    """A tool that gained a path argument nobody added to `PATH_ARGUMENTS`
    would be unconfined, and silently so. This is the check that notices."""
    from arbiter import mcp
    named = set(mcp.PATH_ARGUMENTS)
    for tool in mcp.TOOLS:
        for field in tool["inputSchema"]["properties"]:
            if field.endswith(("_dir", "_path")) or field == "target":
                assert field in named, f"{tool['name']}.{field} is not confined"


def test_the_http_transport_will_not_start_without_somewhere_to_confine_to(tmp_path):
    """Refusing at startup rather than defaulting to the filesystem root: a
    default here would be a quiet grant of everything the process can read."""
    from arbiter import mcp
    from arbiter.service import ServiceError
    pytest.importorskip("mcp", reason="the mcp extra is not installed")
    with pytest.raises(ServiceError) as caught:
        mcp.build_http_app(tmp_path / "keys.json", root=None)
    assert "--root" in str(caught.value)


def test_the_transport_accepts_the_hostname_a_proxy_forwards(tmp_path):
    """A proxy in front forwards the public hostname, and the transport's
    DNS-rebinding guard allows loopback names only until it is told otherwise.
    Left alone that arrangement 421s every real request, so `--allowed-host` has
    to reach the guard -- on the bare name and on any port."""
    from arbiter import api
    keys = tmp_path / "keys.json"
    raw, _ = api.mint_key("dana@acme.example", keys)
    headers = dict(_MCP_HEADERS, Authorization=f"Bearer {raw}")

    # A hostname the guard was not told about, sent by a caller whose key is
    # good: the refusal is the transport's, not the key check's.
    elsewhere = _mcp_app(tmp_path, key_path=keys, allowed_hosts=["arbiter.example.com"])
    with _mcp_client(elsewhere) as client:
        refused = client.post("/mcp", json=_initialize(), headers=headers)
    assert refused.status_code == 421, refused.text

    named = _mcp_app(tmp_path, key_path=keys, allowed_hosts=["testserver"])
    with _mcp_client(named) as client:
        answered = client.post("/mcp", json=_initialize(), headers=headers)
    assert answered.status_code != 421, answered.text


def test_an_authenticated_tool_call_is_limited_and_audited_per_key(tmp_path, monkeypatch):
    """What "the caller reaches dispatch" is actually for: a key's budget is one
    budget across both front doors, and the line saying who called is written
    for MCP exactly as it is for a request to /v1/scan."""
    from arbiter import api, mcp
    log = api.AuditLog(tmp_path / "audit.log")
    caller = {"id": "cccccccccccc", "user": "dana@acme.example"}
    seen = {}

    def fake_scan(**kwargs):
        seen.update(kwargs)
        return {"finding_count": 0}

    monkeypatch.setitem(mcp.HANDLERS, "arbiter_scan", fake_scan)
    result = mcp.dispatch("arbiter_scan", {"target": "myrepo", "output_dir": "out"},
                          caller=caller, audit=log, root=tmp_path / "root")
    assert result == {"finding_count": 0}
    # The handler was handed confined paths, not the ones the caller sent.
    assert Path(seen["target"]).is_relative_to((tmp_path / "root" / caller["id"]).resolve())

    written = [json.loads(line) for line in
               (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()]
    assert written[-1]["event"] == "mcp_scan"
    assert written[-1]["key"] == caller["id"]
    assert written[-1]["user"] == "dana@acme.example"
    assert written[-1]["status"] == 200


def test_a_tool_that_refuses_is_audited_as_a_refusal(tmp_path, monkeypatch):
    """A log holding only successes cannot show somebody hammering the service."""
    from arbiter import api, mcp
    from arbiter.service import ServiceError
    log = api.AuditLog(tmp_path / "audit.log")
    caller = {"id": "dddddddddddd", "user": "sam@acme.example"}

    def refuses(**kwargs):
        raise ServiceError("no")

    monkeypatch.setitem(mcp.HANDLERS, "arbiter_gate", refuses)
    with pytest.raises(ServiceError):
        mcp.dispatch("arbiter_gate", {"target": "r", "output_dir": "o"},
                     caller=caller, audit=log, root=tmp_path / "root")
    entry = json.loads((tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()[-1])
    assert entry["event"] == "mcp_gate" and entry["status"] == 400


def test_stdio_still_dispatches_with_nobody_to_identify(tmp_path, monkeypatch):
    """The local case must not acquire a key requirement as a side effect of the
    remote one existing. Whoever started the subprocess already has the
    privileges the subprocess has."""
    from arbiter import mcp
    monkeypatch.setitem(mcp.HANDLERS, "arbiter_scan", lambda **kw: {"ran": kw["target"]})
    assert mcp.dispatch("arbiter_scan", {"target": "/anywhere/at/all",
                                         "output_dir": "/tmp/out"}) == {
        "ran": "/anywhere/at/all"}


def test_the_tool_schemas_still_build_against_the_installed_sdk():
    """`TOOLS` is plain data so tests can read it without the SDK, which means
    nothing else notices if the SDK's own models stop matching it. The SDK's
    pydantic models take a `camelCase` alias on construction (`inputSchema`,
    matching `TOOLS`' own wire-shaped keys) but expose the field back out as
    the `snake_case` Python attribute (`input_schema`) via an alias generator
    -- reading the alias name back off a built object is the mistake, not a
    version drift, and this is the one place that would catch either."""
    pytest.importorskip("mcp", reason="the mcp extra is not installed")
    from mcp.types import Tool

    from arbiter import mcp as surface
    for tool in surface.TOOLS:
        built = Tool(**tool)
        assert built.name == tool["name"]
        assert built.input_schema == tool["inputSchema"]


def test_the_stdio_server_actually_starts_and_answers_a_real_client(tmp_path):
    """Everything else about stdio is tested by construction: `dispatch` is
    called directly, and `TOOLS` is checked against the SDK's `Tool`. Neither
    starts the server, which is how `serve()` sat broken against the installed
    SDK -- handlers moved from decorators to constructor arguments at 2.0 and
    nothing noticed, because nothing ever spoke to it.

    So this one starts `arbiter mcp` as a subprocess and talks MCP down the
    pipes: initialize, list the tools, call one that succeeds, call one that
    refuses. Slower than the rest of the suite, and the only test that would
    have caught that failure.
    """
    pytest.importorskip("mcp", reason="the mcp extra is not installed")
    import asyncio
    import os
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    repo = Path(__file__).resolve().parents[1]
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"findings": []}), encoding="utf-8")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "import sys; from arbiter.cli import main; sys.exit(main(['mcp']))"],
        cwd=str(repo),
        env={**os.environ, "PYTHONPATH": "src", "PYTHONIOENCODING": "utf-8"},
    )

    async def talk():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                started = await session.initialize()
                listed = await session.list_tools()
                worked = await session.call_tool("arbiter_review_queue", {
                    "report_path": str(report),
                    "output_dir": str(tmp_path / "out")})
                refused = await session.call_tool("arbiter_review_queue", {
                    "report_path": str(tmp_path / "absent.json"),
                    "output_dir": str(tmp_path / "out")})
                return started, listed, worked, refused

    started, listed, worked, refused = asyncio.run(talk())

    from arbiter import mcp
    assert started.server_info.name == "arbiter"
    assert {tool.name for tool in listed.tools} == set(mcp.HANDLERS)

    # An empty report is a legitimate thing to send and comes back as an empty
    # queue -- a result, not a failure, and with no mark in it.
    assert worked.is_error is False
    assert json.loads(worked.content[0].text)["entry_count"] == 0

    # And a refusal arrives as a refusal rather than as text an agent would read
    # back as a finding.
    assert refused.is_error is True
    assert "no report at" in refused.content[0].text


def test_the_mcp_surface_still_records_no_verdict_over_http():
    """The refusal that has to survive every new transport."""
    from arbiter import mcp
    public = {n for n in dir(mcp) if not n.startswith("_")}
    for banned in ("record", "apply", "adjudicate", "verdict", "feedback"):
        assert not any(banned in n.lower() for n in public), f"{banned} is reachable"
    assert set(mcp.HANDLERS) == {"arbiter_scan", "arbiter_gate", "arbiter_review_queue", "arbiter_review_draft"}
