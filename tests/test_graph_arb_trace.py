"""REQ-054: a finding carries what its rule found and the fix, and traces to the ARB-### requirement behind its rule.

A tiny git repository with a vendored Arbiter workspace (its own registry, CHANGELOG.md and one rule pack) and a FULL
Arbiter report whose findings carry no `req:` tags: the only route from a finding to an ARB id is its rule, named in a
change-log bullet, a registry entry or a rule pack. Stdlib unittest only (CI has no pytest). Run with:

    python3 -m unittest tests.test_graph_arb_trace -v
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import make_ai as ma  # noqa: E402
import omni_graph as og  # noqa: E402
import omni_mcp  # noqa: E402
from test_omni_graph_findings import _finding, _git, _write_report, build, make_repo  # noqa: E402

EVIDENCE = "PLANTED-EVIDENCE-LINE-must-not-reach-the-graph"
DESCRIPTION = "Two workers can both see the item and both take it: the check and the change are not atomic."
REMEDIATION = "Hold the collection's lock across the check and the change, or use a queue."

VENDORED_CHANGELOG = """# Changelog

## [Unreleased]
### 2026-10-21
- ARB-060 | Shipped rule packs
  (`packs/rules/ast/{python,go}.yaml`), one probe each.
- ARB-053, ARB-054 | New `concurrency` probe.
  `concurrency.check-then-act-unlocked` reports an `if` that reads a shared
  collection; `concurrency.never-raised-anywhere` is a rule with no finding.

### 2026-10-20
- ARB-064 | Unrelated work naming `src/app.py` and no rule.
"""

PACK = """# Python rules. Owner: the Python agent (ARB-061).
pack: python
languages: [python]
rules:
  - id: http-no-timeout
    title: "outbound call with no timeout"
  - id: never-fires
    title: "a rule with no finding"
"""

ROOT_CHANGELOG = """# Changelog
## 2026-10-21
### Completed
- `REQ-001` | Feature | Store queries: `arbiter/sql.string-concat` findings fixed
  in `src/app.py`.
