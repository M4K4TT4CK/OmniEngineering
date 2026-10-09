"""Tests for `omni test run`: run registered suites -- all, by name, or only those the change set impacts.

Stdlib only; suite commands are `python -c ...` one-liners so nothing outside the standard library runs. The graph for
the `--impacted` case is hand-built the way tests/test_lineage.py builds it (no tree-sitter). Run with:

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
from test_graph_layers import module, write  # noqa: E402

PY = f'"{sys.executable}"'
PASS_CMD = f"{PY} -c \"print('line1'); print('line2'); print('line3'); print('line4'); print('line5'); print('line6')\""
FAIL_CMD = f"{PY} -c \"import sys; print('boom'); sys.exit(3)\""
SLOW_CMD = f"{PY} -c \"import time; time.sleep(30)\""


def suite(suite_id: str, paths: list[str], command: str, covers: list[str]) -> dict:
    return {"id": suite_id, "name": suite_id.replace("-", " ").title(), "kind": "unit", "framework": "script",
            "paths": paths, "command": command, "covers": covers}


def ns(**overrides) -> argparse.Namespace:
    values = {"names": [], "impacted": False, "changed": None, "timeout": 60.0, "json": False}
    values.update(overrides)
    return argparse.Namespace(**values)


class Workspace(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        write(self.root / "src/a.py", "def a(): return 1\n")
        write(self.root / "src/b.py", "def b(): return 2\n")
        write(self.root / "tests_a/test_a.py", "def test_a(): pass\n")
        write(self.root / "tests_b/test_b.py", "def test_b(): pass\n")
        self.register(
            suite("suite-a", ["tests_a"], PASS_CMD, ["src/a.py"]),
            suite("suite-b", ["tests_b"], FAIL_CMD, ["src/b.py"]),
            suite("suite-empty", ["tests_b"], "", []),
        )
        cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)

    def register(self, *suites: dict) -> None:
        write(self.root / ".ai/test-suites.json", json.dumps({"version": "1.0.0", "suites": list(suites)}))

    def run_cli(self, **overrides) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = ma.run_test_run(ns(**overrides))
        return rc, out.getvalue(), err.getvalue()

    def resolved(self) -> dict:
        return og.resolve_test_suites(self.root, ma.project_source_files())


class TestRunOneSuite(Workspace):
    def test_a_passing_command_is_pass_with_its_exit_code_duration_and_the_last_five_lines(self) -> None:
        result = ma.run_test_suite(suite("ok", ["tests_a"], PASS_CMD, []), self.root, 60)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["exit"], 0)
        self.assertEqual(result["tail"], ["line2", "line3", "line4", "line5", "line6"])
        self.assertGreaterEqual(result["duration_s"], 0.0)
        self.assertEqual(result["command"], PASS_CMD)

    def test_a_failing_command_is_fail_with_its_exit_code_and_output(self) -> None:
        result = ma.run_test_suite(suite("bad", ["tests_b"], FAIL_CMD, []), self.root, 60)
        self.assertEqual((result["status"], result["exit"]), ("FAIL", 3))
        self.assertIn("boom", result["tail"])

    def test_an_empty_command_is_skipped_not_run(self) -> None:
        result = ma.run_test_suite(suite("none", ["tests_b"], "   ", []), self.root, 60)
        self.assertEqual(result["status"], "SKIP")
        self.assertIsNone(result["exit"])
        self.assertEqual(result["command"], "")

    def test_a_command_that_overruns_the_timeout_is_timeout_not_a_hang(self) -> None:
        result = ma.run_test_suite(suite("slow", ["tests_a"], SLOW_CMD, []), self.root, 0.5)
        self.assertEqual(result["status"], "TIMEOUT")
        self.assertLess(result["duration_s"], 20.0)


class TestSelection(Workspace):
    def test_by_name_accepts_ids_and_display_names(self) -> None:
        chosen = ma.select_suites(self.resolved(), ["suite-a", "Suite B"], None)
        self.assertEqual([s["id"] for s in chosen], ["suite-a", "suite-b"])

    def test_without_names_or_an_impact_set_every_suite_is_selected(self) -> None:
        self.assertEqual([s["id"] for s in ma.select_suites(self.resolved(), [], None)], ["suite-a", "suite-b", "suite-empty"])

    def test_an_impact_set_matches_files_paths_and_coverage_with_directory_prefixes(self) -> None:
        resolved = self.resolved()
        self.assertEqual([s["id"] for s in ma.select_suites(resolved, [], {"src/a.py"})], ["suite-a"])
        self.assertEqual([s["id"] for s in ma.select_suites(resolved, [], {"src"})], ["suite-a", "suite-b"])   # a directory covers both
        self.assertEqual([s["id"] for s in ma.select_suites(resolved, [], {"tests_b/test_b.py"})], ["suite-b", "suite-empty"])
        self.assertEqual(ma.select_suites(resolved, [], {"docs/guide.md"}), [])
        self.assertEqual(ma.select_suites(resolved, [], set()), [])


class TestRunCommand(Workspace):
    def test_runs_the_named_suite_and_exits_zero_when_it_passes(self) -> None:
        rc, out, _ = self.run_cli(names=["suite-a"])
        self.assertEqual(rc, 0)
        self.assertIn("PASS", out)
        self.assertNotIn("suite-b", out)
        self.assertIn("1 suite(s): 1 passed, 0 failed", out)

    def test_exits_one_on_a_failure_and_shows_its_output_tail(self) -> None:
        rc, out, _ = self.run_cli(names=["suite-b"])
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", out)
        self.assertIn("boom", out)

    def test_an_empty_command_is_reported_as_skip_and_does_not_fail_the_run(self) -> None:
        rc, out, _ = self.run_cli(names=["suite-empty"])
        self.assertEqual(rc, 0)
        self.assertIn("SKIP", out)

    def test_an_unknown_name_is_an_error(self) -> None:
        rc, _, err = self.run_cli(names=["nope"])
        self.assertEqual(rc, 1)
        self.assertIn("Unknown test suite(s): nope", err)

    def test_json_output_has_one_result_per_suite_and_a_summary(self) -> None:
        rc, out, _ = self.run_cli(json=True)
        self.assertEqual(rc, 1)
        payload = json.loads(out)
        self.assertEqual(payload["selected"], ["suite-a", "suite-b", "suite-empty"])
        self.assertEqual([r["status"] for r in payload["results"]], ["PASS", "FAIL", "SKIP"])
        for result in payload["results"]:
            self.assertEqual(set(result), {"name", "id", "command", "status", "exit", "duration_s", "tail"})
        self.assertEqual(payload["summary"], {"PASS": 1, "FAIL": 1, "SKIP": 1, "TIMEOUT": 0})
        self.assertFalse(payload["ok"])
        self.assertIsNone(payload["impacted"])


class TestImpactedSelection(Workspace):
    def setUp(self) -> None:
        super().setUp()
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True, env=env)
        write(self.root / "src/a.py", "def a(): return 10\n")  # the pending change: src/a.py only

    def build_graph(self) -> None:
        graph = og.Graph()
        for relpath in ("src/a.py", "src/b.py", "tests_a/test_a.py", "tests_b/test_b.py"):
            module(graph, relpath)
        og.add_workspace_layer(graph, self.root)
        og.add_assurance_layer(graph, self.root)
        write(self.root / ma.GRAPH_DEFAULT_OUTPUT, json.dumps(graph.to_json(str(self.root), ["python"], {})))

    def test_without_a_graph_it_says_so_and_runs_every_suite(self) -> None:
        rc, out, _ = self.run_cli(impacted=True)
        self.assertEqual(rc, 1, "suite-b fails, and it ran because nothing could narrow the set")
        self.assertIn("no graph", out)
        self.assertIn("suite-a", out)
        self.assertIn("suite-b", out)

    def test_with_a_graph_only_the_suite_the_change_reaches_runs(self) -> None:
        self.build_graph()
        rc, out, _ = self.run_cli(impacted=True)
        self.assertEqual(rc, 0)
        self.assertNotIn("no graph", out)
        self.assertIn("suite-a", out)
        self.assertNotIn("suite-b", out)
        self.assertIn("1 suite(s): 1 passed", out)

    def test_with_a_graph_the_json_names_the_impacted_paths(self) -> None:
        self.build_graph()
        rc, out, _ = self.run_cli(impacted=True, json=True)
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertIn("src/a.py", payload["impacted"])
        self.assertEqual(payload["selected"], ["suite-a"])

    def test_names_take_precedence_over_impacted(self) -> None:
        self.build_graph()
        rc, out, _ = self.run_cli(names=["suite-b"], impacted=True)
        self.assertEqual(rc, 1)
        self.assertIn("suite-b", out)
        self.assertNotIn("suite-a", out)


class TestCompletionRule(unittest.TestCase):
    """The shipped `completion.tests` rule: required since REQ-047, so `omni gate` runs `omni test run --impacted`
    and memoises the pass on the scoped change set."""

    def rule(self) -> dict:
        pack = json.loads((ROOT / ".ai/rules/completion-workflow.json").read_text(encoding="utf-8"))
        return next(r for r in pack["rules"] if r["id"] == "completion.tests")

    def test_the_rule_is_required_and_declares_a_command_validation(self) -> None:
        rule = self.rule()
        self.assertEqual(rule["severity"], "required")
        self.assertIn("--impacted", rule["statement"])
        self.assertIn("memo", rule["statement"])
        self.assertIn("omni waive completion.tests", rule["statement"])
        self.assertNotIn("Recommended here", rule["statement"])
        validation = rule["validation"]
        self.assertEqual(validation["type"], "command")
        self.assertEqual(validation["run"], "omni test run --impacted")
        self.assertEqual(validation["when_changed"], ["**"])
        self.assertEqual(validation["ignore"], [".ai/**", "*.md", "docs/**"])
        self.assertEqual(validation["timeout"], 900)

    def test_the_required_rule_is_executed_by_the_gate(self) -> None:
        cwd = Path.cwd()
        os.chdir(ROOT)
        self.addCleanup(os.chdir, cwd)
        self.assertIn("completion.tests", {str(r["id"]) for r in ma.gate_rules()})

    def test_the_rulepack_still_validates(self) -> None:
        cwd = Path.cwd()
        os.chdir(ROOT)
        self.addCleanup(os.chdir, cwd)
        report = ma.DoctorReport()
        ma.validate_rulepacks({str(path): ma.load_json(Path(path)) for path in ma.RULEPACK_FILES if Path(path).is_file()}, report)
        self.assertEqual([e for e in report.errors if "completion" in e], [])


if __name__ == "__main__":
    unittest.main()
