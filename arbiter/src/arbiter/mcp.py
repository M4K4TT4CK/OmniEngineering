"""Model Context Protocol surface: Arbiter as tools an agent can call.

This is one of two front doors over `service.py`, which holds the containment
rules. The other is the hosted API. Nothing about where output may go, whether
the network is reachable, or what a caller may not do is decided here -- that
would mean deciding it twice.

## What MCP does and does not deliver

It lets an agent run a scan, a gate and a review queue without a person typing
CLI commands, on a machine where Arbiter is already installed. It relocates the
installation rather than removing it. Removing it is what the hosted API is for
(REQ-018); this surface remains the right one for source that must not leave the
machine it sits on.

## Two transports, and why the second one is stricter

Over **stdio** the server is a subprocess of one agent on one machine. There is
no caller to identify: whoever started the process already has the privileges
the process has, so asking it for a key would be theatre.

Over **HTTPS** (`arbiter mcp --http`) that stops being true, and three things
change. Every call carries a key from the same file the hosted API reads, so
access is granted and revoked in one place. The resolved caller reaches
`dispatch`, so the per-key rate limit and the audit line apply exactly as they
do to a request to `/v1/scan`. And every path argument is confined.

The confinement is the part worth explaining, because it is the difference
between the two transports rather than a hardening detail. These tools take
`target` and `output_dir` as paths **on the machine running the server**. For a
local agent that is the whole point. For a remote caller it would be an
arbitrary file read: `target: "/etc"` would come back as findings quoting what
is in there, and `output_dir` would be somewhere to write. So the HTTPS
transport refuses to start without a `--root`, and rewrites every path argument
to sit beneath `root/<key id>` -- one directory per key, so one caller cannot
read another's source or output either. `service.resolve_within` resolves
symlinks before comparing, so a link pointing out of the sandbox is caught
rather than followed.

What that buys is a shared build machine several people can point an agent at.
What it does not buy is somebody scanning source that is not already on that
machine -- for that, the source has to travel, which is the hosted API and
`arbiter remote`.

## The tool that is deliberately absent

There is no tool that records a verdict, here or in `service.py`. `review
--apply` is outside the surface: an agent marking findings in a loop would fill
the calibration ledger with the model's opinion of the model's output, and
`learn.record()` refuses to re-adjudicate a fingerprint, so those marks would be
permanent. The server generates queues. A person marks them.

`arbiter_review_draft` proposes marks, and that is as far as any tool goes: it
writes the queue with an assistant's marks and reasons filled in, as a file
under `output_dir` that a person reads, and records nothing. The draft reaches
the ledger only through `arbiter review --apply` run by a person who names
themselves. Over HTTPS the tool writes under the caller's confined directory
like every other, so it is as safe there as `arbiter_review_queue`.
"""
from __future__ import annotations

import json
import sys
import time
from contextlib import AsyncExitStack
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from . import __version__
from .service import (
    EXIT_ERROR,
    EXIT_OK,
    ServiceError,
    gate,
    resolve_within,
    review_draft,
    review_queue,
    scan,
)

# Kept as an alias so callers and tests that catch the MCP-era name still work.
ToolError = ServiceError

