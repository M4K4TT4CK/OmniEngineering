"""Tests for the MCP (Model Context Protocol) stdio server: the graph and registries as tools, reachable
by any MCP-speaking assistant, not only one with a shell.

Stdlib only, hand-rolled JSON-RPC 2.0 over stdio (see omni_mcp.py's module docstring for why). Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import omni_graph as og  # noqa: E402
import omni_mcp as mcp  # noqa: E402
from test_graph_layers import write  # noqa: E402


class TestToolRegistry(unittest.TestCase):
    def test_every_tool_has_a_name_description_and_object_schema(self) -> None:
        names = set()
        for tool in mcp.TOOLS:
            self.assertTrue(tool.name)
            self.assertTrue(tool.description)
            self.assertEqual(tool.input_schema.get("type"), "object")
            self.assertNotIn(tool.name, names, "duplicate tool name")
            names.add(tool.name)

    def test_tools_by_name_matches_the_list(self) -> None:
        self.assertEqual(set(mcp.TOOLS_BY_NAME), {tool.name for tool in mcp.TOOLS})

    def test_spec_is_json_serialisable(self) -> None:
        json.dumps([tool.spec() for tool in mcp.TOOLS])

    def test_read_only_tools_do_not_include_a_write_verb(self) -> None:
        # this server intentionally exposes no mutating tool yet; catch one creeping in by name
        for tool in mcp.TOOLS:
            self.assertNotRegex(tool.name, r"(add|update|delete|complete|draft|waive|install)")


class TestDispatch(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self._cwd)

        write(self.root / ".ai/requirements/requirements.json", json.dumps({
            "version": "1.0.0", "requirement_id_prefix": "REQ",
            "requirements": [{
                "id": "REQ-001", "category": "Feature", "title": "Add checkout", "description": "d",
                "priority": "high", "status": "completed", "minimum_access_scope": ["src/app.py"],
                "acceptance_criteria": [], "validation_required": [], "documentation_required": [], "risk_notes": [],
            }],
        }))
        write(self.root / ".ai/failures/failure-ledger.json", json.dumps({
            "version": "1.0.0", "failure_id_prefix": "FAIL",
            "failures": [{
                "id": "FAIL-001", "date": "2026-09-02", "title": "Rounding drops cents", "status": "fixed",
                "severity": "high", "symptom": "s", "root_cause": "r", "requirement": "REQ-001",
                "affected": ["src/app.py"], "fix_summary": "f", "regression_tests": [], "prevention_rules": [], "prevention_notes": "",
            }],
        }))

        graph = og.Graph()
        graph.add_node(og.GraphNode("src/app.py", "module", "app", "app", "src/app.py", 1, 5, "python"))
        og.add_governance_layer(graph, self.root)
        self.graph_path = self.root / "graph.json"
        self.graph_path.write_text(json.dumps(graph.to_json(str(self.root), ["python"], {})), encoding="utf-8")

    def test_unknown_tool_raises_tool_error(self) -> None:
        with self.assertRaises(mcp.ToolError):
            mcp.dispatch("no_such_tool", {})

    def test_graph_why_requires_the_graph_file(self) -> None:
        with self.assertRaises(mcp.ToolError):
            mcp.dispatch("graph_why", {"node": "REQ-001", "graph": "missing.json"})

    def test_graph_lineage_uses_the_named_graph_file(self) -> None:
        result = mcp.dispatch("graph_lineage", {"node": "REQ-001", "graph": str(self.graph_path)})
        self.assertTrue(result["ok"])
        self.assertTrue(any(n["id"] == "src/app.py" for n in result["downstream"]))

    def test_graph_lineage_rejects_a_bad_direction(self) -> None:
        with self.assertRaises(mcp.ToolError):
            mcp.dispatch("graph_lineage", {"node": "REQ-001", "graph": str(self.graph_path), "direction": "sideways"})

    def test_graph_trace_and_show_and_timeline_all_reach_the_graph(self) -> None:
        self.assertTrue(mcp.dispatch("graph_show", {"node": "REQ-001", "graph": str(self.graph_path)})["ok"])
        self.assertTrue(mcp.dispatch("graph_timeline", {"node": "REQ-001", "graph": str(self.graph_path)})["ok"])
        result = mcp.dispatch("graph_trace", {"source": "REQ-001", "target": "src/app.py", "graph": str(self.graph_path)})
        self.assertTrue(result["ok"])

    def test_graph_sources_needs_no_built_graph(self) -> None:
        result = mcp.dispatch("graph_sources", {"root": str(self.root)})
        self.assertIn("layers", result)

    def test_requirement_show_found_and_missing(self) -> None:
        found = mcp.dispatch("requirement_show", {"id": "REQ-001"})
        self.assertEqual(found["title"], "Add checkout")
        self.assertFalse(found["archived"])
        with self.assertRaises(mcp.ToolError):
            mcp.dispatch("requirement_show", {"id": "REQ-999"})

    def test_requirement_list_filters_by_status(self) -> None:
        result = mcp.dispatch("requirement_list", {"status": "completed"})
        self.assertEqual(result["shown"], 1)
        self.assertEqual(mcp.dispatch("requirement_list", {"status": "pending"})["shown"], 0)

    def test_requirement_list_accepts_a_list_of_statuses(self) -> None:
        result = mcp.dispatch("requirement_list", {"status": ["completed", "pending"]})
        self.assertEqual(result["shown"], 1)

    def test_requirement_search_reports_which_field_matched(self) -> None:
        result = mcp.dispatch("requirement_search", {"text": "checkout"})
        self.assertEqual(result["count"], 1)
        self.assertIn("title", result["matches"][0]["matched_fields"])
        self.assertEqual(mcp.dispatch("requirement_search", {"text": "nonexistent-xyz"})["count"], 0)

    def test_failure_show_found_and_missing(self) -> None:
        self.assertEqual(mcp.dispatch("failure_show", {"id": "FAIL-001"})["title"], "Rounding drops cents")
        with self.assertRaises(mcp.ToolError):
            mcp.dispatch("failure_show", {"id": "FAIL-999"})

    def test_gate_status_is_read_only_and_never_raises(self) -> None:
        result = mcp.dispatch("gate_status", {})
        self.assertIn("pass", result)
        self.assertIn("changed_paths", result)
        # nothing it read got written back
        self.assertFalse((self.root / ".ai" / "gate-waivers.jsonl").exists())


class TestJsonRpcFraming(unittest.TestCase):
    def call(self, message: dict) -> dict | None:
        return mcp.handle_message(message)

    def test_initialize_replies_with_protocol_and_server_info(self) -> None:
        response = self.call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(response["id"], 1)
        self.assertEqual(response["result"]["protocolVersion"], mcp.PROTOCOL_VERSION)
        self.assertIn("tools", response["result"]["capabilities"])

    def test_initialized_notification_gets_no_reply(self) -> None:
        self.assertIsNone(self.call({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_a_notification_never_gets_a_reply_even_for_a_real_call(self) -> None:
        self.assertIsNone(self.call({"jsonrpc": "2.0", "method": "tools/list"}))

    def test_tools_list_returns_every_registered_tool(self) -> None:
        response = self.call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(response["result"]["tools"]), len(mcp.TOOLS))

    def test_unknown_method_is_a_json_rpc_error_not_a_crash(self) -> None:
        response = self.call({"jsonrpc": "2.0", "id": 3, "method": "totally/bogus"})
        self.assertEqual(response["error"]["code"], -32601)

    def test_tools_call_wraps_a_tool_error_as_content_with_is_error(self) -> None:
        response = self.call({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "requirement_show", "arguments": {"id": "REQ-999"}}})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("Requirement not found", response["result"]["content"][0]["text"])

    def test_tools_call_marks_an_ok_false_payload_as_an_error_too(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            graph_path = Path(d) / "graph.json"
            graph_path.write_text(json.dumps(og.Graph().to_json(d, [], {})), encoding="utf-8")
            response = self.call({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "graph_why", "arguments": {"node": "nope", "graph": str(graph_path)}}})
        self.assertTrue(response["result"]["isError"])
        self.assertIn('"ok": false', response["result"]["content"][0]["text"])

    def test_a_tool_that_raises_unexpectedly_is_reported_not_fatal(self) -> None:
        original = mcp.TOOLS_BY_NAME["gate_status"].handler
        mcp.TOOLS_BY_NAME["gate_status"].handler = lambda arguments: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            response = self.call({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "gate_status", "arguments": {}}})
        finally:
            mcp.TOOLS_BY_NAME["gate_status"].handler = original
        self.assertTrue(response["result"]["isError"])
        self.assertIn("Internal error", response["result"]["content"][0]["text"])

    def test_calling_an_unknown_tool_does_not_raise_out_of_handle_message(self) -> None:
        response = self.call({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "nope", "arguments": {}}})
        self.assertTrue(response["result"]["isError"])


class TestServeStdio(unittest.TestCase):
    def test_end_to_end_over_a_real_pipe(self) -> None:
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        input_stream = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")
        output_stream = io.StringIO()
        mcp.serve_stdio(input_stream, output_stream)
        lines = [line for line in output_stream.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)  # the notification gets no reply
        self.assertEqual(json.loads(lines[0])["id"], 1)
        self.assertEqual(json.loads(lines[1])["id"], 2)

    def test_invalid_json_gets_a_parse_error_not_a_crash(self) -> None:
        output_stream = io.StringIO()
        mcp.serve_stdio(io.StringIO("not json at all\n"), output_stream)
        response = json.loads(output_stream.getvalue().strip())
        self.assertEqual(response["error"]["code"], -32700)

    def test_blank_lines_are_skipped(self) -> None:
        output_stream = io.StringIO()
        mcp.serve_stdio(io.StringIO("\n\n" + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n\n"), output_stream)
        lines = [line for line in output_stream.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)


class TestCLI(unittest.TestCase):
    def test_mcp_tools_lists_every_tool(self) -> None:
        result = subprocess.run([sys.executable, str(ROOT / "omni"), "mcp", "tools"], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0)
        for tool in mcp.TOOLS:
            self.assertIn(tool.name, result.stdout)

    def test_mcp_tools_json_matches_the_registry(self) -> None:
        result = subprocess.run([sys.executable, str(ROOT / "omni"), "mcp", "tools", "--json"], cwd=ROOT, capture_output=True, text=True, timeout=30)
        specs = json.loads(result.stdout)
        self.assertEqual({s["name"] for s in specs}, {tool.name for tool in mcp.TOOLS})

    def test_mcp_serve_speaks_json_rpc_over_the_real_cli(self) -> None:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
        result = subprocess.run([sys.executable, str(ROOT / "omni"), "mcp", "serve"], cwd=ROOT, input=request, capture_output=True, text=True, timeout=30)
        response = json.loads(result.stdout.strip().splitlines()[0])
        self.assertEqual(len(response["result"]["tools"]), len(mcp.TOOLS))


if __name__ == "__main__":
    unittest.main()
