"""Tests for `omni graph impact`: what a pending change set reaches across the governance, history, assurance and
workspace layers, as the CLI, the MCP tool and the one-line summary `omni gate` prints beside the changed set.

Stdlib only and no tree-sitter: the code layer is hand-built the way tests/test_lineage.py builds it. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import make_ai as ma  # noqa: E402
import omni_graph as og  # noqa: E402
import omni_mcp as mcp  # noqa: E402
from test_graph_layers import function, module, write  # noqa: E402

REGISTRY = {
    "version": "1.0.0", "requirement_id_prefix": "REQ",
    "requirements": [
        {"id": "REQ-001", "category": "Feature", "title": "Add a", "description": "d", "priority": "high", "status": "completed",
         "minimum_access_scope": ["src/a.py"], "acceptance_criteria": [], "validation_required": [], "documentation_required": [], "risk_notes": []},
        {"id": "REQ-002", "category": "Feature", "title": "Add b", "description": "d", "priority": "high", "status": "pending",
         "minimum_access_scope": ["src/b.py"], "acceptance_criteria": [], "validation_required": [], "documentation_required": [], "risk_notes": []},
    ],
}
LEDGER = {
    "version": "1.0.0", "failure_id_prefix": "FAIL",
    "failures": [
        {"id": "FAIL-001", "date": "2026-09-02", "title": "a drops cents", "status": "fixed", "severity": "high", "symptom": "s",
         "root_cause": "r", "requirement": "REQ-001", "affected": ["src/a.py"], "fix_summary": "f",
         "regression_tests": ["tests/test_a.py::test_a"], "prevention_rules": ["money.no_floats"], "prevention_notes": ""},
    ],
}
SUITES = {
    "version": "1.0.0",
    "suites": [{"id": "unit", "name": "Unit", "kind": "unit", "framework": "unittest", "paths": ["tests"],
                "command": "python3 -m unittest", "covers": ["src"]}],
}


class ImpactFixture(unittest.TestCase):
    """A small repository on disk with one requirement, one failure, one suite and one rule tied to src/a.py."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        write(self.root / ".ai/requirements/requirements.json", json.dumps(REGISTRY))
        write(self.root / ".ai/failures/failure-ledger.json", json.dumps(LEDGER))
        write(self.root / ".ai/test-suites.json", json.dumps(SUITES))
        write(self.root / ".ai/rules/money.json", json.dumps(
            {"rulepack_id": "money", "version": "1", "title": "t", "purpose": "p", "applies_to": [],
             "rules": [{"id": "money.no_floats", "severity": "required", "statement": "Never use floats for money."}]}))
        write(self.root / "CHANGELOG.md", "# Changelog\n\n## 2026-09-01\n\nAdded a for REQ-001; touches `src/a.py`.\n")
        write(self.root / "src/a.py", "def fn(): return 1\n")
        write(self.root / "src/b.py", "def other(): return 2\n")
        write(self.root / "tests/test_a.py", "def test_a(): pass\n")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add a (REQ-001)")

        self.graph = og.Graph()
        module(self.graph, "src/a.py")
        module(self.graph, "src/b.py")
        module(self.graph, "tests/test_a.py")
        function(self.graph, "src/a.py", "fn")
        function(self.graph, "tests/test_a.py", "test_a")
        self.graph.add_edge("tests/test_a.py::test_a", "src/a.py::fn", "calls", "EXTRACTED", "syntax")
        og.add_governance_layer(self.graph, self.root)
        og.add_workspace_layer(self.graph, self.root)
        og.add_history_layer(self.graph, self.root)
        og.add_assurance_layer(self.graph, self.root)
        self.path = self.root / "graph.json"
        self.path.write_text(json.dumps(self.graph.to_json(str(self.root), ["python"], {})), encoding="utf-8")

    def git(self, *args: str) -> None:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True, env=env)

    def chdir_root(self) -> None:
        cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)

    @staticmethod
    def by_id(result: dict, bucket: str) -> dict[str, dict]:
        return {item["id"]: item for item in result[bucket]}


