"""Tests for the governance and assurance graph layers and the failure ledger.

Stdlib only (unittest) and no tree-sitter: the code layer is hand-built so the
layers can be checked in isolation. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_graph as og  # noqa: E402


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def module(graph: og.Graph, relpath: str, language: str = "python") -> og.GraphNode:
    return graph.add_node(og.GraphNode(relpath, "module", Path(relpath).stem, relpath, relpath, 1, 20, language))


def function(graph: og.Graph, relpath: str, name: str, language: str = "python") -> og.GraphNode:
    node = graph.add_node(og.GraphNode(f"{relpath}::{name}", "function", name, f"{Path(relpath).stem}.{name}", relpath, 2, 5, language))
    graph.add_edge(relpath, node.id, "defines", "EXTRACTED", "syntax")
    return node


REGISTRY = {
    "version": "1.0.0",
    "requirement_id_prefix": "REQ",
    "requirements": [
        {"id": "REQ-001", "category": "Feature", "title": "Add checkout", "description": "d", "priority": "high",
         "status": "completed", "minimum_access_scope": ["src/app.py", "docs"], "acceptance_criteria": [],
         "validation_required": [], "documentation_required": [], "risk_notes": []},
        {"id": "REQ-002", "category": "Defect", "title": "Fix rounding", "description": "d", "priority": "high",
         "status": "pending", "minimum_access_scope": ["src"], "acceptance_criteria": [],
         "validation_required": [], "documentation_required": [], "risk_notes": []},
    ],
}

CHANGELOG = """# Changelog

## 2026-09-01 (2) (Feature - Checkout)

Added checkout for `REQ-001`; touches `src/app.py` and `docs/guide.md`.

```
## 2026-01-01 not a heading, it is inside a fence
```

## 2026-09-01

Fixed rounding for REQ-002 without naming any file.

## Unreleased

Nothing yet.
"""

LEDGER = {
    "version": "1.0.0",
    "failure_id_prefix": "FAIL",
    "failures": [
        {"id": "FAIL-001", "date": "2026-09-02", "title": "Rounding drops cents", "status": "fixed", "severity": "high",
         "symptom": "total is 10.00 instead of 10.05", "root_cause": "float used for money", "requirement": "REQ-002",
         "affected": ["src/app.py", "app.total"], "fix_summary": "use Decimal",
         "regression_tests": ["tests/test_app.py::test_total_keeps_cents"],
         "prevention_rules": ["money.no_floats", ".ai/playbooks/debugging.md"], "prevention_notes": "review checklist"},
        {"id": "FAIL-002", "date": "2026-09-05", "title": "Rounding drops cents again", "status": "open",
         "symptom": "same symptom in tax", "recurrence_of": "FAIL-001", "affected": ["src/does_not_exist.py"]},
    ],
}


class Fixture(unittest.TestCase):
    """A small repository on disk plus a hand-built code layer."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        write(self.root / ".ai/requirements/requirements.json", json.dumps(REGISTRY))
        write(self.root / ".ai/failures/failure-ledger.json", json.dumps(LEDGER))
        write(self.root / ".ai/rules/money.json", json.dumps(
            {"rulepack_id": "money", "version": "1", "title": "t", "purpose": "p", "applies_to": [],
             "rules": [{"id": "money.no_floats", "severity": "required", "statement": "Never use floats for money."}]}))
        write(self.root / ".ai/playbooks/debugging.md", "# debugging\n")
        write(self.root / "CHANGELOG.md", CHANGELOG)
        write(self.root / "docs/guide.md", "guide\n")
        write(self.root / "src/app.py", "def total(): pass\n")
        write(self.root / "src/other.py", "def other(): pass\n")
        write(self.root / "tests/test_app.py", "def test_total_keeps_cents(): pass\n")

        self.graph = og.Graph()
        module(self.graph, "src/app.py")
        module(self.graph, "src/other.py")
        module(self.graph, "tests/test_app.py")
        function(self.graph, "src/app.py", "total")
        function(self.graph, "src/other.py", "other")
        function(self.graph, "tests/test_app.py", "test_total_keeps_cents")
        self.graph.add_edge("tests/test_app.py::test_total_keeps_cents", "src/app.py::total", "calls", "EXTRACTED", "syntax")

    def git(self, *args: str) -> None:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True, env=env)

    def edges(self, edge_type: str) -> set[tuple[str, str]]:
        return {(e.source, e.target) for e in self.graph.edges if e.type == edge_type}


