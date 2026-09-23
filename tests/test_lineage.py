"""Tests for `omni graph lineage`: pick any node and trace up and down with no second endpoint.

Stdlib only. The graph comes from the same small fixture repository as the layer tests, with real commits.
Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import omni_graph as og  # noqa: E402
from test_graph_layers import Fixture, write  # noqa: E402


class TestLineage(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add checkout (REQ-001)")
        write(self.root / "src/other.py", "def other(): return 1\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Fix rounding (REQ-002)")
        og.add_governance_layer(self.graph, self.root)
        og.add_workspace_layer(self.graph, self.root)
        og.add_history_layer(self.graph, self.root)
        og.add_assurance_layer(self.graph, self.root)
        self.path = self.root / "graph.json"
        self.path.write_text(json.dumps(self.graph.to_json(str(self.root), ["python"], {})), encoding="utf-8")

    def ids(self, result: dict, side: str) -> set[str]:
        return {n["id"] for n in result[side]}

    def names(self, result: dict, side: str, kind: str) -> set[str]:
        return {n["name"] for n in result[side] if n["kind"] == kind}

    def commit_named(self, subject_part: str) -> str:
        node = next(n for n in self.graph.nodes.values() if n.kind == "commit" and subject_part in (n.summary or ""))
        return node.id

    def test_a_requirement_needs_no_endpoint_and_reaches_its_delivery_and_files(self) -> None:
        result = og.lineage(self.path, "REQ-001")
        self.assertTrue(result["ok"])
        self.assertEqual(result["upstream"], [])
        self.assertIn(self.commit_named("Add checkout"), self.ids(result, "downstream"))
        self.assertIn("src/app.py", self.ids(result, "downstream"))
        self.assertTrue(self.names(result, "downstream", "changelog"))

    def test_a_commit_traces_up_to_its_requirement_and_down_to_what_it_changed(self) -> None:
        commit = self.commit_named("Fix rounding")
        result = og.lineage(self.path, commit)
        self.assertIn("req:REQ-002", self.ids(result, "upstream"))
        self.assertIn("src/other.py", self.ids(result, "downstream"))
        self.assertNotIn("req:REQ-002", self.ids(result, "downstream"))

    def test_direction_flags_limit_the_walk(self) -> None:
        commit = self.commit_named("Fix rounding")
        self.assertEqual(og.lineage(self.path, commit, direction="up")["downstream"], [])
        self.assertEqual(og.lineage(self.path, commit, direction="down")["upstream"], [])

    def test_the_previous_commit_is_shown_but_not_walked_into(self) -> None:
        first, second = self.commit_named("Add checkout"), self.commit_named("Fix rounding")
        result = og.lineage(self.path, second, direction="up")
        self.assertIn(first, self.ids(result, "upstream"))
        # REQ-001 belongs to the first commit, not to the one being asked about
        self.assertNotIn("req:REQ-001", self.ids(result, "upstream"))

    def test_a_requirements_contents_are_not_expanded_but_a_modules_are(self) -> None:
        from_req = og.lineage(self.path, "REQ-001")
        self.assertNotIn("src/app.py::total", self.ids(from_req, "downstream"))
        from_module = og.lineage(self.path, "src/app.py", direction="down")
        self.assertIn("src/app.py::total", self.ids(from_module, "downstream"))

    def test_code_dependencies_are_opt_in(self) -> None:
        # Without --code the test function reaches the code it calls only the long way round, through its module's inferred
        # `verifies` link. With it, the direct call is one link.
        test_fn = "tests/test_app.py::test_total_keeps_cents"
        depth = lambda result: next(n["depth"] for n in result["upstream"] if n["id"] == "src/app.py::total")  # noqa: E731
        self.assertGreater(depth(og.lineage(self.path, test_fn, direction="up")), 1)
        self.assertEqual(depth(og.lineage(self.path, test_fn, direction="up", include_code=True)), 1)

    def test_a_failure_traces_to_its_requirement_tests_and_rules(self) -> None:
        result = og.lineage(self.path, "FAIL-001")
        self.assertIn("req:REQ-002", self.ids(result, "upstream"))
        down = self.ids(result, "downstream")
        self.assertTrue(any("test_total_keeps_cents" in i for i in down))
        self.assertTrue(any("money.no_floats" in i for i in down))

    def test_depth_and_cap_bound_the_answer_nearest_first(self) -> None:
        shallow = og.lineage(self.path, "REQ-001", depth=1)
        self.assertTrue(all(n["depth"] == 1 for n in shallow["downstream"]))
        capped = og.lineage(self.path, "REQ-001", limit=2)
        self.assertEqual(len(capped["downstream"]), 2)
        self.assertGreater(capped["truncated"]["downstream"], 0)

    def test_every_returned_edge_joins_members_of_the_lineage(self) -> None:
        result = og.lineage(self.path, "REQ-001")
        members = {result["node"]["id"]} | self.ids(result, "upstream") | self.ids(result, "downstream")
        self.assertTrue(result["edges"])
        for edge in result["edges"]:
            self.assertIn(edge["source"], members)
            self.assertIn(edge["target"], members)

    def test_unknown_and_ambiguous_nodes_are_reported_not_guessed(self) -> None:
        self.assertFalse(og.lineage(self.path, "REQ-999")["ok"])

    def test_a_walk_terminates_on_a_cycle(self) -> None:
        self.graph.add_edge("src/app.py", "src/other.py", "defines", "EXTRACTED", "test")
        self.graph.add_edge("src/other.py", "src/app.py", "defines", "EXTRACTED", "test")
        self.path.write_text(json.dumps(self.graph.to_json(str(self.root), ["python"], {})), encoding="utf-8")
        self.assertTrue(og.lineage(self.path, "src/app.py")["ok"])


if __name__ == "__main__":
    unittest.main()
