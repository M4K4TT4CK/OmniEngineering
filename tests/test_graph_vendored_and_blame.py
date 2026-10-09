"""Tests for REQ-051 (a finding traces to the commit, and so the requirement, that introduced its line) and REQ-052
(vendored workspaces' ledgers and registries join the graph under a workspace prefix).

Stdlib only and no tree-sitter: the code layer is the hand-built fixture from test_graph_layers; git is real. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import omni_graph as og  # noqa: E402
from test_graph_findings import FindingsFixture, REPORT  # noqa: E402
from test_graph_layers import write  # noqa: E402

APP_V1 = "def total():\n    pass\n"
APP_V2 = "def total():\n    return 1 + 1\n    pass\n"   # line 2 is new in the second commit; line 3 is the first commit's line 2


def _report(commit: str | None, findings: list[dict] | None = None) -> dict:
    data = dict(REPORT, findings=findings if findings is not None else REPORT["findings"])
    if commit:
        data["repos"] = [{"id": "root", "path": ".", "commit": commit}]
    return data


class BlameFixture(FindingsFixture):
    """The findings fixture as a git repository: commit 1 (REQ-001) writes src/app.py, commit 2 (REQ-002) adds line 2."""

    def setUp(self) -> None:
        super().setUp()
        write(self.root / "src/app.py", APP_V1)
        write(self.root / ".gitignore", "arbiter-out/\ngraph.json\n")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add checkout (REQ-001)")
        write(self.root / "src/app.py", APP_V2)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Return the total (REQ-002)")
        self.head = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        findings = [
            dict(REPORT["findings"][0], location={"path": "src/app.py", "start_line": 2}),            # the line commit 2 wrote
            dict(REPORT["findings"][1], id="f:bbb222", location={"path": "src/app.py", "start_line": 3}),  # the line commit 1 wrote
            REPORT["findings"][2],                                                                 # docs/guide.md:1, commit 1
        ]
        write(self.root / self.report_rel, json.dumps(_report(self.head[:7], findings)))

    def build_all(self) -> Path:
        og.add_governance_layer(self.graph, self.root)
        og.add_workspace_layer(self.graph, self.root)
        og.add_history_layer(self.graph, self.root)
        og.add_assurance_layer(self.graph, self.root)
        self.path = self.root / "graph.json"
        self.path.write_text(json.dumps(self.graph.to_json(str(self.root), ["python"], {})), encoding="utf-8")
        return self.path

    def introduced(self, finding: str) -> list[str]:
        return [e.target for e in self.graph.edges if e.type == "introduced_by" and e.source == f"finding:{finding}"]

    def commit_for(self, requirement: str) -> str:
        return next(e.source for e in self.graph.edges if e.type == "delivers" and e.target == f"req:{requirement}")


class TestBlame(BlameFixture):
    def test_a_finding_on_a_committed_line_has_exactly_one_introducing_commit_and_its_requirement(self) -> None:
        self.build_all()
        self.assertEqual(self.introduced("f:aaa111"), [self.commit_for("REQ-002")])
        self.assertEqual(self.introduced("f:bbb222"), [self.commit_for("REQ-001")])
        self.assertEqual(self.introduced("f:ccc333"), [self.commit_for("REQ-001")])
        node = self.graph.nodes["finding:f:aaa111"]
        self.assertEqual(node.attrs["introduced_by_commit"], self.head)
        self.assertEqual(node.attrs["introduced_by"], self.head[:7])
        self.assertEqual(self.graph.layer_stats["assurance"]["introduced_by"], 3)
        self.assertEqual(self.graph.layer_stats["assurance"]["uncommitted"], 0)
        self.assertEqual(og.EDGE_LAYER["introduced_by"], og.LAYER_ASSURANCE)
        self.assertEqual(og.LINEAGE_FLOW["introduced_by"], "up")
        # the cites edge from the req: tag is still there, as context
        self.assertIn(("finding:f:aaa111", "req:REQ-001"), self.edges("cites"))

    def test_one_blame_per_file_not_per_finding(self) -> None:
        real_run = subprocess.run
        blames: list[list[str]] = []

        def counting_run(argv, *args, **kwargs):
            if "blame" in argv:
                blames.append(list(argv))
            return real_run(argv, *args, **kwargs)

        with mock.patch.object(og.subprocess, "run", side_effect=counting_run):
            self.build_all()
        self.assertEqual(sorted(argv[-1] for argv in blames), ["docs/guide.md", "src/app.py"])  # two files, three findings
        app = next(argv for argv in blames if argv[-1] == "src/app.py")
        self.assertEqual(app[app.index("-L") + 1], "2,2")
        self.assertIn("3,3", app)
        self.assertIn(self.head[:7], app)  # blamed at the commit the report scanned

    def test_the_blame_is_taken_at_the_scanned_commit(self) -> None:
        first = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD~1"], capture_output=True, text=True, check=True).stdout.strip()
        findings = [dict(REPORT["findings"][0], location={"path": "src/app.py", "start_line": 2})]
        write(self.root / self.report_rel, json.dumps(_report(first[:7], findings)))
        self.build_all()
        # at HEAD~1 line 2 of src/app.py is the first commit's `pass`, so the finding is REQ-001's, not REQ-002's
        self.assertEqual(self.introduced("f:aaa111"), [self.commit_for("REQ-001")])
        self.assertEqual(self.graph.nodes["finding:f:aaa111"].attrs["introduced_by_commit"], first)

    def test_an_uncommitted_line_yields_no_edge_and_no_error(self) -> None:
        write(self.root / "src/app.py", APP_V2 + "    x = 1\n")                       # line 4 is a working-tree edit
        write(self.root / "src/new.py", "def new(): pass\n")                            # an untracked file
        findings = [
            dict(REPORT["findings"][0], location={"path": "src/app.py", "start_line": 4}),
            dict(REPORT["findings"][1], id="f:bbb222", location={"path": "src/new.py", "start_line": 1}),
            dict(REPORT["findings"][2], location={"path": "src/app.py", "start_line": 2}),   # still committed
        ]
        write(self.root / self.report_rel, json.dumps(_report(self.head[:7], findings)))
        self.build_all()
        for finding in ("f:aaa111", "f:bbb222"):
            self.assertEqual(self.introduced(finding), [])
            self.assertEqual(self.graph.nodes[f"finding:{finding}"].attrs["introduced_by"], "uncommitted")
            self.assertNotIn("introduced_by_commit", self.graph.nodes[f"finding:{finding}"].attrs)
        self.assertEqual(self.introduced("f:ccc333"), [self.commit_for("REQ-002")])
        self.assertEqual(self.graph.layer_stats["assurance"]["uncommitted"], 2)
        self.assertFalse([n for n in self.graph.notes if "blame" in n.lower()])
        why = og.why(self.build_all(), "f:aaa111")
        self.assertEqual(why["sections"]["introduced by"][0]["name"], "uncommitted change")
        self.assertIn("introduced by  uncommitted change", og.render_findings_tree(og.findings(self.path)))

    def test_outside_git_every_finding_is_uncommitted_and_nothing_breaks(self) -> None:
        subprocess.run(["rm", "-rf", str(self.root / ".git")], check=True)
        self.build_all()
        for finding in ("f:aaa111", "f:bbb222", "f:ccc333"):
            self.assertEqual(self.graph.nodes[f"finding:{finding}"].attrs["introduced_by"], "uncommitted")
        self.assertEqual(self.edges("introduced_by"), set())

    def test_a_commit_beyond_the_history_layer_is_created_with_its_requirement(self) -> None:
        og.add_governance_layer(self.graph, self.root)
        og.add_history_layer(self.graph, self.root, max_commits=1)   # only HEAD (REQ-002) is in the history layer
        og.add_assurance_layer(self.graph, self.root)
        commit_ids = {n.id for n in self.graph.nodes.values() if n.kind == "commit"}
        self.assertEqual(len(commit_ids), 2)   # the blame added the first commit
        first = self.introduced("f:bbb222")[0]
        node = self.graph.nodes[first]
        self.assertEqual((node.layer, node.attrs["requirements"]), (og.LAYER_HISTORY, ["REQ-001"]))
        self.assertIn((first, "req:REQ-001"), self.edges("delivers"))
        for key in ("hash", "date", "files_changed", "modules_touched"):
            self.assertIn(key, node.attrs)

    def test_why_and_findings_print_the_introducing_commit(self) -> None:
        path = self.build_all()
        result = og.why(path, "f:aaa111")
        intro = result["sections"]["introduced by"]
        self.assertEqual(len(intro), 1)
        self.assertEqual((intro[0]["kind"], intro[0]["name"], intro[0]["requirements"]), ("commit", self.head[:7], ["REQ-002"]))
        self.assertEqual(intro[0]["summary"], "Return the total (REQ-002)")
        self.assertEqual([i["via"] for i in result["sections"]["requirement"]], ["being worked when the scan ran"])
        cli = subprocess.run([sys.executable, str(ROOT / "make_ai.py"), "graph", "why", "--graph", str(path), "f:aaa111"],
                             cwd=self.root, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertIn(f"introduced by  {self.head[:7]} Return the total (REQ-002) (REQ-002)", cli.stdout)
        listing = og.findings(path)
        item = next(f for g in listing["directories"] for f in g["findings"] if f["name"] == "f:aaa111")
        self.assertEqual((item["introduced_by"]["commit"], item["introduced_by"]["requirements"]), (self.head[:7], ["REQ-002"]))
        self.assertIn("REQ-002", item["requirements"])   # depth 2: finding -> commit -> requirement
        text = og.render_findings_tree(listing)
        self.assertIn(f"introduced by  {self.head[:7]} Return the total (REQ-002) (REQ-002)", text)
        self.assertEqual(og.describe_introduced_by({"commit": None}), "introduced by  uncommitted change")
        self.assertEqual(og.describe_introduced_by({"commit": "abc1234", "subject": "s", "requirements": []}), "introduced by  abc1234 s (no requirement cited)")

    def test_lineage_reaches_the_introducing_requirement_through_the_commit(self) -> None:
        path = self.build_all()
        up = {n["id"] for n in og.lineage(path, "f:aaa111", depth=2)["upstream"]}
        self.assertIn(self.commit_for("REQ-002"), up)
        self.assertIn("req:REQ-002", up)

    def test_blame_lines_parses_porcelain_and_reports_what_git_cannot_blame(self) -> None:
        found = og.blame_lines(self.root, "src/app.py", [2, 3, 3], self.head[:7])
        self.assertEqual(set(found), {2, 3})
        self.assertEqual(found[2], self.head)
        self.assertNotEqual(found[3], self.head)
        self.assertIsNone(og.blame_lines(self.root, "src/app.py", [99], self.head[:7]))   # past the end of the file
        self.assertIsNone(og.blame_lines(self.root, "src/missing.py", [1]))
        self.assertEqual(og.blame_lines(self.root, "src/app.py", []), {})
        write(self.root / "src/app.py", APP_V2 + "    x = 1\n")
        self.assertEqual(og.blame_lines(self.root, "src/app.py", [4]), {4: ""})   # the working tree: an uncommitted line


VENDORED_REGISTRY = {
    "version": "1.0.0", "requirement_id_prefix": "ARB", "id_aliases": {"REQ-001": "ARB-001"},
    "requirements": [
        {"id": "ARB-001", "category": "Feature", "title": "Vendored feature", "priority": "high", "status": "completed",
         "minimum_access_scope": ["src/lib.py"]},
    ],
}

VENDORED_LEDGER = {
    "version": "1.0.0", "failure_id_prefix": "FAIL",
    "failures": [
        {"id": "FAIL-001", "date": "2026-10-02", "title": "Vendored key leak", "status": "fixed", "severity": "high",
         "symptom": "a key in src/lib.py", "how_detected": "arbiter gate finding f:bbb222 on PR #5", "root_cause": "r",
         "requirement": "REQ-001", "affected": ["src/lib.py"], "fix_summary": "f",
         "regression_tests": ["tests/test_lib.py::test_no_key"], "prevention_rules": ["money.no_floats", "docs/HOW.md"]},
        {"id": "FAIL-002", "date": "2026-10-03", "title": "Leak again", "status": "open", "symptom": "symptom names f:ccc333",
         "recurrence_of": "FAIL-001", "affected": ["src/nothing.py"]},
    ],
}


class VendoredFixture(FindingsFixture):
    """The findings fixture plus a vendored workspace `sub/` with its own registry (ARB ids, an alias) and ledger."""

    def setUp(self) -> None:
        super().setUp()
        write(self.root / "sub/.ai/omni-version.json", "{}")
        write(self.root / "sub/.ai/requirements/requirements.json", json.dumps(VENDORED_REGISTRY))
        write(self.root / "sub/.ai/failures/failure-ledger.json", json.dumps(VENDORED_LEDGER))
        write(self.root / "sub/src/lib.py", "KEY = 1\n")
        write(self.root / "sub/tests/test_lib.py", "def test_no_key(): pass\n")
        write(self.root / "sub/docs/HOW.md", "# how\n")
        write(self.root / "node_modules/x/.ai/omni-version.json", "{}")   # an excluded directory is never a workspace


class TestVendoredWorkspaces(VendoredFixture):
    def test_workspaces_are_found_below_the_root_but_never_in_excluded_directories(self) -> None:
        self.assertEqual(og.vendored_workspaces(self.root), ["sub"])
        write(self.root / "packages/two/.ai/omni-version.json", "{}")
        self.assertEqual(og.vendored_workspaces(self.root), ["sub", "packages/two"])

    def test_vendored_requirements_and_failures_are_prefixed_and_never_collide_with_the_root(self) -> None:
        self.build()
        req = self.graph.nodes["req:sub/ARB-001"]
        self.assertEqual((req.name, req.layer, req.file), ("sub/ARB-001", og.LAYER_GOVERNANCE, "sub/.ai/requirements/requirements.json"))
        self.assertEqual((req.attrs["workspace"], req.attrs["bare_id"], req.attrs["aliases"], req.attrs["scope"]), ("sub", "ARB-001", ["REQ-001"], ["sub/src/lib.py"]))
        fail = self.graph.nodes["failure:sub/FAIL-001"]
        self.assertEqual((fail.name, fail.layer, fail.file, fail.attrs["workspace"], fail.attrs["status"]), ("sub/FAIL-001", og.LAYER_ASSURANCE, "sub/.ai/failures/failure-ledger.json", "sub", "fixed"))
        # the root's ids stay bare, and both FAIL-001 entries exist side by side
        self.assertIn("req:REQ-001", self.graph.nodes)
        self.assertIn("failure:FAIL-001", self.graph.nodes)
        self.assertNotIn("workspace", self.graph.nodes["failure:FAIL-001"].attrs)
        self.assertEqual(self.graph.layer_stats["governance"]["vendored_requirements"], 1)
        self.assertEqual(self.graph.layer_stats["assurance"]["vendored_failures"], 2)
        names = [n.name for n in self.graph.nodes.values() if n.kind in ("failure", "requirement")]
        self.assertEqual(len(names), len(set(names)))

    def test_vendored_nodes_sit_in_the_tree_under_the_workspace_directory(self) -> None:
        self.build()
        contains = self.edges("contains")
        self.assertIn(("file:sub", "file:sub/.ai/failures/failure-ledger.json"), contains)
        self.assertIn(("file:sub/.ai/failures/failure-ledger.json", "failure:sub/FAIL-001"), contains)
        self.assertIn(("file:sub", "file:sub/.ai/requirements/requirements.json"), contains)
        self.assertIn(("file:sub/.ai/requirements/requirements.json", "req:sub/ARB-001"), contains)
        self.assertTrue(self.graph.nodes["file:sub"].attrs.get("directory"))
        self.assertEqual(self.graph.nodes["file:sub"].attrs.get("workspace"), "sub")

    def test_vendored_paths_resolve_relative_to_the_workspace_and_the_requirement_through_its_alias(self) -> None:
        self.build()
        self.assertIn(("failure:sub/FAIL-001", "file:sub/src/lib.py"), self.edges("affects"))
        self.assertIn(("file:sub/tests/test_lib.py", "failure:sub/FAIL-001"), self.edges("guards"))
        self.assertIn(("failure:sub/FAIL-001", "req:sub/ARB-001"), self.edges("arose_in"))   # REQ-001 is the alias of ARB-001 there
        self.assertNotIn(("failure:sub/FAIL-001", "req:REQ-001"), self.edges("arose_in"))       # never the root's REQ-001
        self.assertIn(("failure:sub/FAIL-001", "rule:money.no_floats"), self.edges("prevented_by"))
        self.assertIn(("failure:sub/FAIL-001", "file:sub/docs/HOW.md"), self.edges("prevented_by"))
        self.assertIn(("failure:sub/FAIL-002", "failure:sub/FAIL-001"), self.edges("recurs"))
        self.assertIn(("req:sub/ARB-001", "file:sub/src/lib.py"), self.edges("touches"))
        # the root ledger's FAIL-002 names a missing file (one unresolved reference, as in test_graph_layers); the vendored
        # ledger's own miss (FAIL-002 affects src/nothing.py) is never counted: `omni failure check` does not read that ledger
        self.assertEqual(self.graph.layer_stats["assurance"]["unresolved"], 1)

    def test_recorded_as_resolves_across_workspaces_by_finding_id_in_how_detected_or_symptom(self) -> None:
        self.build()
        recorded = self.edges("recorded_as")
        self.assertIn(("finding:f:aaa111", "failure:FAIL-003"), recorded)          # the root ledger, as before
        self.assertIn(("finding:f:bbb222", "failure:sub/FAIL-001"), recorded)   # how_detected in the vendored ledger
        self.assertIn(("finding:f:ccc333", "failure:sub/FAIL-002"), recorded)   # symptom in the vendored ledger
        self.assertEqual(len(recorded), 3)

    def test_the_requirement_lookup_is_root_first(self) -> None:
        self.build()
        lookup = og._requirement_lookup(self.graph)
        self.assertEqual(lookup["REQ-001"], "req:REQ-001")          # the root claims REQ-001; the vendored alias loses
        self.assertEqual(lookup["ARB-001"], "req:sub/ARB-001")

    def test_commits_and_tags_citing_a_vendored_id_reach_the_prefixed_node(self) -> None:
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Pull the subtree (ARB-001)")
        report = dict(REPORT, findings=[dict(REPORT["findings"][0], tags=["req:ARB-001"])])
        write(self.root / self.report_rel, json.dumps(report))
        og.add_governance_layer(self.graph, self.root)
        og.add_history_layer(self.graph, self.root)
        og.add_assurance_layer(self.graph, self.root)
        commit = next(n for n in self.graph.nodes.values() if n.kind == "commit")
        self.assertIn((commit.id, "req:sub/ARB-001"), self.edges("delivers"))
        self.assertEqual(commit.attrs["requirements"], ["sub/ARB-001"])
        self.assertEqual(self.edges("cites"), {("finding:f:aaa111", "req:sub/ARB-001")})

    def test_why_findings_and_show_print_the_prefixed_ids(self) -> None:
        path = self.build()
        why = og.why(path, "f:bbb222")
        self.assertEqual([i["name"] for i in why["sections"]["failure"]], ["sub/FAIL-001"])
        self.assertEqual(why["sections"]["failure"][0]["workspace"], "sub")
        why_failure = og.why(path, "sub/FAIL-001")
        self.assertEqual(why_failure["node"]["id"], "failure:sub/FAIL-001")
        self.assertEqual([i["name"] for i in why_failure["sections"]["requirement"]], ["sub/ARB-001"])
        self.assertEqual([i["name"] for i in why_failure["sections"]["regression tests"]], ["test_lib.py"])
        listing = og.findings(path, depth=1)
        item = next(f for g in listing["directories"] for f in g["findings"] if f["name"] == "f:bbb222")
        self.assertEqual(item["failures"], ["sub/FAIL-001"])
        self.assertIn("failures: sub/FAIL-001", og.render_findings_tree(listing))
        graph_data = og.load_graph(path)
        self.assertEqual([n["id"] for n in og.find_nodes(graph_data, "FAIL-001")], ["failure:FAIL-001"])          # an exact name wins
        self.assertEqual([n["id"] for n in og.find_nodes(graph_data, "sub/FAIL-001")], ["failure:sub/FAIL-001"])
        self.assertEqual({n["id"] for n in og.find_nodes(graph_data, "fail-001")}, {"failure:FAIL-001", "failure:sub/FAIL-001"})  # a loose search finds both
        self.assertEqual(og.show(path, "sub/FAIL-001")["node"]["id"], "failure:sub/FAIL-001")
        self.assertEqual(og.why(path, "FAIL-001")["node"]["id"], "failure:FAIL-001")   # the bare id resolves to the root's entry, the exact name
        cli = subprocess.run([sys.executable, str(ROOT / "make_ai.py"), "graph", "why", "--graph", str(path), "sub/FAIL-001"],
                             cwd=self.root, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertIn("sub/FAIL-001 (failure)", cli.stdout)
        self.assertIn("[workspace sub]", cli.stdout)

    def test_the_page_carries_the_workspace_for_the_viewer(self) -> None:
        path = self.build()
        page = og.build_view_html(path, view="findings")
        self.assertTrue(page["ok"])
        data = json.loads(re.search(r'id="omni-data" type="application/json">(.*?)</script>', page["html"], re.S).group(1))
        failure = next(n for n in data["nodes"] if n["id"] == "failure:sub/FAIL-001")
        self.assertEqual((failure["name"], failure["attrs"]["workspace"]), ("sub/FAIL-001", "sub"))


if __name__ == "__main__":
    unittest.main()