class TestPathHeuristics(unittest.TestCase):
    def test_test_paths_are_recognised(self) -> None:
        for path in ("tests/test_app.py", "src/app_test.py", "web/src/a.test.tsx", "web/a.spec.ts",
                     "backend/src/test/java/x/FooTest.java", "pkg/foo_test.go", "conftest.py"):
            self.assertTrue(og.is_test_path(path), path)

    def test_source_paths_are_not_tests(self) -> None:
        for path in ("src/app.py", "backend/src/main/java/x/Foo.java", "latest/report.py", None, ""):
            self.assertFalse(og.is_test_path(path), path)


class TestChangelogParsing(unittest.TestCase):
    def test_headings_dates_and_fences(self) -> None:
        entries = og.parse_changelog(CHANGELOG)
        self.assertEqual([e["date"] for e in entries], ["2026-09-01", "2026-09-01", None])
        self.assertEqual(entries[0]["seq"], "2")
        self.assertEqual(entries[0]["title"], "Feature - Checkout")
        self.assertNotIn("not a heading", " ".join(e["heading"] for e in entries))
        self.assertEqual(entries[2]["heading"], "Unreleased")


class TestGovernanceLayer(Fixture):
    def test_requirements_touch_declared_files_and_directories(self) -> None:
        stats = og.add_governance_layer(self.graph, self.root)
        self.assertEqual(stats["requirements"], 2)
        touches = self.edges("touches")
        self.assertIn(("req:REQ-001", "src/app.py"), touches)
        self.assertIn(("req:REQ-001", "file:docs"), touches)  # a directory with no parsed code becomes a file node
        self.assertEqual(self.graph.nodes["file:docs"].attrs, {"directory": True})
        # REQ-002 scopes `src`, which holds two modules: below the fan-out limit, so both are linked.
        self.assertIn(("req:REQ-002", "src/other.py"), touches)

    def test_wide_scopes_link_the_directory_once(self) -> None:
        for number in range(og.DIRECTORY_FANOUT_LIMIT + 5):
            module(self.graph, f"wide/m{number}.py")
        write(self.root / "wide/m0.py", "x\n")
        registry = json.loads(json.dumps(REGISTRY))
        registry["requirements"][0]["minimum_access_scope"] = ["wide"]
        write(self.root / ".ai/requirements/requirements.json", json.dumps(registry))
        og.add_governance_layer(self.graph, self.root)
        targets = {t for s, t in self.edges("touches") if s == "req:REQ-001"}
        self.assertEqual(targets, {"file:wide"})

    def test_changelog_entries_record_requirements_and_mention_files(self) -> None:
        og.add_governance_layer(self.graph, self.root)
        entry = "changelog:2026-09-01#2"
        self.assertIn((entry, "req:REQ-001"), self.edges("records"))
        mentions = {t for s, t in self.edges("mentions") if s == entry}
        self.assertEqual(mentions, {"src/app.py", "file:docs/guide.md"})
        self.assertEqual(self.graph.nodes[entry].layer, og.LAYER_GOVERNANCE)
        self.assertIn(("changelog:2026-09-01", "req:REQ-002"), self.edges("records"))



