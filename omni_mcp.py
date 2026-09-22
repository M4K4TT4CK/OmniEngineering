"""A minimal MCP (Model Context Protocol) server over stdio, exposing the graph and registries as tools.

`omni graph why/lineage/trace/...` and `omni requirement/failure show/list/search` already return plain JSON
(`--json`); this is the same data, reachable without shelling out to the CLI and parsing its output, so any
MCP-speaking assistant -- not only the one with a shell tool, and not only Claude -- can query this project's
requirements, changelog, commits, tests and failures the same way `omni graph` does. That is the whole point:
context management here is not supposed to be one assistant's private trick.

Deliberately hand-rolled against the wire protocol (JSON-RPC 2.0, newline-delimited, over stdin/stdout) rather
than depending on an MCP SDK: this workspace works across assistants, machines and Python versions with only
the standard library, and a stdio server is a small enough surface that a dependency would cost more than it
saves. Every tool here is read-only -- it inspects the graph, the registries or the pending change set, and
never writes anything -- so a client can call them without a confirmation step. `omni gate --hook` (and
`omni requirement draft`, `omni requirement add`, ...) remain the way anything gets *written*.

Stdlib only. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, TextIO

import make_ai as ma
import omni_graph as og

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "omniengineering-graph"
SERVER_VERSION = "1.0.0"


class MCPTool:
    def __init__(self, name: str, description: str, input_schema: dict[str, Any], handler: Callable[[dict[str, Any]], Any]) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.handler = handler

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema}


def _graph_path(arguments: dict[str, Any]) -> Path:
    return Path(str(arguments.get("graph") or ma.GRAPH_DEFAULT_OUTPUT))


def _require_graph_file(path: Path) -> None:
    if not path.is_file():
        raise ToolError(f"Graph file not found: {path}. Run `omni graph build` first (or pass a different `graph` path).")


def _tool_graph_why(arguments: dict[str, Any]) -> Any:
    path = _graph_path(arguments)
    _require_graph_file(path)
    return og.why(path, str(arguments["node"]))


def _tool_graph_lineage(arguments: dict[str, Any]) -> Any:
    path = _graph_path(arguments)
    _require_graph_file(path)
    direction = str(arguments.get("direction", "both"))
    if direction not in ("up", "down", "both"):
        raise ToolError("direction must be one of: up, down, both")
    return og.lineage(
        path, str(arguments["node"]), direction=direction,
        depth=int(arguments.get("depth", 6)), include_code=bool(arguments.get("code", False)),
        limit=int(arguments.get("max_nodes", 400)),
    )


def _tool_graph_trace(arguments: dict[str, Any]) -> Any:
    path = _graph_path(arguments)
    _require_graph_file(path)
    return og.trace(path, str(arguments["source"]), str(arguments["target"]))


def _tool_graph_timeline(arguments: dict[str, Any]) -> Any:
    path = _graph_path(arguments)
    _require_graph_file(path)
    return og.timeline(path, str(arguments["node"]), depth=int(arguments.get("depth", 1)))


def _tool_graph_show(arguments: dict[str, Any]) -> Any:
    path = _graph_path(arguments)
    _require_graph_file(path)
    return og.show(path, str(arguments["node"]))


def _tool_graph_sources(arguments: dict[str, Any]) -> Any:
    return og.describe_sources(Path(str(arguments.get("root", "."))))


def _tool_requirement_show(arguments: dict[str, Any]) -> Any:
    found = ma.find_requirement(str(arguments["id"]))
    if found is None:
        raise ToolError(f"Requirement not found: {arguments['id']}")
    path, _registry, item = found
    result = dict(item)
    result["archived"] = path == ma.REQUIREMENTS_ARCHIVE_PATH
    return result


def _tool_requirement_list(arguments: dict[str, Any]) -> Any:
    items, archived_count = ma.load_requirement_items(include_archive=bool(arguments.get("all", False)))
    statuses = arguments.get("status")
    if statuses:
        wanted = {statuses} if isinstance(statuses, str) else set(statuses)
        items = [item for item in items if item.get("status") in wanted]
    last = arguments.get("last")
    if last:
        items = items[-int(last):]
    return {"requirements": items, "shown": len(items), "archived_not_shown": archived_count if not arguments.get("all") else 0}


def _tool_requirement_search(arguments: dict[str, Any]) -> Any:
    needle = str(arguments["text"]).lower()
    items, _ = ma.load_requirement_items(include_archive=not bool(arguments.get("active_only", False)))
    matches = []
    for item in items:
        haystacks = {
            "id": str(item.get("id", "")), "title": str(item.get("title", "")), "category": str(item.get("category", "")),
            "description": str(item.get("description", "")),
            "risk_notes": " ".join(str(v) for v in (item.get("risk_notes") or [])),
            "acceptance_criteria": " ".join(str(v) for v in (item.get("acceptance_criteria") or [])),
        }
        matched_fields = [name for name, text in haystacks.items() if needle in text.lower()]
        if matched_fields:
            matches.append({"id": item.get("id"), "status": item.get("status"), "title": item.get("title"), "matched_fields": matched_fields})
    return {"matches": matches, "count": len(matches)}


def _tool_failure_show(arguments: dict[str, Any]) -> Any:
    ledger = ma.load_failure_ledger()
    found = ma.find_failure(ledger, str(arguments["id"]))
    if found is None:
        raise ToolError(f"Failure not found: {arguments['id']}")
    return found


def _tool_gate_status(arguments: dict[str, Any]) -> Any:
    """The same check `omni gate` runs, as data: never writes a waiver, never exits the process, never blocks."""
    base = ma.gate_base_commit()
    changed = sorted(ma.gate_changed_paths(base))
    if not changed:
        return {"pass": True, "changed_paths": [], "failures": [], "waived": []}
    doctor = ma.build_doctor_report()
    failures = [f"doctor: {message}" for message in doctor.errors]
    rule_failures, waived = ma.gate_evaluate(set(changed), ma.gate_waivers(base), base)
    failures.extend(rule_failures)
    return {"pass": not failures, "changed_paths": changed, "failures": failures, "waived": waived}


class ToolError(Exception):
    """A well-formed refusal (not found, ambiguous, missing graph) -- reported to the client, not a crash."""


TOOLS: list[MCPTool] = [
    MCPTool(
        "graph_lineage",
        "Trace everything upstream (what led to a node) and downstream (what came from it) with no second "
        "endpoint: pick a requirement, commit, change-log entry, failure, file or symbol.",
        {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Requirement id (REQ-021), commit hash, failure id (FAIL-003), file path or symbol."},
                "direction": {"type": "string", "enum": ["up", "down", "both"], "default": "both"},
                "depth": {"type": "integer", "default": 6, "description": "How many links to follow."},
                "code": {"type": "boolean", "default": False, "description": "Also follow calls, imports, inheritance and table mappings between code symbols."},
                "max_nodes": {"type": "integer", "default": 400, "description": "Most nodes returned per direction, nearest first."},
                "graph": {"type": "string", "description": f"Graph file to read. Defaults to {ma.GRAPH_DEFAULT_OUTPUT}."},
            },
            "required": ["node"],
        },
        _tool_graph_lineage,
    ),
    MCPTool(
        "graph_why",
        "Cross-layer traversal for a file, symbol, requirement or failure: the requirements, changelog "
        "entries, commits, tests, failures and rules that touch it.",
        {"type": "object", "properties": {
            "node": {"type": "string", "description": "File path, symbol, requirement id (REQ-021) or failure id (FAIL-001)."},
            "graph": {"type": "string", "description": f"Graph file to read. Defaults to {ma.GRAPH_DEFAULT_OUTPUT}."},
        }, "required": ["node"]},
        _tool_graph_why,
    ),
    MCPTool(
        "graph_trace",
        "The shortest chain of links between two nodes in the graph, with the steps in between.",
        {"type": "object", "properties": {
            "source": {"type": "string"}, "target": {"type": "string"},
            "graph": {"type": "string", "description": f"Graph file to read. Defaults to {ma.GRAPH_DEFAULT_OUTPUT}."},
        }, "required": ["source", "target"]},
        _tool_graph_trace,
    ),
    MCPTool(
        "graph_timeline",
        "Chronology of the commits, changelog entries and failures tied to a file, symbol, requirement or failure, oldest first.",
        {"type": "object", "properties": {
            "node": {"type": "string"}, "depth": {"type": "integer", "default": 1},
            "graph": {"type": "string", "description": f"Graph file to read. Defaults to {ma.GRAPH_DEFAULT_OUTPUT}."},
        }, "required": ["node"]},
        _tool_graph_timeline,
    ),
    MCPTool(
        "graph_show",
        "One node's full record plus every outgoing and incoming edge, unfiltered.",
        {"type": "object", "properties": {
            "node": {"type": "string"},
            "graph": {"type": "string", "description": f"Graph file to read. Defaults to {ma.GRAPH_DEFAULT_OUTPUT}."},
        }, "required": ["node"]},
        _tool_graph_show,
    ),
    MCPTool(
        "graph_sources",
        "What each graph layer would read from this project and what is missing, without building anything.",
        {"type": "object", "properties": {"root": {"type": "string", "default": "."}}},
        _tool_graph_sources,
    ),
    MCPTool(
        "requirement_show",
        "One requirement's full record by ID (active or archived).",
        {"type": "object", "properties": {"id": {"type": "string", "description": "Requirement ID, e.g. REQ-042."}}, "required": ["id"]},
        _tool_requirement_show,
    ),
    MCPTool(
        "requirement_list",
        "Requirements, optionally filtered by status.",
        {"type": "object", "properties": {
            "status": {"description": "One status, or a list of statuses.", "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "all": {"type": "boolean", "default": False, "description": "Include archived requirements."},
            "last": {"type": "integer", "description": "Only the last N entries."},
        }},
        _tool_requirement_list,
    ),
    MCPTool(
        "requirement_search",
        "Case-insensitive text search across requirement id, title, category, description, risk notes and acceptance criteria.",
        {"type": "object", "properties": {
            "text": {"type": "string"}, "active_only": {"type": "boolean", "default": False, "description": "Skip archived requirements."},
        }, "required": ["text"]},
        _tool_requirement_search,
    ),
    MCPTool(
        "failure_show",
        "One failure-ledger entry by ID: symptom, root cause, regression tests, prevention.",
        {"type": "object", "properties": {"id": {"type": "string", "description": "Failure ID, e.g. FAIL-003."}}, "required": ["id"]},
        _tool_failure_show,
    ),
    MCPTool(
        "gate_status",
        "Whether the pending change set (working tree plus commits since the merge-base) currently satisfies "
        "the completion gate -- the same check `omni gate` runs, read-only: nothing is written, nothing is blocked.",
        {"type": "object", "properties": {}},
        _tool_gate_status,
    ),
]
TOOLS_BY_NAME: dict[str, MCPTool] = {tool.name: tool for tool in TOOLS}


def dispatch(name: str, arguments: dict[str, Any] | None) -> Any:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise ToolError(f"Unknown tool: {name}. Known tools: {', '.join(sorted(TOOLS_BY_NAME))}.")
    try:
        return tool.handler(arguments or {})
    except ToolError:
        raise
    except KeyError as exc:
        raise ToolError(f"{name}: missing required argument {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{name}: {exc}") from exc


def _rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_call_result(payload: Any, is_error: bool = False) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    """One JSON-RPC request in, one response out (or None for a notification, which gets none)."""
    method = message.get("method")
    request_id = message.get("id")
    is_notification = "id" not in message

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
        return None if is_notification else _rpc_result(request_id, result)

    if method in ("notifications/initialized", "initialized"):
        return None  # acknowledgement only; the client does not expect a reply

    if method == "ping":
        return None if is_notification else _rpc_result(request_id, {})

    if method == "tools/list":
        return None if is_notification else _rpc_result(request_id, {"tools": [tool.spec() for tool in TOOLS]})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            payload = dispatch(str(name), arguments)
            # graph_why/lineage/trace/timeline/show return {"ok": false, ...} for "not found" or "ambiguous"
            # instead of raising, so a client that only checks isError still needs the signal.
            response = _tool_call_result(payload, is_error=isinstance(payload, dict) and payload.get("ok") is False)
        except ToolError as exc:
            response = _tool_call_result(str(exc), is_error=True)
        except Exception:  # noqa: BLE001 -- a tool crashing must reach the client as a tool error, not kill the server
            response = _tool_call_result(f"Internal error in tool {name!r}:\n{traceback.format_exc()}", is_error=True)
        return None if is_notification else _rpc_result(request_id, response)

    if is_notification:
        return None
    return _rpc_error(request_id, -32601, f"Method not found: {method}")


def serve_stdio(input_stream: TextIO | None = None, output_stream: TextIO | None = None) -> None:
    """The stdio transport: one JSON object per line in, one per line out. Runs until stdin closes."""
    input_stream = input_stream if input_stream is not None else sys.stdin
    output_stream = output_stream if output_stream is not None else sys.stdout
    for line in input_stream:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = _rpc_error(None, -32700, "Parse error: invalid JSON")
        else:
            try:
                response = handle_message(message)
            except Exception:  # noqa: BLE001 -- one malformed request must not take the whole server down
                response = _rpc_error(message.get("id"), -32603, f"Internal error:\n{traceback.format_exc()}")
        if response is not None:
            output_stream.write(json.dumps(response) + "\n")
            output_stream.flush()