# The schemas are data so tests can assert on them without the SDK installed --
# including asserting that no tool records a verdict.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "arbiter_scan",
        "description": "Analyse a repository for security, compliance, quality "
                       "and drift findings. Returns the full report JSON.",
        "inputSchema": {
            "type": "object",
            "required": ["target", "output_dir"],
            "properties": {
                "target": {"type": "string", "description": "path to the repository to scan"},
                "output_dir": {"type": "string",
                               "description": "directory for output; nothing is written outside it"},
                "profile": {"type": "string", "enum": ["offline", "ci"], "default": "offline",
                            "description": "both run with the network off"},
                "only": {"type": "string", "description": "comma-separated probes to run"},
                "skip": {"type": "string", "description": "comma-separated probes to skip"},
            },
        },
    },
    {
        "name": "arbiter_gate",
        "description": "Run the policy gate over a repository. Returns pass or "
                       "fail with the claim ledger.",
        "inputSchema": {
            "type": "object",
            "required": ["target", "output_dir"],
            "properties": {
                "target": {"type": "string"},
                "output_dir": {"type": "string"},
                "profile": {"type": "string", "enum": ["offline", "ci"], "default": "ci"},
                "only": {"type": "string"},
                "skip": {"type": "string"},
            },
        },
    },
    {
        "name": "arbiter_review_queue",
        "description": "Generate a queue of findings for a person to adjudicate. "
                       "Every mark is blank. This tool cannot record verdicts.",
        "inputSchema": {
            "type": "object",
            "required": ["report_path", "output_dir"],
            "properties": {
                "report_path": {"type": "string", "description": "path to a report.json"},
                "output_dir": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
                "rule": {"type": "string",
                         "description": "only findings whose rule id contains this"},
            },
        },
    },
    {
        "name": "arbiter_review_draft",
        "description": "Propose a mark and a reason for findings in the review "
                       "queue, written as review-draft.md for a person to read. "
                       "Nothing is recorded: only a person running the CLI with "
                       "their name records a mark, after reading the draft.",
        "inputSchema": {
            "type": "object",
            "required": ["report_path", "output_dir", "verdicts"],
            "properties": {
                "report_path": {"type": "string", "description": "path to a report.json"},
                "output_dir": {"type": "string",
                               "description": "directory for review-draft.md; nothing is "
                                              "written outside it"},
                "verdicts": {
                    "type": "array",
                    "description": "proposed marks; a finding not listed stays blank",
                    "items": {
                        "type": "object",
                        "required": ["id", "mark"],
                        "properties": {
                            "id": {"type": "string", "description": "finding id, f:..."},
                            "mark": {"type": "string", "enum": ["y", "n", "?"],
                                     "description": "y a real problem, n not one, ? unsure"},
                            "reason": {"type": "string",
                                       "description": "why; required for y and n, read by "
                                                      "the person before they decide"},
                        },
                    },
                },
                "limit": {"type": "integer", "default": 20},
                "rule": {"type": "string",
                         "description": "only findings whose rule id contains this"},
            },
        },
    },
]

HANDLERS = {
    "arbiter_scan": scan,
    "arbiter_gate": gate,
    "arbiter_review_queue": review_queue,
    "arbiter_review_draft": review_draft,
}

# Every argument naming a place on disk. Listed rather than guessed at from the
# value, because a tool gaining a path argument that nobody adds here would be
# unconfined on the HTTPS transport, and that failure would be silent.
PATH_ARGUMENTS = ("target", "output_dir", "report_path")

# Who is calling, for the duration of one HTTP request. Empty over stdio, where
# there is nobody to identify. The HTTP layer sets it; `_call` reads it and
# passes it on explicitly, so `dispatch` never has to consult ambient state.
CALLER: ContextVar[dict | None] = ContextVar("arbiter_mcp_caller", default=None)


def confine(arguments: dict, root: str | Path, caller: dict | None = None) -> dict:
    """Rewrite every path argument to sit beneath this caller's own directory.

    A path argument is what makes these tools useful locally and dangerous
    remotely: `target` is read and `output_dir` is written, both on the server.
    Each key gets `root/<key id>`, so confinement also separates callers from
    each other rather than merely keeping them all inside one shared box.

    A relative path is joined onto that directory, which makes the natural thing
    to send -- `"target": "myrepo"` -- also the correct one.
    """
    base = Path(root).expanduser().resolve()
    if caller and caller.get("id"):
        base = base / caller["id"]
    base.mkdir(parents=True, exist_ok=True)

    confined = dict(arguments)
    for field in PATH_ARGUMENTS:
        if field not in confined:
            continue
        value = str(confined[field] or "").strip()
        if not value:
            raise ServiceError(f"{field} is empty")
        confined[field] = str(resolve_within(value, str(base), field))
    return confined