class TestHistoryLayer(Fixture):
    def commit_all(self, subject: str, body: str | None = None) -> None:
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", subject, *(["-m", body] if body else []))

    def build(self) -> dict:
        og.add_governance_layer(self.graph, self.root)
        return og.add_history_layer(self.graph, self.root)

    def test_commits_deliver_requirements_and_prove_what_they_touched(self) -> None:
        self.commit_all("Add checkout (REQ-001)", "Pulled from another project's REQ-002")
        stats = self.build()
        commits = [n for n in self.graph.nodes.values() if n.kind == "commit"]
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0].layer, og.LAYER_HISTORY)
        commit_id = commits[0].id
        # The subject names REQ-001; the body's REQ-002 is another project's and must be ignored.
        self.assertEqual({t for s, t in self.edges("delivers") if s == commit_id}, {"req:REQ-001"})
        self.assertIn((commit_id, "src/app.py"), self.edges("modifies"))
        inferred = [e for e in self.graph.edges if e.type == "touches" and e.provenance == "INFERRED"]
        self.assertTrue(any(e.source == "req:REQ-001" and e.target == "src/other.py" for e in inferred))
        self.assertEqual(stats["commits"], 1)
        self.assertEqual(self.graph.nodes["req:REQ-001"].attrs["commits"], 1)

    def test_every_commit_is_history_even_without_a_requirement_id(self) -> None:
        self.commit_all("tidy up")
        self.build()
        commits = [n for n in self.graph.nodes.values() if n.kind == "commit"]
        self.assertEqual(len(commits), 1)
        # Nothing in the message names a requirement; the only ties are the INFERRED ones from the changelog it added.
        self.assertFalse([e for e in self.graph.edges if e.type == "delivers" and e.provenance == "EXTRACTED"])
        self.assertIn((commits[0].id, "src/app.py"), self.edges("modifies"))

    def test_commits_chain_in_order(self) -> None:
        self.commit_all("first (REQ-001)")
        write(self.root / "src/other.py", "def other(): return 2\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "second (REQ-001)")
        self.build()
        self.assertEqual(len(self.edges("follows")), 1)

    def test_a_squash_commit_without_an_id_is_tied_to_the_requirement_its_changelog_entry_records(self) -> None:
        self.commit_all("Merge feature branch (#12)")
        # The same commit added the changelog entry, so the entry's heading identifies the work.
        og.add_governance_layer(self.graph, self.root)
        og.add_history_layer(self.graph, self.root)
        commit = next(n for n in self.graph.nodes.values() if n.kind == "commit")
        logged = {t for s, t in self.edges("logged_in") if s == commit.id}
        self.assertIn("changelog:2026-09-01#2", logged)
        delivered = {(e.target, e.provenance) for e in self.graph.edges if e.type == "delivers" and e.source == commit.id}
        # REQ-001 is in the heading-less body of entry (2); it is recorded, so the commit is tied to it (INFERRED).
        self.assertIn(("req:REQ-001", "INFERRED"), delivered)

    def test_no_git_repository_is_a_note_not_a_crash(self) -> None:
        stats = self.build()
        self.assertEqual(stats["commits"], 0)
        self.assertTrue(any("not a git repository" in note for note in self.graph.notes))

    def test_a_repository_with_no_commits_yet(self) -> None:
        self.git("init", "-q")
        stats = self.build()
        self.assertEqual(stats["commits"], 0)
        self.assertTrue(any("no commits yet" in note for note in self.graph.notes))


class TestWorkspaceLayer(Fixture):
    def test_rulepacks_rules_playbooks_and_checklists_become_nodes(self) -> None:
        write(self.root / ".ai/checklists/pre.md", "# Pre-completion\n")
        stats = og.add_workspace_layer(self.graph, self.root)
        self.assertEqual((stats["rulepacks"], stats["rules"], stats["playbooks"], stats["checklists"]), (1, 1, 1, 1))
        rule = self.graph.nodes["rule:money.no_floats"]
        self.assertEqual(rule.layer, og.LAYER_WORKSPACE)
        self.assertEqual(self.graph.nodes["file:.ai/playbooks/debugging.md"].kind, "playbook")
        self.assertIn(("file:.ai/rules/money.json", "rule:money.no_floats"), self.edges("defines"))

    def test_failures_link_to_workspace_nodes_not_duplicates(self) -> None:
        og.add_workspace_layer(self.graph, self.root)
        og.add_assurance_layer(self.graph, self.root)
        prevented = {t for s, t in self.edges("prevented_by") if s == "failure:FAIL-001"}
        self.assertEqual(prevented, {"rule:money.no_floats", "file:.ai/playbooks/debugging.md"})
        self.assertEqual(self.graph.nodes["file:.ai/playbooks/debugging.md"].layer, og.LAYER_WORKSPACE)

    def test_a_project_without_omni_has_an_empty_workspace_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stats = og.add_workspace_layer(og.Graph(), Path(tmp))
        self.assertEqual(stats["rules"], 0)


class TestAssuranceLayer(Fixture):
    def build(self) -> dict:
        og.add_governance_layer(self.graph, self.root)
        return og.add_assurance_layer(self.graph, self.root)

    def test_test_nodes_move_to_the_assurance_layer(self) -> None:
        stats = self.build()
        self.assertEqual(self.graph.nodes["tests/test_app.py"].layer, og.LAYER_ASSURANCE)
        self.assertTrue(self.graph.nodes["tests/test_app.py::test_total_keeps_cents"].attrs["test"])
        self.assertEqual(self.graph.nodes["src/app.py"].layer, og.LAYER_CODE)
        self.assertEqual(stats["test_files"], 1)

    def test_verifies_edges_link_test_files_to_the_code_they_call(self) -> None:
        self.build()
        self.assertIn(("tests/test_app.py", "src/app.py::total"), self.edges("verifies"))
        self.assertNotIn(("tests/test_app.py", "src/other.py::other"), self.edges("verifies"))

    def test_failure_edges(self) -> None:
        stats = self.build()
        self.assertEqual(stats["failures"], 2)
        self.assertIn(("failure:FAIL-001", "src/app.py"), self.edges("affects"))
        self.assertIn(("failure:FAIL-001", "src/app.py::total"), self.edges("affects"))  # resolved by qualified name
        self.assertIn(("failure:FAIL-001", "req:REQ-002"), self.edges("arose_in"))
        self.assertIn(("tests/test_app.py::test_total_keeps_cents", "failure:FAIL-001"), self.edges("guards"))
        prevented = {t for s, t in self.edges("prevented_by") if s == "failure:FAIL-001"}
        self.assertEqual(prevented, {"rule:money.no_floats", "file:.ai/playbooks/debugging.md"})
        self.assertEqual(self.graph.nodes["rule:money.no_floats"].summary, "Never use floats for money.")
        self.assertIn(("failure:FAIL-002", "failure:FAIL-001"), self.edges("recurs"))

    def test_unresolved_references_are_reported_not_invented(self) -> None:
        stats = self.build()
        self.assertEqual(stats["unresolved"], 1)
        self.assertTrue(any("src/does_not_exist.py" in note for note in self.graph.notes))
        self.assertFalse([e for e in self.graph.edges if e.type == "affects" and e.source == "failure:FAIL-002"])

    def test_regression_test_outside_naming_conventions_is_promoted(self) -> None:
        write(self.root / "checks/rounding.py", "x\n")
        module(self.graph, "checks/rounding.py")
        ledger = json.loads(json.dumps(LEDGER))
        ledger["failures"][0]["regression_tests"] = ["checks/rounding.py"]
        write(self.root / ".ai/failures/failure-ledger.json", json.dumps(ledger))
        self.build()
        self.assertEqual(self.graph.nodes["checks/rounding.py"].layer, og.LAYER_ASSURANCE)
        self.assertIn(("checks/rounding.py", "failure:FAIL-001"), self.edges("guards"))

    def test_layers_serialise_only_when_not_code(self) -> None:
        self.build()
        data = self.graph.to_json(".", ["python"], {})
        by_id = {n["id"]: n for n in data["nodes"]}
        self.assertNotIn("layer", by_id["src/app.py"])
        self.assertEqual(by_id["req:REQ-001"]["layer"], "governance")
        self.assertEqual(by_id["failure:FAIL-001"]["layer"], "assurance")
        edge_layers = {e["type"]: e.get("layer") for e in data["edges"]}
        self.assertEqual(edge_layers["touches"], "governance")
        self.assertEqual(edge_layers["guards"], "assurance")
        self.assertIsNone(edge_layers["calls"])


class TestLedgerCheck(unittest.TestCase):
    def check(self, failures: list[dict]) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp) / og.FAILURE_LEDGER_PATH, json.dumps({"version": "1", "failure_id_prefix": "FAIL", "failures": failures}))
            return og.check_failure_ledger(Path(tmp))["problems"]

    def test_a_fixed_failure_needs_its_reasoning(self) -> None:
        problems = self.check([{"id": "FAIL-001", "title": "t", "status": "fixed", "symptom": "s"}])
        text = "\n".join(problems)
        for needed in ("root_cause", "fix_summary", "regression_tests", "prevention_rules"):
            self.assertIn(needed, text)

    def test_an_honest_reason_replaces_a_missing_test(self) -> None:
        problems = self.check([{
            "id": "FAIL-001", "title": "t", "status": "fixed", "symptom": "s", "root_cause": "r", "fix_summary": "f",
            "no_test_reason": "needs a live database", "prevention_notes": "n"}])
        self.assertEqual(problems, [])

    def test_open_failures_and_duplicates(self) -> None:
        self.assertEqual(self.check([{"id": "FAIL-001", "title": "t", "status": "open", "symptom": "s"}]), [])
        dup = self.check([{"id": "FAIL-001", "title": "t", "status": "open", "symptom": "s"}] * 2)
        self.assertTrue(any("duplicate" in p for p in dup))

    def test_missing_ledger_is_fine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(og.check_failure_ledger(Path(tmp))["ok"])