class TestImpact(ImpactFixture):
    def test_a_changed_file_reaches_its_requirement_failure_suite_tests_and_rule_with_hop_counts(self) -> None:
        result = og.impact(self.path, ["src/a.py"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["changed"], ["src/a.py"])
        self.assertEqual(result["unresolved"], [])
        requirement = self.by_id(result, "requirements")["req:REQ-001"]
        self.assertEqual((requirement["hops"], requirement["via"]), (1, "touches"))
        failure = self.by_id(result, "failures")["failure:FAIL-001"]
        self.assertEqual((failure["hops"], failure["via"]), (1, "affects"))
        suite = self.by_id(result, "suites")["suite:unit"]
        self.assertEqual((suite["hops"], suite["via"]), (1, "covers"))
        rule = self.by_id(result, "rules")["rule:money.no_floats"]
        self.assertEqual((rule["hops"], rule["via"]), (2, "prevented_by"))
        tests = self.by_id(result, "tests")
        self.assertEqual(set(tests), {"tests/test_a.py"}, "the regression test reached through `guards` is listed as its file, once")
        self.assertTrue(result["changelog"], "the changelog entry that names src/a.py is one hop away")
        self.assertTrue(result["commits"], "the commit that modified src/a.py is one hop away")
        # a requirement that scopes another file is not dragged in through the suite's shared coverage at this depth
        self.assertNotIn("req:REQ-002", self.by_id(result, "requirements"))

    def test_every_entry_names_the_edge_and_the_node_it_was_reached_from(self) -> None:
        result = og.impact(self.path, ["src/a.py"])
        for bucket in og.IMPACT_BUCKETS:
            for item in result[bucket]:
                self.assertIn(item["via"], og.IMPACT_EDGE_TYPES, item)
                self.assertGreaterEqual(item["hops"], 1)
                self.assertTrue(item["from"])

    def test_an_unknown_path_lands_in_unresolved_and_reaches_nothing(self) -> None:
        result = og.impact(self.path, ["src/nope.py"])
        self.assertEqual(result["unresolved"], ["src/nope.py"])
        self.assertEqual(result["changed"], [])
        self.assertFalse(any(result[bucket] for bucket in og.IMPACT_BUCKETS))

    def test_a_path_inside_a_directory_scope_is_resolved_through_the_directory_node(self) -> None:
        # REQ-003 scopes the whole `docs` directory, which has no parsed module: a new file under it must still resolve.
        registry = {**REGISTRY, "requirements": REGISTRY["requirements"] + [
            {"id": "REQ-003", "category": "Docs", "title": "Docs", "description": "d", "priority": "low", "status": "pending",
             "minimum_access_scope": ["docs"], "acceptance_criteria": [], "validation_required": [], "documentation_required": [], "risk_notes": []}]}
        write(self.root / ".ai/requirements/requirements.json", json.dumps(registry))
        write(self.root / "docs/guide.md", "guide\n")
        graph = og.Graph()
        module(graph, "src/a.py")
        og.add_governance_layer(graph, self.root)
        self.path.write_text(json.dumps(graph.to_json(str(self.root), ["python"], {})), encoding="utf-8")
        result = og.impact(self.path, ["docs/new-page.md"])
        self.assertEqual(result["changed"], ["docs/new-page.md"])
        self.assertIn("req:REQ-003", self.by_id(result, "requirements"))

    def test_depth_zero_reaches_nothing_beyond_the_seeds(self) -> None:
        result = og.impact(self.path, ["src/a.py"], depth=0)
        self.assertEqual(result["changed"], ["src/a.py"])
        self.assertGreater(result["seeds"], 0)
        self.assertFalse(any(result[bucket] for bucket in og.IMPACT_BUCKETS))

    def test_depth_one_stops_before_the_rule_behind_the_failure(self) -> None:
        result = og.impact(self.path, ["src/a.py"], depth=1)
        self.assertIn("failure:FAIL-001", self.by_id(result, "failures"))
        self.assertEqual(result["rules"], [])

    def test_code_edges_are_never_followed(self) -> None:
        # Through code, src/b.py is one call away from src/a.py and REQ-002 two hops; the walk must not take that road.
        # (Across the layers it is three hops: suite -> covers src/b.py -> touched by REQ-002, which depth 2 stops short of.)
        self.graph.add_edge("src/a.py::fn", "src/b.py", "calls", "EXTRACTED", "syntax")
        self.path.write_text(json.dumps(self.graph.to_json(str(self.root), ["python"], {})), encoding="utf-8")
        result = og.impact(self.path, ["src/a.py"], depth=2)
        self.assertNotIn("req:REQ-002", self.by_id(result, "requirements"))
        self.assertIn("req:REQ-002", self.by_id(og.impact(self.path, ["src/a.py"], depth=3), "requirements"))
        self.assertNotIn("follows", og.IMPACT_EDGE_TYPES, "walking the commit chain would replay the whole history")

    def test_paths_are_normalised_and_deduplicated(self) -> None:
        result = og.impact(self.path, ["./src/a.py", "src\\a.py", "src/a.py/"])
        self.assertEqual(result["changed"], ["src/a.py"])

    def test_summary_is_one_line_that_counts_each_bucket_and_the_unresolved_paths(self) -> None:
        line = og.impact_summary(og.impact(self.path, ["src/a.py", "src/nope.py"]))
        self.assertTrue(line.startswith("impact: "))
        self.assertNotIn("\n", line)
        for fragment in ("1 requirement(s)", "1 failure(s)", "1 suite(s)", "1 test file(s)", "1 rule(s)", "; 1 path unresolved"):
            self.assertIn(fragment, line)
        self.assertEqual(og.impact_summary({"unresolved": []}), "impact: nothing in the graph is tied to the changed path(s)")


class TestImpactCommandAndTool(ImpactFixture):
    def modify_a(self) -> None:
        write(self.root / "src/a.py", "def fn(): return 2\n")

    def test_the_cli_groups_by_bucket_and_ends_with_the_summary(self) -> None:
        self.chdir_root()
        self.modify_a()
        out = io.StringIO()
        with redirect_stdout(out):
            rc = ma.run_graph_impact(argparse.Namespace(changed=None, graph=str(self.path), depth=2, limit=40, json=False))
        self.assertEqual(rc, 0)
        text = out.getvalue()
        for heading in ("requirements (", "failures (", "suites (", "rules ("):
            self.assertIn(heading, text)
        self.assertIn("REQ-001", text)
        self.assertTrue(text.strip().splitlines()[-1].startswith("impact: "))

    def test_the_cli_json_is_the_impact_dict_plus_the_base(self) -> None:
        self.chdir_root()
        self.modify_a()
        out = io.StringIO()
        with redirect_stdout(out):
            rc = ma.run_graph_impact(argparse.Namespace(changed=None, graph=str(self.path), depth=2, limit=40, json=True))
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertIn("src/a.py", payload["changed"])
        self.assertIn("req:REQ-001", {i["id"] for i in payload["requirements"]})
        self.assertIn("base", payload)

    def test_the_cli_refuses_without_a_graph_file(self) -> None:
        self.chdir_root()
        err = io.StringIO()
        with redirect_stderr(err):
            rc = ma.run_graph_impact(argparse.Namespace(changed=None, graph="missing.json", depth=2, limit=40, json=False))
        self.assertEqual(rc, 1)
        self.assertIn("Graph file not found", err.getvalue())

    def test_the_mcp_tool_returns_the_same_buckets_as_the_function(self) -> None:
        self.chdir_root()
        self.modify_a()
        tool = mcp.dispatch("graph_impact", {"graph": str(self.path)})
        direct = og.impact(self.path, ["src/a.py"])
        self.assertTrue(tool["ok"])
        self.assertIn("src/a.py", tool["changed"])
        for bucket in og.IMPACT_BUCKETS:
            self.assertEqual({i["id"] for i in tool[bucket]}, {i["id"] for i in direct[bucket]}, bucket)
        self.assertIn("graph_impact", mcp.TOOLS_BY_NAME)

    def test_the_mcp_tool_honours_depth_and_requires_the_graph_file(self) -> None:
        self.chdir_root()
        self.modify_a()
        shallow = mcp.dispatch("graph_impact", {"graph": str(self.path), "depth": 0})
        self.assertFalse(any(shallow[bucket] for bucket in og.IMPACT_BUCKETS))
        with self.assertRaises(mcp.ToolError):
            mcp.dispatch("graph_impact", {"graph": "missing.json"})


class TestGatePrintsTheImpactLine(ImpactFixture):
    """`omni gate` prints the one-line summary beside the changed set when a graph exists; it never changes the verdict."""

    def gate_output(self) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = ma.run_gate(argparse.Namespace(hook=False))
        return rc, out.getvalue() + err.getvalue()

    def test_the_impact_line_appears_when_the_default_graph_file_exists(self) -> None:
        self.chdir_root()
        write(self.root / "src/a.py", "def fn(): return 2\n")
        (self.root / ma.GRAPH_DEFAULT_OUTPUT).parent.mkdir(parents=True, exist_ok=True)
        (self.root / ma.GRAPH_DEFAULT_OUTPUT).write_bytes(self.path.read_bytes())
        rc_with, with_graph = self.gate_output()
        self.assertIn("impact: ", with_graph)
        self.assertIn("1 requirement(s)", with_graph)
        (self.root / ma.GRAPH_DEFAULT_OUTPUT).unlink()
        rc_without, without_graph = self.gate_output()
        self.assertNotIn("impact: ", without_graph)
        self.assertEqual(rc_with, rc_without, "the impact line is advice; it must not change the gate's verdict")

    def test_a_broken_graph_file_prints_nothing_and_does_not_break_the_gate(self) -> None:
        self.chdir_root()
        write(self.root / "src/a.py", "def fn(): return 2\n")
        write(self.root / ma.GRAPH_DEFAULT_OUTPUT, "{not json")
        rc, text = self.gate_output()
        self.assertNotIn("impact: ", text)
        self.assertNotIn("Traceback", text)
        self.assertIn("omni gate", text)


if __name__ == "__main__":
    unittest.main()
