"""ARB-064: every Arbiter finding is traceable through OmniGraph.

A tiny git repository plus two synthetic Arbiter reports (a full scan and a
newer partial gate scan) are built in tmp_path; the graph is built over them
and the queries `omni graph findings/why/impact`, the MCP `graph_cwe` tool and
`omni graph build --if-stale` are asserted on the result. These tests run
without a tree-sitter grammar (the findings then attach to `file` nodes); the
one symbol-span assertion skips when the python grammar is absent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import contextlib
import io
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # omni_graph.py, make_ai.py and omni_mcp.py live at the repository root

import make_ai as ma  # noqa: E402
import omni_graph as og  # noqa: E402
import omni_mcp  # noqa: E402

try:
    og.load_language("python")
    HAVE_PYTHON_GRAMMAR = True
except og.GraphDependencyError:
    HAVE_PYTHON_GRAMMAR = False

SOURCE = "src/app.py" if HAVE_PYTHON_GRAMMAR else "src/app.txt"
APP_SOURCE = '''import sqlite3


class Store:
    def __init__(self):
        self.tasks = []

    def take(self):
        if self.tasks:
            return self.tasks.pop()
        return None


def query(db, name):
    return db.execute("select * from t where name = '" + name + "'")
'''


def _finding(fid: str, rule: str, line: int, cwe: list[str], status: str = "new", **extra) -> dict:
    item = {
        "id": fid, "rule_id": rule, "title": f"{rule} at line {line}", "dimension": extra.pop("dimension", "quality"),
        "severity": extra.pop("severity", "medium"), "confidence": "high", "provenance": "deterministic", "repo_id": "root",
        "location": {"path": extra.pop("path", "src/" + Path(SOURCE).name), "start_line": line, "end_line": 0, "repo_id": "root"},
        "probe": extra.pop("probe", rule.split("/")[1].split(".")[0]), "controls": extra.pop("controls", ["NIST-800-218:PW.5"]),
        "cwe": cwe, "tags": extra.pop("tags", []), "status": status, "suppressed": False,
    }
    item.update(extra)
    return item


FULL_FINDINGS = [
    _finding("f:aaa000000001", "arbiter/concurrency.check-then-act-unlocked", 9, ["CWE-362"], tags=["req:REQ-001", "race"]),
    _finding("f:bbb000000002", "arbiter/sql.string-concat", 15, ["CWE-89"], dimension="security", severity="high"),
    _finding("f:ddd000000004", "arbiter/ast.function-too-long", 1, ["CWE-1080"], severity="low"),
]
GATE_FINDINGS = [
    _finding("f:bbb000000002", "arbiter/sql.string-concat", 15, ["CWE-89"], status="existing", dimension="security", severity="high"),
    _finding("f:ccc000000003", "arbiter/supply.unpinned-dependency", 0, ["CWE-1357", "CWE-494"], path="requirements.txt", dimension="supply_chain"),
]


def _git(root: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, env=env).stdout.strip()


def _write_report(root: Path, rel: str, findings: list[dict], mode: str, repo_path: Path | None = None) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    commit = _git(root, "rev-parse", "--short", "HEAD")
    path.write_text(json.dumps({
        "schema_version": 2, "scan_scope": {"mode": mode, **({"changed_since": "HEAD~1"} if mode == "partial" else {})},
        "repos": [{"id": "root", "path": str(repo_path or root), "source": str(repo_path or root), "commit": commit, "files": 2, "loc": 20}],
        "findings": findings,
    }, indent=1), encoding="utf-8")
    return path


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / SOURCE).write_text(APP_SOURCE, encoding="utf-8")
    (root / "requirements.txt").write_text("requests\n", encoding="utf-8")
    (root / ".gitignore").write_text("arbiter-out/\n.ai/project-graph.json\n", encoding="utf-8")
    (root / ".ai" / "requirements").mkdir(parents=True)
    (root / ".ai" / "failures").mkdir(parents=True)
    (root / ".ai" / "requirements" / "requirements.json").write_text(json.dumps({
        "version": "3.0.0", "requirements": [
            {"id": "REQ-001", "title": "Workers take tasks safely", "status": "in_progress", "category": "feature", "scope": ["src/"]},
        ],
    }), encoding="utf-8")
    (root / ".ai" / "failures" / "failure-ledger.json").write_text(json.dumps({"failures": [
        {"id": "FAIL-001", "title": "Race in Store.take", "status": "open", "severity": "medium", "date": "2026-10-20",
         "symptom": "two workers took the same task", "how_detected": "arbiter finding f:aaa000000001", "requirement": "REQ-001", "affected": ["src/"]},
    ]}), encoding="utf-8")
    (root / ".ai" / "graph-config.json").write_text(json.dumps({"tooling_paths": [], "max_commits": 50}), encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "REQ-001: initial store")
    return root


def build(root: Path) -> tuple[og.Graph, Path]:
    graph, info = og.build_graph(root, ["auto"])
    out = root / og.GRAPH_DEFAULT_OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph.to_json(str(root), ["auto"], info)), encoding="utf-8")
    return graph, out



def _edges(graph: og.Graph, source: str, edge_type: str) -> set[str]:
    return {e.target for e in graph.edges if e.source == source and e.type == edge_type}


def _make_reports(root: Path) -> None:
    full = _write_report(root, "arbiter-out/full/report.json", FULL_FINDINGS, "full")
    gate = _write_report(root, "arbiter-out/omni-gate/report.json", GATE_FINDINGS, "partial")
    # the gate ran after the full scan: make the mtimes say so regardless of filesystem resolution
    older = time.time() - 60
    os.utime(full, (older, older))
    os.utime(gate, None)


def _build_if_stale() -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = ma.main(["graph", "build", "--if-stale"])
    return rc, buf.getvalue()


class TestGraphFindings(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_path = Path(tmp.name)
        self.repo = make_repo(self.tmp_path)
        _make_reports(self.repo)
        og._CONFIG_CACHE.clear()
        self.addCleanup(og._CONFIG_CACHE.clear)

    def chdir_repo(self) -> None:
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)


    # --------------------------------------------------------------------------
    # nodes and edges
    # --------------------------------------------------------------------------

    def test_finding_violates_rule_and_is_classified_as_cwe(self) -> None:
        repo = self.repo
        graph, _ = build(repo)
        finding = graph.nodes["finding:f:aaa000000001"]
        assert finding.attrs["cwe"] == ["CWE-362"]
        assert finding.attrs["controls"] == ["NIST-800-218:PW.5"]
        assert finding.attrs["probe"] == "concurrency"
        assert _edges(graph, finding.id, "violates") == {"arbiter_rule:arbiter/concurrency.check-then-act-unlocked"}
        assert _edges(graph, finding.id, "classified_as") == {"cwe:CWE-362"}
        rule = graph.nodes["arbiter_rule:arbiter/concurrency.check-then-act-unlocked"]
        assert rule.layer == og.LAYER_ASSURANCE and rule.attrs["probe"] == "concurrency" and rule.attrs["title"]
        assert _edges(graph, rule.id, "classified_as") == {"cwe:CWE-362"}, "the rule carries its findings' classification"
        cwe = graph.nodes["cwe:CWE-362"]
        assert cwe.layer == og.LAYER_ASSURANCE and "Race Condition" in cwe.attrs["name"]
        assert graph.nodes["cwe:CWE-1357"].attrs["name"], "a CWE Arbiter uses is named"
        assert og.NODE_KIND_LAYER["cwe"] == og.LAYER_ASSURANCE and og.EDGE_LAYER["violates"] == og.LAYER_ASSURANCE
        stats = graph.layer_stats[og.LAYER_ASSURANCE]
        assert stats["findings"] == 4 and stats["arbiter_rules"] == 4 and stats["cwes"] == 5
        assert stats["reports"] == ["arbiter-out/full/report.json", "arbiter-out/omni-gate/report.json"]

    def test_finding_cites_requirement_flags_file_and_is_recorded_as_failure(self) -> None:
        repo = self.repo
        graph, _ = build(repo)
        fid = "finding:f:aaa000000001"
        cited = _edges(graph, fid, "cites")
        assert [(graph.nodes[t].kind, graph.nodes[t].name) for t in cited] == [("requirement", "REQ-001")]
        assert _edges(graph, fid, "recorded_as") == {"failure:FAIL-001"}
        flagged = _edges(graph, fid, "flags")
        file_targets = {t for t in flagged if graph.nodes[t].kind in ("file", "module")}
        assert len(file_targets) == 1 and graph.nodes[next(iter(file_targets))].file == SOURCE
        assert "file:src" in graph.nodes, "the directory chain is made explicit for the viewer tree"
        assert graph.nodes[fid].attrs["introduced_by"] != "uncommitted"
        assert _edges(graph, fid, "introduced_by"), "git blame ties the line to the commit that wrote it"


    @unittest.skipUnless(HAVE_PYTHON_GRAMMAR, "needs a tree-sitter python grammar")
    def test_finding_flags_the_enclosing_symbol(self) -> None:
        repo = self.repo
        graph, _ = build(repo)
        symbols = {graph.nodes[t].name for t in _edges(graph, "finding:f:aaa000000001", "flags") if graph.nodes[t].kind in og._FINDING_SYMBOL_KINDS}
        assert symbols == {"take"}
        symbols = {graph.nodes[t].name for t in _edges(graph, "finding:f:bbb000000002", "flags") if graph.nodes[t].kind in og._FINDING_SYMBOL_KINDS}
        assert symbols == {"query"}

    def test_start_line_zero_still_attaches_to_the_file(self) -> None:
        repo = self.repo
        graph, _ = build(repo)
        node = graph.nodes["finding:f:ccc000000003"]
        assert node.start_line is None and node.attrs["line"] is None
        assert _edges(graph, node.id, "flags") == {"file:requirements.txt"}
        assert "introduced_by" not in node.attrs and not _edges(graph, node.id, "introduced_by"), "no line: no blame claim, and no error"
        assert _edges(graph, node.id, "classified_as") == {"cwe:CWE-1357", "cwe:CWE-494"}

    # --------------------------------------------------------------------------
    # two reports merged
    # --------------------------------------------------------------------------

    def test_full_and_gate_reports_merge_with_the_newer_gate_winning_on_status(self) -> None:
        repo = self.repo
        reports = og.find_findings_reports(repo)
        assert [r.relative_to(repo).as_posix() for r in reports] == ["arbiter-out/full/report.json", "arbiter-out/omni-gate/report.json"]
        graph, _ = build(repo)
        both = graph.nodes["finding:f:bbb000000002"].attrs
        assert both["status"] == "existing", "the gate report re-scanned the file and knows the status"
        assert both["sources"] == ["arbiter-out/full/report.json", "arbiter-out/omni-gate/report.json"]
        assert graph.nodes["finding:f:ddd000000004"].attrs["sources"] == ["arbiter-out/full/report.json"]
        assert graph.nodes["finding:f:ccc000000003"].attrs["sources"] == ["arbiter-out/omni-gate/report.json"]
        assert sum(1 for n in graph.nodes.values() if n.kind == "finding") == 4, "deduplicated by id"

    def test_an_older_gate_report_does_not_override_a_newer_full_scan(self) -> None:
        repo = self.repo
        gate = repo / "arbiter-out/omni-gate/report.json"
        older = time.time() - 600
        os.utime(gate, (older, older))
        os.utime(repo / "arbiter-out/full/report.json", None)
        items = {i["id"]: i for i in og.load_merged_findings(repo, og.find_findings_reports(repo))}
        assert items["f:bbb000000002"]["status"] == "new"
        assert items["f:bbb000000002"]["sources"] == ["arbiter-out/full/report.json", "arbiter-out/omni-gate/report.json"]
        assert "f:ccc000000003" in items, "a finding only the gate holds is still added"

    def test_findings_report_path_setting_reads_only_that_report(self) -> None:
        repo = self.repo
        cfg_path = repo / ".ai" / "graph-config.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["findings_report"] = "arbiter-out/omni-gate/report.json"
        cfg_path.write_text(json.dumps(cfg))
        og._CONFIG_CACHE.clear()
        assert [r.relative_to(repo).as_posix() for r in og.find_findings_reports(repo)] == ["arbiter-out/omni-gate/report.json"]
        graph, _ = build(repo)
        assert {n.name for n in graph.nodes.values() if n.kind == "finding"} == {"f:bbb000000002", "f:ccc000000003"}
        described = og.describe_findings_source(repo)
        assert described["findings"] == 2 and [r["path"] for r in described["reports"]] == ["arbiter-out/omni-gate/report.json"]

    def test_paths_are_prefixed_with_the_scanned_subdirectory(self) -> None:
        tmp_path = self.tmp_path / "own"
        root = make_repo(tmp_path)
        sub = root / "src"
        report = _write_report(root, "arbiter-out/report.json", [_finding("f:eee000000005", "arbiter/x.y", 1, ["CWE-20"], path=Path(SOURCE).name)], "full", repo_path=sub)
        items = og.load_findings(root, report)
        assert items[0]["path"] == SOURCE and items[0]["directory_path"] == "src"

    # --------------------------------------------------------------------------
    # queries
    # --------------------------------------------------------------------------

    def test_findings_query_filters_by_cwe_and_rule_and_shows_the_cwe(self) -> None:
        repo = self.repo
        _, graph_path = build(repo)
        result = og.findings(graph_path, cwe="CWE-89")
        assert result["total"] == 1 and result["directories"][0]["findings"][0]["name"] == "f:bbb000000002"
        assert result["by_cwe"] == {"CWE-89": 1} and result["filters"]["cwe"] == "CWE-89"
        assert og.findings(graph_path, cwe="89")["total"] == 1, "a bare number is a CWE id too"
        assert og.findings(graph_path, cwe="CWE-9999")["total"] == 0
        by_rule = og.findings(graph_path, rule="arbiter/supply.*")
        assert [f["name"] for d in by_rule["directories"] for f in d["findings"]] == ["f:ccc000000003"]
        assert og.findings(graph_path, rule="concurrency")["total"] == 1, "no glob characters means a substring"
        everything = og.findings(graph_path)
        assert everything["total"] == 4 and everything["reports"] == ["arbiter-out/full/report.json", "arbiter-out/omni-gate/report.json"]
        item = next(f for d in everything["directories"] for f in d["findings"] if f["name"] == "f:bbb000000002")
        assert item["cwe"] == ["CWE-89"] and item["controls"] == ["NIST-800-218:PW.5"] and len(item["sources"]) == 2
        tree = og.render_findings_tree(everything)
        assert "[CWE-89]" in tree and "arbiter/sql.string-concat" in tree and "(existing)" in tree
        assert "from: arbiter-out/full/report.json, arbiter-out/omni-gate/report.json" in tree

    def test_why_finding_lists_rule_and_cwe_and_why_cwe_lists_findings(self) -> None:
        repo = self.repo
        _, graph_path = build(repo)
        result = og.why(graph_path, "f:aaa000000001")
        assert result["ok"]
        assert [i["name"] for i in result["sections"]["rule"]] == ["arbiter/concurrency.check-then-act-unlocked"]
        assert [i["name"] for i in result["sections"]["cwe"]] == ["CWE-362"]
        assert [i["name"] for i in result["sections"]["requirement"]] == ["REQ-001"]
        by_cwe = og.why(graph_path, "CWE-89")
        assert by_cwe["ok"] and by_cwe["node"]["kind"] == "cwe"
        assert [i["name"] for i in by_cwe["sections"]["findings"]] == ["f:bbb000000002"]
        assert [i["file"] for i in by_cwe["sections"]["files"]] == [SOURCE]
        assert [i["name"] for i in by_cwe["sections"]["rules"]] == ["arbiter/sql.string-concat"]
        by_rule = og.why(graph_path, "arbiter/sql.string-concat")
        assert by_rule["node"]["kind"] == "arbiter_rule" and [i["name"] for i in by_rule["sections"]["cwe"]] == ["CWE-89"]

    def test_impact_of_a_file_lists_its_findings_and_impact_of_a_cwe_lists_findings_and_files(self) -> None:
        repo = self.repo
        _, graph_path = build(repo)
        by_file = og.impact(graph_path, [SOURCE])
        names = sorted(i["name"] for i in by_file["findings"])
        assert names == ["f:aaa000000001", "f:bbb000000002", "f:ddd000000004"]
        assert all(i["hops"] == 1 and i["via"] == "flags" for i in by_file["findings"])
        assert not any(i["kind"] in ("cwe", "arbiter_rule") for bucket in og.IMPACT_BUCKETS for i in by_file[bucket]), "a file never fans out through a CWE hub"
        assert "3 finding(s)" in og.impact_summary(by_file)
        by_cwe = og.impact(graph_path, ["CWE-89"])
        assert by_cwe["changed"] == ["CWE-89"] and [i["name"] for i in by_cwe["findings"]] == ["f:bbb000000002"]
        assert [i["file"] for i in by_cwe["files"]] == [SOURCE]
        by_rule = og.impact(graph_path, ["arbiter/supply.unpinned-dependency"])
        assert [i["name"] for i in by_rule["findings"]] == ["f:ccc000000003"] and [i["file"] for i in by_rule["files"]] == ["requirements.txt"]
        assert og.impact(graph_path, ["CWE-4242"])["unresolved"] == ["CWE-4242"]

    def test_cwe_report_is_the_mcp_graph_cwe_tool(self) -> None:
        repo = self.repo
        _, graph_path = build(repo)
        report = og.cwe_report(graph_path, "CWE-1357")
        assert report["ok"] and report["name"] == "CWE-1357" and report["total"] == 1
        assert report["findings"][0]["id"] == "finding:f:ccc000000003" and report["files"] == [{"file": "requirements.txt", "findings": 1}]
        assert report["rules"] == [{"rule_id": "arbiter/supply.unpinned-dependency", "findings": 1}]
        assert og.cwe_report(graph_path, "arbiter/sql.string-concat")["cwe"] == ["CWE-89"]
        missing = og.cwe_report(graph_path, "CWE-4242")
        assert not missing["ok"] and "CWE-89" in missing["known"]
        self.chdir_repo()
        via_mcp = omni_mcp.dispatch("graph_cwe", {"id": "89"})
        assert via_mcp["ok"] and [f["name"] for f in via_mcp["findings"]] == ["f:bbb000000002"]
        filtered = omni_mcp.dispatch("graph_findings", {"cwe": "CWE-362", "rule": "arbiter/concurrency.*"})
        assert filtered["total"] == 1
        assert "graph_cwe" in omni_mcp.TOOLS_BY_NAME and "cwe" in omni_mcp.TOOLS_BY_NAME["graph_findings"].input_schema["properties"]
        by_paths = omni_mcp.dispatch("graph_impact", {"paths": ["CWE-89"]})
        assert [i["name"] for i in by_paths["findings"]] == ["f:bbb000000002"]

    # --------------------------------------------------------------------------
    # rebuild only when an input moved
    # --------------------------------------------------------------------------

    def test_build_if_stale_is_a_noop_on_a_second_run(self) -> None:
        repo = self.repo
        self.chdir_repo()
        assert og.graph_stale(Path(og.GRAPH_DEFAULT_OUTPUT), repo) == (True, "no graph file")
        rc, first = _build_if_stale()
        assert rc == 0
        assert "rebuilding" in first and "Wrote" in first
        assert og.graph_stale(Path(og.GRAPH_DEFAULT_OUTPUT), repo) == (False, "")
        rc, out = _build_if_stale()
        assert rc == 0 and "up to date; nothing rebuilt" in out
        # a new gate report is an input change
        stamp = time.time() + 5
        os.utime(repo / "arbiter-out/omni-gate/report.json", (stamp, stamp))
        stale, reason = og.graph_stale(Path(og.GRAPH_DEFAULT_OUTPUT), repo)
        assert stale and reason == "arbiter-out/omni-gate/report.json changed"
        rc, out = _build_if_stale()
        assert rc == 0 and "rebuilding" in out
        # so is a commit
        (repo / "NOTES.md").write_text("x\n")
        _git(repo, "add", "NOTES.md")
        _git(repo, "commit", "-q", "-m", "notes")
        stale, reason = og.graph_stale(Path(og.GRAPH_DEFAULT_OUTPUT), repo)
        assert stale and reason.startswith("HEAD moved")

    def test_gate_rebuild_note_rebuilds_a_stale_graph_and_never_raises(self) -> None:
        repo = self.repo
        self.chdir_repo()
        assert ma.graph_rebuild_if_stale_note() is None, "no graph file: nothing to keep current"
        build(repo)
        assert ma.graph_rebuild_if_stale_note() is None, "fresh graph: nothing to do"
        stamp = time.time() + 5
        os.utime(repo / "arbiter-out/full/report.json", (stamp, stamp))
        note = ma.graph_rebuild_if_stale_note()
        assert note and note.startswith("graph: rebuilt .ai/project-graph.json (arbiter-out/full/report.json changed;") and "4 finding(s)" in note
        assert ma.graph_rebuild_if_stale_note() is None
        os.utime(repo / "arbiter-out/full/report.json", (stamp + 5, stamp + 5))
        with mock.patch.object(og, "build_graph", side_effect=RuntimeError("boom")):
            note = ma.graph_rebuild_if_stale_note()
        assert note and note.startswith("graph: not rebuilt (RuntimeError: boom)")


if __name__ == "__main__":
    unittest.main()