class TestWhy(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add checkout (REQ-001)")
        og.add_governance_layer(self.graph, self.root)
        og.add_history_layer(self.graph, self.root)
        og.add_assurance_layer(self.graph, self.root)
        self.path = self.root / "graph.json"
        self.path.write_text(json.dumps(self.graph.to_json(".", ["python"], {})), encoding="utf-8")

    def names(self, result: dict, section: str) -> list[str]:
        return [item["name"] for item in result["sections"].get(section, [])]

    def test_file_traversal_joins_every_layer(self) -> None:
        result = og.why(self.path, "src/app.py")
        self.assertTrue(result["ok"])
        self.assertEqual(self.names(result, "requirements")[0], "REQ-001")  # git-proven outranks scope-only
        self.assertIn("REQ-002", self.names(result, "requirements"))
        self.assertIn("2026-09-01 (2)", self.names(result, "changelog"))
        self.assertTrue(self.names(result, "commits"))
        self.assertIn("test_app", self.names(result, "tests"))
        self.assertIn("FAIL-001", self.names(result, "failures"))
        self.assertIn("money.no_floats", self.names(result, "rules and playbooks from those failures"))
        self.assertNotIn("gaps", result["sections"])

    def test_untested_code_is_flagged(self) -> None:
        result = og.why(self.path, "src/other.py")
        self.assertIn("gaps", result["sections"])
        self.assertNotIn("tests", result["sections"])

    def test_failure_and_requirement_views(self) -> None:
        failure = og.why(self.path, "FAIL-001")
        self.assertIn("test_total_keeps_cents", self.names(failure, "regression tests"))
        self.assertIn("REQ-002", self.names(failure, "requirement"))
        requirement = og.why(self.path, "REQ-001")
        self.assertIn("app", self.names(requirement, "files touched"))
        self.assertIn("2026-09-01 (2)", self.names(requirement, "changelog"))

    def test_unknown_node(self) -> None:
        self.assertFalse(og.why(self.path, "no-such-thing")["ok"])

    def test_timeline_orders_everything_dated_that_is_tied_to_a_node(self) -> None:
        result = og.timeline(self.path, "REQ-001")
        self.assertTrue(result["ok"])
        kinds = {e["kind"] for e in result["events"]}
        self.assertTrue({"commit", "changelog", "requirement"} <= kinds)
        dates = [e["date"] for e in result["events"]]
        self.assertEqual(dates, sorted(dates))

    def test_commit_view_links_requirement_changelog_and_files(self) -> None:
        commit = next(n for n in json.loads(self.path.read_text())["nodes"] if n["kind"] == "commit")
        result = og.why(self.path, commit["id"])
        self.assertIn("REQ-001", self.names(result, "requirements"))
        self.assertIn("app", self.names(result, "files modified"))


class TestCli(Fixture):
    def run_omni(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ROOT / "make_ai.py"), *args], cwd=self.root, capture_output=True, text=True)

    def test_defect_requirement_cannot_complete_without_a_failure_entry(self) -> None:
        write(self.root / ".ai/failures/failure-ledger.json", json.dumps({"version": "1", "failure_id_prefix": "FAIL", "failures": []}))
        refused = self.run_omni("requirement", "complete", "REQ-002")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("failure-ledger entry", refused.stderr)
        self.assertEqual(json.loads((self.root / ".ai/requirements/requirements.json").read_text())["requirements"][1]["status"], "pending")

    def test_a_short_excuse_is_not_enough_but_a_reason_is_recorded(self) -> None:
        write(self.root / ".ai/failures/failure-ledger.json", json.dumps({"version": "1", "failure_id_prefix": "FAIL", "failures": []}))
        self.assertEqual(self.run_omni("requirement", "complete", "REQ-002", "--no-failure-entry", "n/a").returncode, 1)
        done = self.run_omni("requirement", "complete", "REQ-002", "--no-failure-entry", "typo in a comment, nothing failed")
        self.assertEqual(done.returncode, 0, done.stderr)
        item = json.loads((self.root / ".ai/requirements/requirements.json").read_text())["requirements"][1]
        self.assertEqual(item["status"], "completed")
        self.assertIn("typo in a comment", " ".join(item["risk_notes"]))

    def test_a_ledger_entry_unblocks_completion(self) -> None:
        # LEDGER's FAIL-001 already references REQ-002.
        self.assertEqual(self.run_omni("requirement", "complete", "REQ-002").returncode, 0)

    def test_feature_requirements_are_not_gated(self) -> None:
        self.assertEqual(self.run_omni("requirement", "complete", "REQ-001").returncode, 0)

    def test_failure_add_assigns_ids_and_reports_what_is_missing(self) -> None:
        added = self.run_omni("failure", "add", "--title", "Crash on empty cart", "--symptom", "NPE", "--status", "fixed", "--requirement", "REQ-001")
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertIn("FAIL-003", added.stdout)
        self.assertIn("still needed", added.stdout)
        self.assertIn("root_cause", added.stdout)
        updated = self.run_omni("failure", "update", "FAIL-003", "--root-cause", "unchecked null", "--fix", "guard",
                                "--tests", "tests/test_app.py::test_total_keeps_cents", "--prevention-notes", "null-check rule")
        self.assertEqual(updated.returncode, 0, updated.stderr)
        self.assertNotIn("still needed", updated.stdout)
        self.assertEqual(self.run_omni("failure", "show", "3").returncode, 0)

    def test_failure_check_flags_missing_files_and_unknown_rules(self) -> None:
        ledger = json.loads(json.dumps(LEDGER))
        ledger["failures"][0]["prevention_rules"] = ["no.such.rule"]
        write(self.root / ".ai/failures/failure-ledger.json", json.dumps(ledger))
        result = self.run_omni("failure", "check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("src/does_not_exist.py", result.stdout)
        self.assertIn("no.such.rule", result.stdout)


if __name__ == "__main__":
    unittest.main()