"""


def make_vendored_repo(tmp: Path) -> Path:
    root = make_repo(tmp)
    ws = root / "arbiter"
    (ws / ".ai" / "requirements").mkdir(parents=True)
    (ws / ".ai" / "omni-version.json").write_text("{}", encoding="utf-8")
    (ws / ".ai" / "requirements" / "requirements.json").write_text(json.dumps({
        "id_aliases": {"REQ-053": "ARB-053"},
        "requirements": [
            {"id": "ARB-053", "title": "Flag check-then-act on shared collections without a lock", "status": "completed"},
            {"id": "ARB-054", "title": "Flag unsynchronised counters", "status": "completed"},
            {"id": "ARB-060", "title": "Shared foundation for per-language rule packs", "status": "completed"},
            {"id": "ARB-061", "title": "Interview slice rules in five languages", "status": "completed"},
            {"id": "ARB-062", "title": "CWE Top 25 coverage", "status": "pending",
             "description": "supply.unpinned-dependency stays the supply-chain check; arbiter/nope.not-a-rule is not one."},
            {"id": "ARB-064", "title": "Every finding traceable in OmniGraph", "status": "pending"},
        ],
    }), encoding="utf-8")
    (ws / "CHANGELOG.md").write_text(VENDORED_CHANGELOG, encoding="utf-8")
    pack = ws / "src" / "arbiter" / "packs" / "rules" / "ast" / "python.yaml"
    pack.parent.mkdir(parents=True)
    pack.write_text(PACK, encoding="utf-8")
    (root / "CHANGELOG.md").write_text(ROOT_CHANGELOG, encoding="utf-8")
    cfg = root / ".ai" / "graph-config.json"
    cfg.write_text(json.dumps({**json.loads(cfg.read_text()), "exclude_code_globs": ["arbiter/"]}), encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "Vendor arbiter (REQ-001)")
    findings = [
        _finding("f:race00000001", "arbiter/concurrency.check-then-act-unlocked", 9, ["CWE-362"],
                 description=DESCRIPTION, remediation=REMEDIATION, remediation_source="rule", evidence=EVIDENCE),
        _finding("f:sql000000002", "arbiter/sql.string-concat", 15, ["CWE-89"], dimension="security", severity="high"),
        _finding("f:http00000003", "arbiter/python.http-no-timeout", 15, ["CWE-400"], dimension="reliability"),
        _finding("f:supp00000004", "arbiter/supply.unpinned-dependency", 0, ["CWE-1357"], path="requirements.txt"),
        _finding("f:long00000005", "arbiter/ast.function-too-long", 1, ["CWE-1080"], severity="low"),
    ]
    assert not any(t.startswith("req:") for f in findings for t in f["tags"]), "a full scan: no req: tags"
    _write_report(root, "arbiter-out/report.json", findings, "full")
    return root


class ArbTraceFixture(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = make_vendored_repo(Path(tmp.name))
        og._CONFIG_CACHE.clear()
        self.addCleanup(og._CONFIG_CACHE.clear)
        self.graph, self.path = build(self.repo)

    def targets(self, source: str, edge_type: str) -> set[str]:
        return {e.target for e in self.graph.edges if e.source == source and e.type == edge_type}

    def traced(self, rule: str) -> set[str]:
        return {self.graph.nodes[t].name for t in self.targets(f"arbiter_rule:{rule}", "specified_by")}


class TestFindingAndFixText(ArbTraceFixture):
    def test_description_and_remediation_are_kept_and_the_evidence_is_not(self) -> None:
        attrs = self.graph.nodes["finding:f:race00000001"].attrs
        self.assertEqual(attrs["description"], DESCRIPTION)
        self.assertEqual(attrs["remediation"], REMEDIATION)
        self.assertEqual(attrs["remediation_source"], "rule")
        self.assertNotIn("evidence", attrs)
        self.assertNotIn(EVIDENCE, self.path.read_text(encoding="utf-8"))
        page = og.build_view_html(self.path)
        self.assertTrue(page["ok"], page)
        self.assertIn(DESCRIPTION, page["html"])
        self.assertIn(REMEDIATION, page["html"])
        self.assertNotIn(EVIDENCE, page["html"])

    def test_long_text_is_trimmed(self) -> None:
        report = self.repo / "arbiter-out" / "report.json"
        data = json.loads(report.read_text())
        data["findings"][0]["description"] = "word " * 400
        report.write_text(json.dumps(data))
        og._REPORT_CACHE.clear()
        item = next(i for i in og.load_findings(self.repo, report) if i["id"] == "f:race00000001")
        self.assertLessEqual(len(item["description"]), og._FINDING_TEXT_LIMIT)

    def test_why_findings_and_mcp_carry_the_finding_text_and_fix(self) -> None:
        why = og.why(self.path, "f:race00000001")
        head = why["sections"]["finding"][0]
        self.assertEqual((head["description"], head["remediation"]), (DESCRIPTION, REMEDIATION))
        listed = og.findings(self.path)
        item = next(f for g in listed["directories"] for f in g["findings"] if f["name"] == "f:race00000001")
        self.assertEqual((item["description"], item["remediation"]), (DESCRIPTION, REMEDIATION))
        tree = og.render_findings_tree(listed)
        self.assertIn("finding  Two workers can both see the item", tree)
        self.assertIn("fix      Hold the collection's lock", tree)
        via_mcp = omni_mcp._tool_graph_findings({"graph": str(self.path)})
        item = next(f for g in via_mcp["directories"] for f in g["findings"] if f["name"] == "f:race00000001")
        self.assertEqual(item["remediation"], REMEDIATION)

    def test_cli_why_prints_the_finding_the_fix_and_the_traced_requirements(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = ma.run_graph_why(SimpleNamespace(graph=str(self.path), node="f:race00000001", json=False, limit=20))
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("finding  " + DESCRIPTION[:40], text)
        self.assertIn("fix      " + REMEDIATION[:40], text)
        self.assertRegex(text, r"traced to \(2\)\n  arbiter/ARB-053")


class TestTraceByArbId(ArbTraceFixture):
    def test_a_bare_rule_id_in_a_vendored_changelog_bullet_traces_to_the_bullets_ids(self) -> None:
        rule = "arbiter_rule:arbiter/concurrency.check-then-act-unlocked"
        self.assertEqual(self.traced("arbiter/concurrency.check-then-act-unlocked"), {"arbiter/ARB-053", "arbiter/ARB-054"})
        edge = next(e for e in self.graph.edges if e.source == rule and e.type == "specified_by")
        self.assertEqual((edge.provenance, edge.resolver), ("INFERRED", "changelog"))
        self.assertIn("arbiter/CHANGELOG.md:", edge.detail)
        entries = {e.source for e in self.graph.edges if e.target == rule and e.type == "mentions_rule"}
        self.assertEqual(entries, {"changelog:arbiter-changelog-md:2026-10-21"})
        self.assertEqual(self.graph.nodes["finding:f:race00000001"].attrs["traced_to"], ["arbiter/ARB-053", "arbiter/ARB-054"])
        self.assertEqual(self.graph.nodes[rule].attrs["requirements"], ["arbiter/ARB-053", "arbiter/ARB-054"])
        self.assertNotIn("arbiter/ARB-060", self.traced("arbiter/concurrency.check-then-act-unlocked"), "another bullet's id is not borrowed")

    def test_the_vendored_changelog_becomes_entries_that_record_its_requirements(self) -> None:
        entry = self.graph.nodes["changelog:arbiter-changelog-md:2026-10-21"]
        self.assertEqual((entry.kind, entry.attrs["workspace"], entry.file), ("changelog", "arbiter", "arbiter/CHANGELOG.md"))
        self.assertEqual(self.targets(entry.id, "records"), {"req:arbiter/ARB-053", "req:arbiter/ARB-054", "req:arbiter/ARB-060"})
        self.assertEqual(og.EDGE_LAYER["mentions_rule"], og.LAYER_GOVERNANCE)
        self.assertEqual((og.LINEAGE_FLOW["specified_by"], og.LINEAGE_FLOW["mentions_rule"]), ("up", "down"))

    def test_a_prefixed_rule_id_in_the_root_changelog_traces_to_the_root_requirement(self) -> None:
        self.assertEqual(self.traced("arbiter/sql.string-concat"), {"REQ-001"})
        self.assertEqual(self.graph.nodes["finding:f:sql000000002"].attrs["traced_to"], ["REQ-001"])

    def test_a_rule_pack_named_in_a_bullet_and_its_header_trace_its_rules(self) -> None:
        self.assertEqual(self.traced("arbiter/python.http-no-timeout"), {"arbiter/ARB-060", "arbiter/ARB-061"})

    def test_a_registry_entry_that_names_a_rule_traces_it(self) -> None:
        self.assertEqual(self.traced("arbiter/supply.unpinned-dependency"), {"arbiter/ARB-062"})
        edge = next(e for e in self.graph.edges if e.source == "arbiter_rule:arbiter/supply.unpinned-dependency" and e.type == "specified_by")
        self.assertEqual(edge.resolver, "requirements-registry")

    def test_unknown_and_unraised_rule_ids_are_ignored(self) -> None:
        names = {n.name for n in self.graph.nodes.values() if n.kind == "arbiter_rule"}
        for absent in ("arbiter/concurrency.never-raised-anywhere", "arbiter/nope.not-a-rule", "arbiter/python.never-fires"):
            self.assertNotIn(absent, names)
        self.assertEqual(self.traced("arbiter/ast.function-too-long"), set(), "no record names it: no trace is invented")
        self.assertNotIn("traced_to", self.graph.nodes["finding:f:long00000005"].attrs)
        stats = self.graph.layer_stats[og.LAYER_ASSURANCE]
        self.assertEqual(stats["traced_findings"], 4)

    def test_requirements_the_report_declares_are_read_as_extracted(self) -> None:
        report = self.repo / "arbiter-out" / "report.json"
        data = json.loads(report.read_text())
        data["findings"][4]["requirements"] = ["ARB-064"]                       # on the finding
        data["rules"] = [{"id": "arbiter/supply.unpinned-dependency", "requirements": ["arbiter/ARB-061"]}]  # rule metadata
        report.write_text(json.dumps(data))
        og._REPORT_CACHE.clear()
        graph, _ = build(self.repo)
        edges = {(e.source, e.target): e for e in graph.edges if e.type == "specified_by"}
        declared = edges[("arbiter_rule:arbiter/ast.function-too-long", "req:arbiter/ARB-064")]
        self.assertEqual((declared.provenance, declared.resolver), ("EXTRACTED", "arbiter-report"))
        self.assertEqual(edges[("arbiter_rule:arbiter/supply.unpinned-dependency", "req:arbiter/ARB-061")].provenance, "EXTRACTED")
        self.assertEqual(edges[("arbiter_rule:arbiter/supply.unpinned-dependency", "req:arbiter/ARB-062")].provenance, "INFERRED", "the inferred sources still add theirs")
        self.assertEqual(graph.nodes["finding:f:long00000005"].attrs["traced_to"], ["arbiter/ARB-064"])

    def test_why_and_lineage_of_an_arb_id_reach_its_rules_and_findings(self) -> None:
        why = og.why(self.path, "arbiter/ARB-053")
        self.assertEqual([i["name"] for i in why["sections"]["arbiter rules"]], ["arbiter/concurrency.check-then-act-unlocked"])
        self.assertEqual([i["name"] for i in why["sections"]["findings"]], ["f:race00000001"])
        down = og.lineage(self.path, "arbiter/ARB-053", direction="down")
        reached = {(n["kind"], n["name"]) for n in down["downstream"]}
        self.assertIn(("arbiter_rule", "arbiter/concurrency.check-then-act-unlocked"), reached)
        self.assertIn(("finding", "f:race00000001"), reached)
        up = og.lineage(self.path, "f:race00000001", direction="up")
        self.assertIn("arbiter/ARB-053", {n["name"] for n in up["upstream"]})
        traced = og.why(self.path, "f:race00000001")["sections"]["traced to"]
        self.assertEqual([i["name"] for i in traced], ["arbiter/ARB-053", "arbiter/ARB-054"])
        self.assertTrue(all("via arbiter/concurrency.check-then-act-unlocked" in i["via"] for i in traced))
        rule = og.why(self.path, "arbiter/concurrency.check-then-act-unlocked")
        self.assertEqual({i["name"] for i in rule["sections"]["specified by"]}, {"arbiter/ARB-053", "arbiter/ARB-054"})

    def test_root_commit_history_ignores_the_vendored_changelog_headings(self) -> None:
        logged = {e.target for e in self.graph.edges if e.type == "logged_in"}
        self.assertFalse(any(t.startswith("changelog:arbiter-changelog-md:") for t in logged))


class TestViewerFindingPanel(unittest.TestCase):
    TEMPLATE = (ROOT / ".ai" / "graph-viewer" / "viewer.html").read_text(encoding="utf-8")

    def test_the_panel_has_finding_and_fix_blocks_built_with_text_nodes(self) -> None:
        body = self.TEMPLATE[self.TEMPLATE.index("function renderFinding"):self.TEMPLATE.index("function renderDetail")]
        self.assertIn("['Finding', a.description", body)
        self.assertIn("['Fix', a.remediation", body)
        self.assertIn("el('div', 'fx', b[1])", body, "the text goes in through textContent, never innerHTML")
        self.assertNotIn("innerHTML", body)
        self.assertIn("'traced to'", body)
        self.assertIn("traceThrough(n.id, t.rule, t.req)", body)

    def test_search_reads_the_finding_text_fix_and_traced_ids(self) -> None:
        haystack = re.search(r"function haystack\(n\) \{(.*?)\n  \}", self.TEMPLATE, re.S).group(1)
        for key in ("a.description", "a.remediation", "a.traced_to"):
            self.assertIn(key, haystack)
        for edge in ("specified_by", "mentions_rule"):
            self.assertIn(f"{edge}: '", self.TEMPLATE)


if __name__ == "__main__":
    unittest.main()
