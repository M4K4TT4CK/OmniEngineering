"""Tests for REQ-043: Arbiter findings as graph nodes, traceable from the CLI, the MCP server and the viewer.

Stdlib only and no tree-sitter: the code layer is the hand-built fixture from test_graph_layers, the report is a
minimal Arbiter report.json written into the fixture repository. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import omni_graph as og  # noqa: E402
import omni_mcp as mcp  # noqa: E402
from test_graph_layers import Fixture, write  # noqa: E402

EVIDENCE = "PLANTED-EVIDENCE-7f3a9c-never-in-the-graph"

REPORT = {
    "schema_version": 1,
    "findings": [
        {"id": "f:aaa111", "rule_id": "arbiter/secrets.aws-access-key", "title": "Key in source", "dimension": "security",
         "severity": "high", "status": "new", "suppressed": False, "tags": ["secret", "req:REQ-001"],
         "location": {"path": "src/app.py", "start_line": 3, "logical": "", "repo_id": "root"}, "evidence": EVIDENCE},
        {"id": "f:bbb222", "rule_id": "arbiter/quality.long-function", "title": "Long function", "dimension": "quality",
         "severity": "low", "status": "existing", "suppressed": False, "tags": ["outside-this-change"],
         "location": {"path": "src/other.py", "start_line": 10}, "evidence": EVIDENCE},
        {"id": "f:ccc333", "rule_id": "arbiter/quality.doc-smell", "title": "Doc smell", "dimension": "quality",
         "severity": "low", "status": "new", "suppressed": False, "tags": [],
         "location": {"path": "docs/guide.md", "start_line": 1}, "evidence": EVIDENCE},
        {"id": "f:ddd444", "rule_id": "arbiter/secrets.private-key", "title": "Suppressed finding", "dimension": "security",
         "severity": "critical", "status": "new", "suppressed": True, "suppression_reason": "fixture",
         "tags": [], "location": {"path": "src/app.py", "start_line": 1}, "evidence": EVIDENCE},
    ],
}


class FindingsFixture(Fixture):
    """The layer fixture plus an Arbiter report and a ledger entry that records one finding."""

    report_rel = "arbiter-out/omni-gate/report.json"

    def setUp(self) -> None:
        super().setUp()
        write(self.root / self.report_rel, json.dumps(REPORT))
        ledger = json.loads((self.root / ".ai/failures/failure-ledger.json").read_text(encoding="utf-8"))
        ledger["failures"].append({
            "id": "FAIL-003", "date": "2026-10-01", "title": "Key leaked", "status": "open", "symptom": "s",
            "how_detected": "arbiter gate finding f:aaa111 during REQ-001", "affected": ["src/app.py"],
        })
        write(self.root / ".ai/failures/failure-ledger.json", json.dumps(ledger))

    def build(self) -> Path:
        og.add_governance_layer(self.graph, self.root)
        og.add_workspace_layer(self.graph, self.root)
        og.add_assurance_layer(self.graph, self.root)
        self.path = self.root / "graph.json"
        self.path.write_text(json.dumps(self.graph.to_json(str(self.root), ["python"], {})), encoding="utf-8")
        return self.path

    def finding_ids(self) -> set[str]:
        return {n.id for n in self.graph.nodes.values() if n.kind == "finding"}


class TestFindingNodes(FindingsFixture):
    def test_unsuppressed_findings_become_assurance_nodes_with_the_kept_fields(self) -> None:
        self.build()
        self.assertEqual(self.finding_ids(), {"finding:f:aaa111", "finding:f:bbb222", "finding:f:ccc333"})
        node = self.graph.nodes["finding:f:aaa111"]
        self.assertEqual((node.layer, node.name, node.file, node.start_line, node.summary), ("assurance", "f:aaa111", "src/app.py", 3, "Key in source"))
        self.assertEqual(node.attrs["rule_id"], "arbiter/secrets.aws-access-key")
        self.assertEqual((node.attrs["dimension"], node.attrs["severity"], node.attrs["status"], node.attrs["line"]), ("security", "high", "new", 3))
        self.assertEqual(node.attrs["directory_path"], "src")
        self.assertEqual(node.attrs["report"], self.report_rel)
        self.assertIn("outside-this-change", self.graph.nodes["finding:f:bbb222"].attrs["tags"])
        self.assertEqual(self.graph.layer_stats["assurance"]["findings"], 3)

    def test_flags_reach_the_file_and_the_symbol_whose_span_holds_the_line(self) -> None:
        self.build()
        flags = self.edges("flags")
        self.assertIn(("finding:f:aaa111", "src/app.py"), flags)
        self.assertIn(("finding:f:aaa111", "src/app.py::total"), flags)  # line 3 sits inside total (lines 2-5)
        self.assertIn(("finding:f:bbb222", "src/other.py"), flags)
        self.assertNotIn(("finding:f:bbb222", "src/other.py::other"), flags)  # line 10 is outside it
        self.assertIn(("finding:f:ccc333", "file:docs/guide.md"), flags)  # an unparsed file resolves like a failure's path does
        self.assertEqual(self.graph.layer_stats["assurance"]["flags"], 4)

    def test_cites_and_recorded_as_edges(self) -> None:
        self.build()
        self.assertEqual(self.edges("cites"), {("finding:f:aaa111", "req:REQ-001")})
        self.assertEqual(self.edges("recorded_as"), {("finding:f:aaa111", "failure:FAIL-003")})
        for edge_type in ("flags", "cites", "recorded_as"):
            self.assertEqual(og.EDGE_LAYER[edge_type], og.LAYER_ASSURANCE)
            self.assertIn(edge_type, og.LINEAGE_FLOW)

    def test_directories_contain_their_files_and_subdirectories(self) -> None:
        self.build()
        contains = self.edges("contains")
        self.assertIn(("file:src", "src/app.py"), contains)
        self.assertIn(("file:src", "src/other.py"), contains)
        self.assertIn(("file:docs", "file:docs/guide.md"), contains)
        self.assertTrue(self.graph.nodes["file:src"].attrs.get("directory"))

    def test_no_report_means_no_nodes_and_sources_says_so(self) -> None:
        (self.root / self.report_rel).unlink()
        self.build()
        self.assertEqual(self.finding_ids(), set())
        self.assertTrue(any(og.FINDINGS_NO_REPORT_NOTE in note for note in self.graph.notes))
        sources = og.describe_sources(self.root)
        self.assertEqual(sources["layers"]["assurance"]["findings"]["note"], "findings: no Arbiter report found (run ./omni gate)")
        self.assertIn("findings: no Arbiter report found (run ./omni gate)", sources["hints"])
        result = subprocess.run([sys.executable, str(ROOT / "make_ai.py"), "graph", "sources"], cwd=self.root, capture_output=True, text=True, encoding="utf-8")
        self.assertIn("findings: no Arbiter report found (run ./omni gate)", result.stdout)

    def test_sources_names_the_report_it_would_read(self) -> None:
        sources = og.describe_sources(self.root)["layers"]["assurance"]["findings"]
        self.assertEqual((sources["report"], sources["findings"]), (self.report_rel, 3))

    def test_the_newest_report_under_the_rules_out_directory_wins(self) -> None:
        (self.root / self.report_rel).unlink()
        write(self.root / ".ai/rules/completion-workflow.json", json.dumps({
            "rulepack_id": "completion", "version": "1", "title": "t", "purpose": "p", "applies_to": [],
            "rules": [{"id": "completion.arbiter_gate", "severity": "required", "statement": "s", "validation": {
                "type": "command", "run": "arbiter gate . --changed {base} --out custom-out/gate --format json"}}],
        }))
        older = dict(REPORT, findings=[REPORT["findings"][2]])
        write(self.root / "custom-out/gate/earlier/report.json", json.dumps(older))  # one level below the --out directory
        write(self.root / "custom-out/gate/report.json", json.dumps(REPORT))
        os.utime(self.root / "custom-out/gate/earlier/report.json", (1_600_000_000, 1_600_000_000))
        self.assertEqual(og.find_findings_report(self.root), self.root / "custom-out/gate/report.json")
        self.assertIsNone(og.find_findings_report(self.root, {**og.graph_config(self.root), "rules_dir": "nowhere"}))  # back to the default arbiter-out, which the test removed
        self.build()
        self.assertEqual(len(self.finding_ids()), 3)

    def test_graph_config_overrides_or_disables_the_report(self) -> None:
        write(self.root / "custom/report.json", json.dumps(dict(REPORT, findings=[REPORT["findings"][2]])))
        write(self.root / ".ai/graph-config.json", json.dumps({"findings_report": "custom/report.json"}))
        self.build()
        self.assertEqual(self.finding_ids(), {"finding:f:ccc333"})
        write(self.root / ".ai/graph-config.json", json.dumps({"findings_report": None}))
        problems: list[str] = []
        self.assertIsNone(og.graph_config(self.root, problems)["findings_report"])
        self.assertEqual(problems, [])
        self.assertIsNone(og.find_findings_report(self.root))
        self.assertTrue(og.describe_sources(self.root)["layers"]["assurance"]["findings"]["disabled"])

    def test_nothing_from_the_report_but_identity_location_and_rule_is_copied(self) -> None:
        path = self.build()
        self.assertIn(EVIDENCE, (self.root / self.report_rel).read_text(encoding="utf-8"))  # control: the report does carry it
        self.assertNotIn(EVIDENCE, path.read_text(encoding="utf-8"))
        for node in self.graph.nodes.values():
            self.assertNotIn(EVIDENCE, json.dumps(node.to_json()))


class TestFindingQueries(FindingsFixture):
    def setUp(self) -> None:
        super().setUp()
        self.build()

    def test_why_on_a_finding_id_lists_rule_code_requirement_and_failure(self) -> None:
        result = og.why(self.path, "f:aaa111")
        self.assertTrue(result["ok"])
        self.assertEqual(result["node"]["kind"], "finding")
        self.assertEqual(result["node"]["rule_id"], "arbiter/secrets.aws-access-key")
        sections = result["sections"]
        self.assertEqual(sections["finding"][0]["name"], "arbiter/secrets.aws-access-key")
        self.assertEqual({i["id"] for i in sections["flagged code"]}, {"src/app.py", "src/app.py::total"})
        self.assertEqual([i["name"] for i in sections["requirement"]], ["REQ-001"])
        self.assertEqual([i["name"] for i in sections["failure"]], ["FAIL-003"])

    def test_why_on_a_file_lists_its_findings(self) -> None:
        self.assertEqual([i["name"] for i in og.why(self.path, "src/app.py")["sections"]["findings"]], ["f:aaa111"])
        self.assertEqual([i["name"] for i in og.why(self.path, "src/app.py::total")["sections"]["findings"]], ["f:aaa111"])

    def test_findings_group_by_directory_with_counts(self) -> None:
        result = og.findings(self.path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["report"], self.report_rel)
        self.assertEqual(result["by_dimension"], {"quality": 2, "security": 1})
        self.assertEqual(result["by_severity"], {"high": 1, "low": 2})
        self.assertEqual([g["directory"] for g in result["directories"]], ["docs", "src"])
        src = result["directories"][1]
        self.assertEqual((src["count"], src["by_dimension"], src["by_severity"]), (2, {"quality": 1, "security": 1}, {"high": 1, "low": 1}))
        first = src["findings"][0]  # high before low
        self.assertEqual((first["name"], first["symbol"], first["line"]), ("f:aaa111", "total", 3))
        self.assertIn("REQ-001", first["requirements"])
        self.assertIn("FAIL-003", first["failures"])

    def test_under_dimension_and_severity_narrow_the_result(self) -> None:
        self.assertEqual([g["directory"] for g in og.findings(self.path, under="src")["directories"]], ["src"])
        self.assertEqual(og.findings(self.path, under="src/")["total"], 2)
        self.assertEqual(og.findings(self.path, under="src/app.py")["total"], 1)
        self.assertEqual(og.findings(self.path, dimension="security")["total"], 1)
        self.assertEqual(og.findings(self.path, severity="LOW")["total"], 2)
        self.assertEqual(og.findings(self.path, under="docs", severity="high")["total"], 0)

    def test_depth_bounds_what_a_finding_is_tied_to(self) -> None:
        shallow = og.findings(self.path, depth=1)["directories"][1]["findings"][0]
        self.assertEqual(shallow["failures"], ["FAIL-003"])  # one hop: the ledger entry it was recorded as
        self.assertEqual(shallow["requirements"], ["REQ-001"])  # one hop: the cited requirement
        deeper = og.findings(self.path, depth=2)["directories"][1]["findings"][0]
        self.assertIn("REQ-002", deeper["requirements"])  # two hops: through the file to the requirement whose scope covers it

    def test_the_tree_is_readable_text(self) -> None:
        text = og.render_findings_tree(og.findings(self.path, severity="high"))
        self.assertIn("findings: 1", text)
        self.assertIn("src/  1", text)
        self.assertIn("f:aaa111", text)
        self.assertIn("src/app.py:3 in total", text)
        self.assertIn("requirements: REQ-001", text)
        self.assertNotIn("f:bbb222", text)
        self.assertIn("(none", og.render_findings_tree(og.findings(self.path, dimension="drift")))

    def test_lineage_stops_at_the_file_at_depth_one_and_reaches_the_requirement_deeper(self) -> None:
        one = {n["id"] for n in og.lineage(self.path, "f:bbb222", depth=1)["upstream"]}
        self.assertEqual(one, {"src/other.py"})
        three = {n["id"] for n in og.lineage(self.path, "f:bbb222", depth=3)["upstream"]}
        self.assertIn("req:REQ-002", three)
        self.assertIn("file:src", three)
        down = {n["id"] for n in og.lineage(self.path, "f:aaa111", depth=1)["downstream"]}
        self.assertEqual(down, {"failure:FAIL-003"})

    def test_the_mcp_tool_returns_the_cli_json(self) -> None:
        via_tool = mcp.dispatch("graph_findings", {"graph": str(self.path), "under": "src", "severity": "high"})
        cli = subprocess.run(
            [sys.executable, str(ROOT / "make_ai.py"), "graph", "findings", "--graph", str(self.path), "--under", "src", "--severity", "high", "--json"],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout), via_tool)
        self.assertEqual(via_tool["total"], 1)
        self.assertIn("graph_findings", mcp.TOOLS_BY_NAME)

    def test_the_cli_tree_and_view_flag(self) -> None:
        output = self.root / "out/findings.html"
        cli = subprocess.run(
            [sys.executable, str(ROOT / "make_ai.py"), "graph", "findings", "--graph", str(self.path), "--tree", "--view", "--output", str(output)],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertIn("findings: 3", cli.stdout)
        self.assertIn("Findings tab", cli.stdout)
        self.assertIn('"view":"findings"', output.read_text(encoding="utf-8"))

    def test_the_svg_colours_findings_by_category_and_has_a_legend(self) -> None:
        svg = og.render(self.path, focus="f:aaa111", depth=1)["svg"]
        self.assertIn("FINDINGS BY CATEGORY", svg)
        self.assertIn(og._FINDING_DIMENSION_COLORS["security"], svg)
        self.assertEqual(og._node_color({"kind": "finding", "attrs": {"dimension": "quality"}}), og._FINDING_DIMENSION_COLORS["quality"])


if __name__ == "__main__":
    unittest.main()