def dispatch(name: str, arguments: dict, caller: dict | None = None,
             audit: Any = None, root: str | Path | None = None) -> dict:
    """Call one tool by name. Unknown names are refused, not guessed at.

    `caller` is the key record the HTTP transport resolved, and None over stdio.
    When there is one, this is where the per-key concurrency slot is taken and
    the audit line is written -- the same limiter and the same log the hosted API
    uses, so a key's budget is one budget rather than one per front door.
    """
    handler = HANDLERS.get(name)
    if handler is None:
        raise ServiceError(f"no such tool: {name}")

    arguments = dict(arguments or {})
    if root is not None:
        arguments = confine(arguments, root, caller)

    if caller is None:
        return handler(**arguments)

    from . import api

    log = audit if audit is not None else api.AUDIT
    event = "mcp_" + name.removeprefix("arbiter_")
    started = time.monotonic()
    try:
        with api.LIMITER.slot(caller["id"]):
            result = handler(**arguments)
    except api.RateLimited:
        # Checked before ServiceError because RateLimited subclasses it, and
        # "you asked for too much" is a different answer from "that was malformed".
        log.record(event, caller, status=429, started=started)
        raise
    except ServiceError:
        log.record(event, caller, status=400, started=started)
        raise
    except Exception:  # audited as a 500, then re-raised unchanged
        log.record(event, caller, status=500, started=started)
        raise
    log.record(event, caller, status=200, started=started)
    return result


def _build_server(audit: Any = None, root: str | Path | None = None):
    """The protocol server itself, shared by both transports.

    Built here rather than twice so that a tool added to `TOOLS` appears on
    stdio and over HTTPS alike, and so the two cannot drift apart in what they
    expose.

    `TOOLS` is splatted straight into `Tool`, which still accepts `inputSchema`
    under its newer `input_schema` name -- so the schemas stay plain data that a
    test can read without the SDK installed.

    The SDK changed how a `Server` learns its handlers: older releases took
    `on_list_tools`/`on_call_tool` as constructor keywords, current ones expect
    `@server.list_tools()`/`@server.call_tool()` decorators applied after
    construction. Picking one shape and pinning a version range in the manifest
    would drift the moment either side upgrades without the other -- this
    inspects the installed `Server.__init__` and calls whichever shape it
    actually declares, so a resinstalled or upgraded SDK is read, not guessed.
    """
    import asyncio
    import inspect

    from mcp.server import Server
    from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

    def dispatch_call_tool(name: str, arguments: dict | None) -> CallToolResult:
        # The caller was resolved by the HTTP layer before the protocol saw the
        # request, and is None over stdio. It is read here and passed on, so
        # `dispatch` takes it as an argument rather than consulting ambient state.
        caller = CALLER.get()
        try:
            result = dispatch(name, arguments or {}, caller, audit, root)
        except ServiceError as exc:
            # Flagged as an error rather than returned as ordinary text: a
            # refusal an agent reads as a result is a refusal it will act on.
            return CallToolResult(
                content=[TextContent(type="text", text=f"error: {exc}")],
                isError=True)
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result, indent=2))])

    if "on_list_tools" in inspect.signature(Server.__init__).parameters:
        async def on_list_tools(_context, _params=None) -> ListToolsResult:
            return ListToolsResult(tools=[Tool(**tool) for tool in TOOLS])

        async def on_call_tool(_context, params) -> CallToolResult:
            return await asyncio.to_thread(
                dispatch_call_tool, params.name, params.arguments)

        return Server("arbiter", version=__version__,  # type: ignore[call-arg] - mcp 2.2 takes the handlers here; older stubs do not know them
                      on_list_tools=on_list_tools, on_call_tool=on_call_tool)

    server = Server("arbiter", version=__version__)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [Tool(**tool) for tool in TOOLS]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> CallToolResult:
        return await asyncio.to_thread(dispatch_call_tool, name, arguments)

    return server


def serve() -> int:
    """Run the MCP server on stdio.

    The SDK is imported here rather than at module scope so the schemas and the
    dispatch stay importable -- and testable -- without it. `mcp` is an optional
    extra; the core install is PyYAML alone.
    """
    try:
        from mcp.server.stdio import stdio_server
    except ImportError:
        print("arbiter: the MCP server needs the optional 'mcp' dependency.\n"
              "         pip install 'arbiter-eval[mcp]'", file=sys.stderr)
        return EXIT_ERROR

    import asyncio

    server = _build_server()

    async def _main() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_main())
    return EXIT_OK


# --------------------------------------------------------------------------
# The HTTPS transport, for more than one caller
# --------------------------------------------------------------------------
#
# Everything below exists because stdio serves exactly one agent on one machine.
# The rules it adds are not new policy: they are `api.py`'s rules, reached from
# here, so that a key means the same thing and is revoked in one place whichever
# door it arrives at.

