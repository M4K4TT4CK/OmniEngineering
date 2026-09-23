"""The graph must build sensibly for any project, not just an OmniEngineering workspace.

Each test builds a small, differently shaped project (no `.ai/`, no git, foreign
requirement ids, another changelog format, broken JSON, ...) and checks that the
layers degrade to a clear note instead of an exception or a silent nothing.
Stdlib only; no tree-sitter is needed (the SQL language needs no grammar).
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


def git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


def build(root: Path, layers: set[str] | None = None) -> og.Graph:
    graph, _ = og.build_graph(root, ["sql"], layers=layers)
    return graph


class Project(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def notes(self, graph: og.Graph) -> str:
        return "\n".join(graph.notes)

    def edges(self, graph: og.Graph, edge_type: str) -> set[tuple[str, str]]:
        return {(e.source, e.target) for e in graph.edges if e.type == edge_type}


class TestEmptyAndBareProjects(Project):
    def test_an_empty_directory_builds_and_explains_what_is_missing(self) -> None:
        graph = build(self.root)
        data = graph.to_json(str(self.root), [], {})
        self.assertEqual(data["nodes"], [])
        text = self.notes(graph)
        self.assertIn("no requirements found", text)
        self.assertIn("no changelog found", text)
        self.assertIn("not a git repository", text)

    def test_a_project_with_only_a_changelog_still_gets_entries(self) -> None:
        write(self.root / "CHANGELOG.md", "# Changelog\n\n## [1.2.0] - 2024-03-05\n\n- Added things\n\n## [1.1.0] - 2024-01-10\n\n- Older\n")
        graph = build(self.root)
        names = sorted(n.name for n in graph.nodes.values() if n.kind == "changelog")
        self.assertEqual(names, ["v1.1.0 (2024-01-10)", "v1.2.0 (2024-03-05)"])
        self.assertEqual({n.attrs["date"] for n in graph.nodes.values() if n.kind == "changelog"}, {"2024-01-10", "2024-03-05"})

    def test_layers_can_be_switched_off(self) -> None:
        write(self.root / "CHANGELOG.md", "## 2024-01-01\n\nx\n")
        graph = build(self.root, {og.LAYER_CODE})
        self.assertFalse([n for n in graph.nodes.values() if n.kind == "changelog"])


class TestForeignConventions(Project):
    def test_any_requirement_id_format_and_a_bare_list_registry(self) -> None:
        write(self.root / "docs/reqs.json", json.dumps([
            {"key": "PROJ-12", "summary": "Export CSV", "state": "done", "files": ["src/export.py"]},
            {"key": "PROJ-1", "summary": "Login", "scope": "src/login.py"},
            {"number": 7, "name": "Numeric id"},
            "not an object", {"title": "no id at all"},
        ]))
        write(self.root / ".ai/graph-config.json", json.dumps({"requirements_files": ["docs/reqs.json"]}))
        write(self.root / "src/export.py", "x = 1\n")
        write(self.root / "CHANGELOG.md", "## 2024-05-01\n\nShipped PROJ-12, not PROJ-123 or PROJ-1-beta.\n")
        graph = build(self.root)
        reqs = {n.name: n for n in graph.nodes.values() if n.kind == "requirement"}
        self.assertEqual(set(reqs), {"PROJ-12", "PROJ-1", "7"})
        self.assertEqual(reqs["PROJ-12"].attrs["status"], "done")
        records = {t for _s, t in self.edges(graph, "records")}
        self.assertEqual(records, {"req:PROJ-12"})  # PROJ-1 must not match inside PROJ-12 / PROJ-123 / PROJ-1-beta

    def test_a_custom_id_pattern_can_be_supplied(self) -> None:
        write(self.root / "reqs.json", json.dumps({"requirements": [{"id": "#42", "title": "t"}]}))
        write(self.root / ".ai/graph-config.json", json.dumps({"requirements_files": ["reqs.json"], "requirement_id_pattern": r"#\d+"}))
        write(self.root / "CHANGELOG.md", "## 2024-05-01\n\nFixed #42 today.\n")
        graph = build(self.root)
        self.assertIn(("changelog:2024-05-01", "req:#42"), self.edges(graph, "records"))

    def test_changelog_in_a_configured_location_and_format(self) -> None:
        write(self.root / "docs/HISTORY.md", "# History\n\n## v2.0.0 (2025-02-03)\n\nBig.\n\n### Notes\n\nsub-heading is body\n")
        write(self.root / ".ai/graph-config.json", json.dumps({"changelog_files": ["docs/HISTORY.md"]}))
        graph = build(self.root)
        entries = [n for n in graph.nodes.values() if n.kind == "changelog"]
        self.assertEqual([n.name for n in entries], ["v2.0.0 (2025-02-03)"])
        self.assertEqual(entries[0].file, "docs/HISTORY.md")

    def test_duplicate_requirement_ids_are_ignored_with_a_note(self) -> None:
        write(self.root / ".ai/requirements/requirements.json", json.dumps({"requirements": [{"id": "REQ-001", "title": "a"}, {"id": "REQ-001", "title": "b"}]}))
        graph = build(self.root)
        self.assertEqual(len([n for n in graph.nodes.values() if n.kind == "requirement"]), 1)
        self.assertIn("duplicate id", self.notes(graph))


class TestChangelogFormats(unittest.TestCase):
    def test_formats(self) -> None:
        keep = og.parse_changelog("# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2020-01-02\n\nx\n")
        self.assertEqual([e["version"] for e in keep], [None, "1.0.0"])
        self.assertEqual(keep[1]["date"], "2020-01-02")
        h1 = og.parse_changelog("# 2024-01-01\n\nfirst\n\n# 2024-02-02\n\nsecond\n")
        self.assertEqual([e["date"] for e in h1], ["2024-01-01", "2024-02-02"])
        undated = og.parse_changelog("# Changelog\n\n## Added widgets\n\n## Fixed bugs\n")
        self.assertEqual(len(undated), 2)
        self.assertEqual(og.parse_changelog("no headings here\njust text\n"), [])
        self.assertEqual(og.parse_changelog(""), [])
        slashes = og.parse_changelog("## 2024/03/04 - release\n")
        self.assertEqual(slashes[0]["date"], "2024-03-04")


class TestBrokenInputs(Project):
    def test_malformed_json_is_reported_and_other_layers_still_build(self) -> None:
        write(self.root / ".ai/requirements/requirements.json", "{ this is not json")
        write(self.root / ".ai/failures/failure-ledger.json", '{"failures": [')
        write(self.root / ".ai/test-suites.json", "[1, 2")
        write(self.root / ".ai/graph-config.json", "nope")
        write(self.root / "CHANGELOG.md", "## 2024-01-01\n\nstill works\n")
        graph = build(self.root)
        text = self.notes(graph)
        for name in ("requirements.json", "failure-ledger.json", "graph-config.json"):
            self.assertIn(name, text)
            self.assertIn("not valid JSON", text)
        self.assertEqual(len([n for n in graph.nodes.values() if n.kind == "changelog"]), 1)

    def test_wrongly_typed_config_values_fall_back_with_a_message(self) -> None:
        write(self.root / ".ai/graph-config.json", json.dumps({"changelog_files": 5, "max_commits": "many", "bogus": 1, "test_globs": ["e2e/*"]}))
        problems: list[str] = []
        cfg = og.graph_config(self.root, problems)
        text = "\n".join(problems)
        self.assertIn("changelog_files", text)
        self.assertIn("max_commits", text)
        self.assertIn("unknown key 'bogus'", text)
        self.assertEqual(cfg["max_commits"], og.DEFAULT_MAX_COMMITS)
        self.assertEqual(cfg["test_globs"], ["e2e/*"])

    def test_a_single_string_is_accepted_where_a_list_is_expected(self) -> None:
        write(self.root / ".ai/graph-config.json", json.dumps({"changelog_files": "docs/CHANGES.md"}))
        self.assertEqual(og.graph_config(self.root)["changelog_files"], ["docs/CHANGES.md"])

    def test_a_json_file_with_a_byte_order_mark(self) -> None:
        (self.root / ".ai/requirements").mkdir(parents=True)
        (self.root / ".ai/requirements/requirements.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"requirements": [{"id": "REQ-001", "title": "t"}]}).encode())
        graph = build(self.root)
        self.assertEqual(len([n for n in graph.nodes.values() if n.kind == "requirement"]), 1)

    def test_a_ledger_and_suite_file_that_are_the_wrong_shape(self) -> None:
        write(self.root / ".ai/failures/failure-ledger.json", "[]")
        write(self.root / ".ai/test-suites.json", '{"suites": "oops"}')
        graph = build(self.root)  # must not raise
        self.assertIn("must be a JSON object", self.notes(graph))


class TestGitShapes(Project):
    def init(self) -> None:
        git(self.root, "init", "-q")

    def commit(self, message: str) -> None:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)

    def test_unicode_and_spaced_paths(self) -> None:
        self.init()
        write(self.root / "docs/résumé plan.md", "x\n")
        write(self.root / ".ai/requirements/requirements.json", json.dumps({"requirements": [{"id": "REQ-001", "title": "t"}]}))
        self.commit("Add plan (REQ-001)")
        graph = build(self.root)
        self.assertIn("file:docs/résumé plan.md", graph.nodes)
        self.assertIn(("commit:" + next(n.id for n in graph.nodes.values() if n.kind == "commit")[7:], "file:docs/résumé plan.md"), self.edges(graph, "modifies"))

    def test_a_project_that_is_a_subfolder_of_a_larger_repository(self) -> None:
        self.init()
        write(self.root / "app/src/mod.sql", "CREATE TABLE t (id int);\n")
        write(self.root / "app/.ai/requirements/requirements.json", json.dumps({"requirements": [{"id": "REQ-001", "title": "t", "scope": ["src"]}]}))
        write(self.root / "other/unrelated.md", "x\n")
        self.commit("Add table (REQ-001)")
        graph = build(self.root / "app")
        commit = next(n for n in graph.nodes.values() if n.kind == "commit")
        self.assertEqual(commit.attrs["modules_touched"], 1)  # only files under app/, named relative to it
        self.assertNotIn("file:other/unrelated.md", graph.nodes)

    def test_history_limit_comes_from_the_config(self) -> None:
        self.init()
        for number in range(3):
            write(self.root / f"f{number}.md", str(number))
            self.commit(f"c{number}")
        write(self.root / ".ai/graph-config.json", json.dumps({"max_commits": 2}))
        graph, _ = og.build_graph(self.root, ["sql"])
        self.assertEqual(len([n for n in graph.nodes.values() if n.kind == "commit"]), 2)
        self.assertIn("only the newest 2 commits", self.notes(graph))

    def test_a_commit_message_naming_no_requirement_does_not_invent_one(self) -> None:
        self.init()
        write(self.root / ".ai/requirements/requirements.json", json.dumps({"requirements": [{"id": "REQ-001", "title": "t"}]}))
        write(self.root / "a.md", "x")
        self.commit("Refactor: nothing to see")
        graph = build(self.root)
        self.assertFalse(self.edges(graph, "delivers"))


class TestFilesystemHazards(Project):
    def test_a_symlink_loop_does_not_hang_discovery(self) -> None:
        write(self.root / "a.sql", "CREATE TABLE t (id int);\n")
        try:
            os.symlink(self.root, self.root / "loop", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not permitted here")
        found = og.discover_source_files(self.root, ["sql"])
        self.assertEqual([p.name for p, _ in found], ["a.sql"])


class TestTestSuites(Project):
    def files(self) -> list[str]:
        return sorted(p.relative_to(self.root).as_posix() for p, _ in og.discover_source_files(self.root, sorted(og.language_extensions())))

    def test_detects_a_maven_and_a_vitest_project_with_their_ci_commands(self) -> None:
        write(self.root / "backend/pom.xml", "<project/>")
        write(self.root / "backend/src/main/java/A.java", "// @WebMvcTest is only mentioned in this comment\nclass A {}\n")
        write(self.root / "backend/src/test/java/ATest.java", "import org.junit.jupiter.api.Test;\nclass ATest { @Test void a() {} }\n")
        write(self.root / "web/package.json", json.dumps({"devDependencies": {"vitest": "^1"}, "scripts": {"test": "vitest run"}}))
        write(self.root / "web/src/x.test.ts", "import { it } from 'vitest';\nit('x', () => {});\n")
        write(self.root / "web/src/x.ts", "export const x = 1;\n")
        write(self.root / ".github/workflows/ci.yml", "jobs:\n  t:\n    steps:\n      - name: web tests\n        working-directory: web\n        run: npm test -- --run\n      - run: cd backend && mvn test -B\n")
        suites = {s["id"]: s for s in og.detect_test_suites(self.root, self.files())}
        self.assertEqual(set(suites), {"backend-junit", "web-vitest"})
        self.assertEqual(suites["backend-junit"]["files"], ["backend/src/test/java/ATest.java"])  # not A.java, whose comment mentions a test annotation
        self.assertEqual(suites["backend-junit"]["command"], "cd backend && mvn test")
        self.assertEqual(suites["web-vitest"]["command"], "cd web && npm test -- --run")
        self.assertEqual(suites["web-vitest"]["paths"], ["web/src/x.test.ts"])  # web/src also holds non-test code, so it is not claimed whole

    def test_a_manual_suite_claims_files_no_convention_would_find_and_wins_over_detection(self) -> None:
        write(self.root / "checks/smoke_login.py", "print('ok')\n")
        write(self.root / "tests/test_a.py", "import unittest\nclass T(unittest.TestCase): pass\n")
        write(self.root / ".ai/test-suites.json", json.dumps({"version": "1", "suites": [
            {"id": "smoke", "name": "Smoke", "kind": "smoke", "framework": "script", "paths": ["checks"], "command": "python checks/smoke_login.py", "covers": ["src"]},
            {"id": "everything", "paths": ["tests", "gone/"]},
        ]}))
        resolved = og.resolve_test_suites(self.root, self.files())
        by_id = {s["id"]: s for s in resolved["suites"]}
        self.assertEqual(by_id["smoke"]["files"], ["checks/smoke_login.py"])
        self.assertEqual(by_id["everything"]["files"], ["tests/test_a.py"])
        self.assertEqual(resolved["auto"], [])  # everything detected is already claimed by a registered suite
        self.assertEqual(resolved["missing_paths"], {"everything": ["gone/"]})

    def test_suites_become_graph_nodes_that_group_tests_and_cover_code(self) -> None:
        write(self.root / "checks/smoke_login.py", "print('ok')\n")
        write(self.root / "src/login.py", "x = 1\n")
        write(self.root / ".ai/test-suites.json", json.dumps({"suites": [{"id": "smoke", "paths": ["checks/smoke_login.py"], "kind": "smoke", "command": "python checks/smoke_login.py", "covers": ["src"]}]}))
        graph = og.Graph()
        for rel in ("checks/smoke_login.py", "src/login.py"):
            graph.add_node(og.GraphNode(rel, "module", Path(rel).stem, rel, rel, 1, 1, "python"))
        stats = og.add_assurance_layer(graph, self.root)
        self.assertEqual((stats["suites"], stats["suites_manual"]), (1, 1))
        suite = graph.nodes["suite:smoke"]
        self.assertEqual((suite.kind, suite.layer, suite.attrs["command"]), ("suite", og.LAYER_ASSURANCE, "python checks/smoke_login.py"))
        self.assertEqual(graph.nodes["checks/smoke_login.py"].layer, og.LAYER_ASSURANCE)  # a test although its name says otherwise
        self.assertEqual(graph.nodes["checks/smoke_login.py"].attrs["suite"], "smoke")
        edges = {(e.source, e.target, e.type, e.provenance) for e in graph.edges}
        self.assertIn(("suite:smoke", "checks/smoke_login.py", "contains", "EXTRACTED"), edges)
        self.assertIn(("suite:smoke", "src/login.py", "covers", "EXTRACTED"), edges)

    def test_config_globs_add_and_exclude_test_files(self) -> None:
        write(self.root / "qa/scenario_1.py", "x\n")
        write(self.root / "tests/test_fixture_data.py", "x\n")
        write(self.root / ".ai/graph-config.json", json.dumps({"test_globs": ["qa/*"], "exclude_test_globs": ["tests/test_fixture_data.py"]}))
        graph = og.Graph()
        for rel in ("qa/scenario_1.py", "tests/test_fixture_data.py"):
            graph.add_node(og.GraphNode(rel, "module", Path(rel).stem, rel, rel, 1, 1, "python"))
        og.add_assurance_layer(graph, self.root)
        self.assertEqual(graph.nodes["qa/scenario_1.py"].layer, og.LAYER_ASSURANCE)
        self.assertEqual(graph.nodes["tests/test_fixture_data.py"].layer, og.LAYER_CODE)

    def test_a_ledger_entry_can_name_a_suite_as_its_regression_test(self) -> None:
        write(self.root / "checks/smoke.py", "x\n")
        write(self.root / ".ai/test-suites.json", json.dumps({"suites": [{"id": "smoke", "paths": ["checks"]}]}))
        write(self.root / ".ai/failures/failure-ledger.json", json.dumps({"failures": [
            {"id": "FAIL-001", "title": "t", "status": "open", "symptom": "s", "regression_tests": ["smoke"]}]}))
        graph = og.Graph()
        graph.add_node(og.GraphNode("checks/smoke.py", "module", "smoke", "checks/smoke.py", "checks/smoke.py", 1, 1, "python"))
        og.add_assurance_layer(graph, self.root)
        self.assertIn(("suite:smoke", "failure:FAIL-001"), {(e.source, e.target) for e in graph.edges if e.type == "guards"})

    def test_a_project_with_no_tests_says_so(self) -> None:
        write(self.root / "src/a.sql", "CREATE TABLE t (id int);\n")
        report = og.describe_sources(self.root)
        self.assertTrue(any("No tests found" in hint for hint in report["hints"]))


class TestProjectFocus(Project):
    """The assurance layer is about the project OmniEngineering is embedded in, not about OmniEngineering."""

    def graph_with(self, *relpaths: str) -> og.Graph:
        graph = og.Graph()
        for rel in relpaths:
            write(self.root / rel, "x = 1\n")
            graph.add_node(og.GraphNode(rel, "module", Path(rel).stem, rel, rel, 1, 1, "python"))
            fn = og.GraphNode(f"{rel}::f", "function", "f", f"{Path(rel).stem}.f", rel, 1, 1, "python")
            graph.add_node(fn)
            graph.add_edge(rel, fn.id, "defines", "EXTRACTED", "syntax")
        return graph

    def test_omniengineering_code_moves_to_the_workspace_layer(self) -> None:
        graph = self.graph_with("make_ai.py", "omni_graph.py", "app/service.py")
        stats = og.add_workspace_layer(graph, self.root)
        self.assertEqual(stats["tooling_nodes"], 4)  # two modules and their functions
        self.assertEqual(graph.nodes["make_ai.py"].layer, og.LAYER_WORKSPACE)
        self.assertTrue(graph.nodes["omni_graph.py::f"].attrs["tooling"])
        self.assertEqual(graph.nodes["app/service.py"].layer, og.LAYER_CODE)

    def test_tooling_is_never_a_test_a_suite_or_a_coverage_target(self) -> None:
        write(self.root / "tests/test_service.py", "import unittest\nclass T(unittest.TestCase): pass\n")
        graph = self.graph_with("make_ai.py", "app/service.py", "tests/test_service.py")
        graph.add_edge("tests/test_service.py::f", "make_ai.py::f", "calls", "EXTRACTED", "syntax")
        graph.add_edge("tests/test_service.py::f", "app/service.py::f", "calls", "EXTRACTED", "syntax")
        og.add_workspace_layer(graph, self.root)
        og.add_assurance_layer(graph, self.root)
        verified = {t for s, t in ((e.source, e.target) for e in graph.edges if e.type == "verifies")}
        self.assertEqual(verified, {"app/service.py::f"})  # the test exercises the project, not the omni CLI
        self.assertEqual(graph.nodes["make_ai.py"].layer, og.LAYER_WORKSPACE)

    def test_detection_skips_tooling_paths(self) -> None:
        write(self.root / "tests/test_omni_thing.py", "import unittest\nclass T(unittest.TestCase): pass\n")
        write(self.root / ".ai/scripts/test_hook.py", "import unittest\nclass T(unittest.TestCase): pass\n")
        files = ["tests/test_omni_thing.py", ".ai/scripts/test_hook.py"]
        suites = og.detect_test_suites(self.root, files)
        self.assertEqual([f for s in suites for f in s["files"]], ["tests/test_omni_thing.py"])

    def test_omniengineerings_own_repository_can_declare_itself_the_project(self) -> None:
        write(self.root / ".ai/graph-config.json", json.dumps({"tooling_paths": []}))
        write(self.root / "tests/test_cli.py", "import unittest\nclass T(unittest.TestCase): pass\n")
        graph = self.graph_with("make_ai.py", "tests/test_cli.py")
        graph.add_edge("tests/test_cli.py::f", "make_ai.py::f", "calls", "EXTRACTED", "syntax")
        og.add_workspace_layer(graph, self.root)
        og.add_assurance_layer(graph, self.root)
        self.assertEqual(graph.nodes["make_ai.py"].layer, og.LAYER_CODE)
        self.assertIn(("tests/test_cli.py", "make_ai.py::f"), {(e.source, e.target) for e in graph.edges if e.type == "verifies"})

    def test_an_untested_tooling_file_is_not_reported_as_a_gap(self) -> None:
        graph = self.graph_with("make_ai.py", "app/service.py")
        og.add_workspace_layer(graph, self.root)
        path = self.root / "g.json"
        path.write_text(json.dumps(graph.to_json(".", ["python"], {})), encoding="utf-8")
        self.assertNotIn("gaps", og.why(path, "make_ai.py")["sections"])
        self.assertIn("gaps", og.why(path, "app/service.py")["sections"])


class TestSourcesReport(Project):
    def test_report_and_draft_config_for_an_unusual_layout(self) -> None:
        write(self.root / "docs/CHANGELOG.md", "## 2024-01-01\n\nx\n")
        write(self.root / "docs/requirements/requirements.json", json.dumps({"requirements": [{"id": "A-1", "title": "t"}]}))
        report = og.describe_sources(self.root)
        self.assertEqual(report["layers"]["governance"]["changelog"][0]["path"], "docs/CHANGELOG.md")
        self.assertTrue(any("No requirement registry found" in hint for hint in report["hints"]))
        draft = og.draft_graph_config(self.root)
        self.assertEqual(draft["requirements_files"], ["docs/requirements/requirements.json"])

    def test_the_cli_reports_and_writes_a_draft(self) -> None:
        write(self.root / "docs/requirements/requirements.json", json.dumps({"requirements": [{"id": "A-1", "title": "t"}]}))
        result = subprocess.run([sys.executable, str(ROOT / "make_ai.py"), "graph", "sources", "--write"], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Graph sources for", result.stdout)
        config = json.loads((self.root / ".ai/graph-config.json").read_text())
        self.assertEqual(config["requirements_files"], ["docs/requirements/requirements.json"])
        again = subprocess.run([sys.executable, str(ROOT / "make_ai.py"), "graph", "sources", "--write"], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(again.returncode, 1)  # refuses to overwrite


class TestTestCli(Project):
    def run_omni(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ROOT / "make_ai.py"), *args], cwd=self.root, capture_output=True, text=True)

    def test_detect_write_add_check_remove(self) -> None:
        write(self.root / "tests/test_a.py", "import unittest\nclass T(unittest.TestCase): pass\n")
        write(self.root / "checks/smoke_a.py", "print(1)\n")
        detected = self.run_omni("test", "detect", "--write")
        self.assertEqual(detected.returncode, 0, detected.stderr)
        config = json.loads((self.root / ".ai/test-suites.json").read_text())
        self.assertEqual([s["id"] for s in config["suites"]], ["root-unittest"])
        bad = self.run_omni("test", "add", "--name", "Smoke", "--paths", "nowhere/")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("match no source file", bad.stderr)
        ok = self.run_omni("test", "add", "--name", "Smoke", "--paths", "checks", "--kind", "smoke", "--command", "python checks/smoke_a.py")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.run_omni("test", "add", "--name", "Smoke", "--paths", "checks").returncode, 1)  # duplicate id
        self.assertEqual(self.run_omni("test", "check").returncode, 0)
        self.assertEqual(self.run_omni("test", "remove", "smoke").returncode, 0)
        self.assertEqual(self.run_omni("test", "remove", "smoke").returncode, 1)

    def test_a_configured_registry_location_is_honoured(self) -> None:
        write(self.root / ".ai/graph-config.json", json.dumps({"test_suites_file": "qa/suites.json"}))
        write(self.root / "tests/test_a.py", "import unittest\nclass T(unittest.TestCase): pass\n")
        self.assertEqual(self.run_omni("test", "detect", "--write").returncode, 0)
        self.assertTrue((self.root / "qa/suites.json").is_file())
        self.assertFalse((self.root / ".ai/test-suites.json").exists())


if __name__ == "__main__":
    unittest.main()
