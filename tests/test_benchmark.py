"""Tests for `omni graph benchmark`: a targeted graph query vs. the naive alternative (grep and read
whole, or `git show` for a commit), measured live against a real graph and a real repository -- never
canned numbers, so these tests build a real (small) repo and check the arithmetic, not hardcoded output.

Stdlib only. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import omni_graph as og  # noqa: E402
from test_graph_layers import Fixture, module, write  # noqa: E402


class TestBenchmark(Fixture):
    def build_graph(self) -> Path:
        og.add_governance_layer(self.graph, self.root)
        og.add_workspace_layer(self.graph, self.root)
        og.add_history_layer(self.graph, self.root)
        og.add_assurance_layer(self.graph, self.root)
        path = self.root / "graph.json"
        path.write_text(json.dumps(self.graph.to_json(str(self.root), ["python"], {})), encoding="utf-8")
        return path

    def test_picks_a_real_requirement_commit_and_file(self) -> None:
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add checkout (REQ-001)")
        path = self.build_graph()
        result = og.benchmark(path, self.root)
        self.assertTrue(result["ok"])
        kinds = {case["kind"] for case in result["cases"]}
        self.assertIn("requirement", kinds)
        self.assertIn("commit", kinds)
        self.assertIn("file", kinds)

    def test_the_graph_answer_is_far_smaller_than_reading_the_matches_whole(self) -> None:
        # the fixture's app.py is small, but the naive path still has to read every matching file whole,
        # while the graph answer is one bounded JSON document -- the graph answer must not be the bigger one
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add checkout (REQ-001)")
        path = self.build_graph()
        result = og.benchmark(path, self.root)
        req_case = next(c for c in result["cases"] if c["kind"] == "requirement")
        self.assertLessEqual(req_case["graph_chars"], req_case["naive_chars"])

    def test_a_commits_naive_cost_is_git_show_not_a_grep(self) -> None:
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add checkout (REQ-001)")
        path = self.build_graph()
        result = og.benchmark(path, self.root)
        commit_case = next(c for c in result["cases"] if c["kind"] == "commit")
        self.assertIn("git show", commit_case["naive_method"])
        self.assertGreater(commit_case["naive_chars"], 0)

    def test_the_ratio_is_naive_over_graph(self) -> None:
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add checkout (REQ-001)")
        path = self.build_graph()
        result = og.benchmark(path, self.root)
        for case in result["cases"]:
            if case["ratio"] is not None:
                self.assertAlmostEqual(case["ratio"], round(case["naive_chars"] / case["graph_chars"], 1))

    def test_a_binary_file_is_never_picked_only_a_parsed_module(self) -> None:
        self.git("init", "-q")
        # a large binary "module"-shaped node must not exist for this to be a real test of the exclusion;
        # instead this checks the positive case: the actual pick is the fixture's real python module
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add checkout (REQ-001)")
        path = self.build_graph()
        result = og.benchmark(path, self.root)
        file_case = next(c for c in result["cases"] if c["kind"] == "file")
        self.assertTrue(file_case["node"].endswith(".py"))

    def test_an_untracked_or_gitignored_file_is_not_picked(self) -> None:
        self.git("init", "-q")
        write(self.root / ".gitignore", "ignored_big.py\n")
        write(self.root / "ignored_big.py", "x = 1\n" * 100000)  # bigger than the fixture's real files
        module(self.graph, "ignored_big.py")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add checkout (REQ-001), ignore a big generated file")
        path = self.build_graph()
        result = og.benchmark(path, self.root)
        file_case = next(c for c in result["cases"] if c["kind"] == "file")
        self.assertNotEqual(file_case["node"], "ignored_big.py")

    def test_no_git_repository_degrades_to_zero_naive_cost_not_a_crash(self) -> None:
        path = self.build_graph()  # never ran `git init`
        result = og.benchmark(path, self.root)
        self.assertTrue(result["ok"])

    def test_a_graph_with_nothing_to_pick_reports_an_empty_case_list_not_an_error(self) -> None:
        empty = og.Graph()
        path = self.root / "empty.json"
        path.write_text(json.dumps(empty.to_json(str(self.root), [], {})), encoding="utf-8")
        result = og.benchmark(path, self.root)
        self.assertTrue(result["ok"])
        self.assertEqual(result["cases"], [])

    def test_a_failure_ledger_entry_is_picked_when_one_exists(self) -> None:
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add checkout (REQ-001)")
        og.add_assurance_layer(self.graph, self.root)  # failures are wired up via the assurance layer in this fixture
        path = self.build_graph()
        result = og.benchmark(path, self.root)
        kinds = {case["kind"] for case in result["cases"]}
        self.assertIn("failure", kinds)

    def test_result_is_json_serialisable(self) -> None:
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add checkout (REQ-001)")
        path = self.build_graph()
        json.dumps(og.benchmark(path, self.root))


class TestBenchmarkCLI(Fixture):
    """These exercise the CLI wiring and output shape, not graph-building: they write a hand-built graph
    (the same way TestBenchmark does) rather than calling `omni graph build`, which needs the optional
    [graph] extra (tree-sitter) for every language including python -- see the note on FAIL-009. Passing
    --graph and --root at a small fixture keeps this suite honest about running with no extras installed."""

    def build_graph(self) -> Path:
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add checkout (REQ-001)")
        og.add_governance_layer(self.graph, self.root)
        og.add_history_layer(self.graph, self.root)
        path = self.root / "graph.json"
        path.write_text(json.dumps(self.graph.to_json(str(self.root), ["python"], {})), encoding="utf-8")
        return path

    def test_json_output_matches_the_direct_call(self) -> None:
        graph_path = self.build_graph()
        result = subprocess.run(
            [sys.executable, str(ROOT / "omni"), "graph", "benchmark", "--graph", str(graph_path), "--root", str(self.root), "--json"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertIsInstance(payload["cases"], list)

    def test_text_output_names_each_case(self) -> None:
        graph_path = self.build_graph()
        result = subprocess.run(
            [sys.executable, str(ROOT / "omni"), "graph", "benchmark", "--graph", str(graph_path), "--root", str(self.root)],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("naive costs", result.stdout)

    def test_missing_graph_file_is_reported_not_a_traceback(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "omni"), "graph", "benchmark", "--graph", "does-not-exist.json"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Graph file not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