def _headers_from(scope: dict) -> dict[str, str]:
    """ASGI headers as a lowercase mapping. Duplicates keep the first."""
    out: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers", []):
        name = raw_name.decode("latin-1").lower()
        if name not in out:
            out[name] = raw_value.decode("latin-1")
    return out


def token_from(headers: dict[str, str]) -> str:
    """The key a caller presented, by either accepted name.

    MCP clients send `Authorization: Bearer`; everything else that talks to
    Arbiter sends `X-API-Key`. Accepting both costs nothing and spares a person
    finding out which one this server wanted by being refused.
    """
    auth = headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return headers.get("x-api-key", "").strip()


async def _refuse(send, status: int, detail: str,
                  extra: list[tuple[bytes, bytes]] | None = None) -> None:
    """End a request with a JSON reason rather than a bare status."""
    from .api import HSTS_HEADER

    body = json.dumps({"detail": detail}).encode("utf-8")
    headers = [(b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode("ascii")),
               (b"strict-transport-security", HSTS_HEADER.encode("ascii"))]
    headers.extend(extra or [])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def build_http_app(key_path: Path | None = None, root: str | Path | None = None,
                   audit: Any = None,
                   path: str = "/mcp", json_response: bool = False,
                   host: str = "127.0.0.1",
                   allowed_hosts: list[str] | None = None) -> Any:
    """The ASGI application: TLS, then a key, then the protocol.

    Separate from `serve_http` so the whole request path can be tested without
    binding a socket or holding a certificate.

    `root` is required. Without it every authenticated caller could name any
    path the server process can read, and an MCP tool would be a file-read
    primitive with a scanner attached.

    The app it returns can be served once: the SDK's session manager refuses a
    second `run()`, so a test wanting two clients builds two apps.
    """
    from . import api

    if not root:
        raise ServiceError(
            "the HTTP transport needs --root: a directory that every caller's "
            "paths must stay inside. Without one, a key would let anybody read "
            "any file this server can. Stdio needs no root because it serves "
            "one agent on the machine it already runs on."
        )
    base = Path(root).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)

    log = audit if audit is not None else api.AUDIT
    server = _build_server(log, base)

    # The transport checks the Host header as its defence against DNS
    # rebinding, and defaults to allowing loopback only. That default is wrong
    # whenever a proxy sits in front: the proxy forwards the public hostname,
    # which is not a loopback name, so every real request comes back 421. Naming
    # the hostname with --allowed-host is what that arrangement needs, and it
    # cannot be inferred from here -- a forwarded request looks like any other.
    #
    # The SDK's own default (`security_settings=None`) disables this guard
    # entirely, kept only for callers that never asked for it. Arbiter always
    # asked for it, so an explicit loopback-only setting is built here instead
    # of inheriting a permissive default the moment it changes upstream.
    from mcp.server.transport_security import TransportSecuritySettings

    if allowed_hosts:
        hosts = list(allowed_hosts)
    else:
        # No explicit list: fall back to the bind address and localhost, which
        # is right for a local instance and matches this transport's own
        # stated default of "loopback only".
        hosts = [host, "localhost", "127.0.0.1"]
    # Each name is allowed on any port as well as bare, because the port a
    # proxy forwards is not something the operator should have to predict.
    names = [entry for host_name in hosts for entry in (host_name, f"{host_name}:*")]
    security_settings = TransportSecuritySettings(
        allowed_hosts=names,
        allowed_origins=[f"https://{name}" for name in names])

    # Stateless: no session is kept between requests, so there is nothing
    # holding one caller's state for another to resume, and nothing to expire.
    # It matches what the rest of the service promises -- that it keeps nothing.
    #
    # The SDK dropped `Server.streamable_http_app()`, the single-call helper
    # this used to build on, in favour of wiring a `StreamableHTTPSessionManager`
    # by hand. That manager owns a task group that must live for exactly the
    # ASGI app's lifespan, so this app answers `lifespan` itself instead of
    # delegating it, entering the manager's `run()` on startup and leaving it
    # on shutdown.
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    session_manager = StreamableHTTPSessionManager(
        app=server, json_response=json_response, stateless=True,
        security_settings=security_settings)

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            async with AsyncExitStack() as stack:
                while True:
                    message = await receive()
                    if message["type"] == "lifespan.startup":
                        try:
                            await stack.enter_async_context(session_manager.run())
                        except Exception as exc:
                            await send({"type": "lifespan.startup.failed",
                                       "message": str(exc)})
                            return
                        await send({"type": "lifespan.startup.complete"})
                    elif message["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return

        if scope["type"] != "http":
            return

        if scope["path"] != path:
            await _refuse(send, 404, "not found")
            return

        headers = _headers_from(scope)
        try:
            api.require_tls(scope.get("scheme", ""))
        except ServiceError as exc:
            # 426 Upgrade Required: the request was understood and the transport
            # is what is wrong, which is exactly the case.
            await _refuse(send, 426, str(exc))
            return

        record = api.verify_key(token_from(headers), key_path)
        if record is None:
            # One message for unknown, revoked and expired alike, and the
            # refused key is not written down: a near-miss secret on disk is
            # worse than a thinner log line.
            log.record("mcp_auth_failed", status=401)
            await _refuse(send, 401, "unknown, revoked or expired API key",
                          [(b"www-authenticate", b'Bearer realm="arbiter"')])
            return
        try:
            api.LIMITER.check(record["id"])
        except api.RateLimited as exc:
            log.record("mcp_rate_limited", record, status=429)
            await _refuse(send, 429, str(exc),
                          [(b"retry-after", str(exc.retry_after).encode("ascii"))])
            return

        async def hsts(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append(
                    (b"strict-transport-security", api.HSTS_HEADER.encode("ascii")))
            await send(message)

        reset = CALLER.set(record)
        try:
            await session_manager.handle_request(scope, receive, hsts)
        finally:
            CALLER.reset(reset)

    return app


def serve_http(host: str = "127.0.0.1", port: int = 8444,
               key_path: Path | None = None, root: str | Path | None = None,
               certfile: str | None = None, keyfile: str | None = None,
               audit_path: str | None = None,
               audit: bool = True, path: str = "/mcp",
               allowed_hosts: list[str] | None = None,
               limiter: str = "memory") -> int:
    """Serve the MCP tools over TLS to more than one caller.

    Like the hosted API, there is no plaintext mode and no plaintext port: this
    process holds the certificate, including when a proxy sits in front of it.

    `limiter` is `memory` for one process or `file` for several processes that
    serve the same key file and should share one budget (`api.FileRateLimiter`).
    """
    from . import api

    # Before anything else, and before the dependency check: an operator whose
    # arrangement would have served plaintext should be told that, not told it
    # only after they have fixed an unrelated install.
    api.check_tls_config(certfile, keyfile)
    api.configure_limiter(limiter, key_path)

    try:
        import uvicorn
    except ImportError:
        print("arbiter: the MCP server over HTTP needs the optional 'mcp' "
              "dependency.\n         pip install 'arbiter-eval[mcp]'", file=sys.stderr)
        return EXIT_ERROR

    log = api.AuditLog(Path(audit_path).expanduser() if audit_path else None,
                       enabled=audit)
    try:
        app = build_http_app(key_path, root, audit=log, path=path, host=host,
                             allowed_hosts=allowed_hosts)
    except ImportError:
        print("arbiter: the MCP server over HTTP needs the optional 'mcp' "
              "dependency.\n         pip install 'arbiter-eval[mcp]'", file=sys.stderr)
        return EXIT_ERROR

    where = Path(root).expanduser().resolve()
    print(f"arbiter: every caller's paths stay under {where}, one directory per key")
    if audit:
        print(f"arbiter: request lines go to {log.path or api.default_audit_path()} "
              "(who called and how it ended; never their code)")
    else:
        print("arbiter: auditing is off; no record of who called will be kept")
    if limiter == "file":
        print(f"arbiter: rate limits are shared through {api.LIMITER.path}")

    # The cipher list matches `api.serve`, and for the same reason: uvicorn
    # builds its own SSL context and exposes no minimum-version hook, so
    # restricting to forward-secret AEAD suites is what can actually be asserted
    # from here.
    print(f"arbiter: serving MCP on https://{host}:{port}{path}")
    uvicorn.run(app, host=host, port=port, ssl_certfile=certfile,
                ssl_keyfile=keyfile,
                ssl_ciphers="ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM")
    return EXIT_OK
