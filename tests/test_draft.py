"""Tests for `omni requirement draft`: draft a requirement and a CHANGELOG.md stub from a commit or the
current change set, so the manual step is editing a draft instead of writing one from nothing.

Stdlib only. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_ai as ma  # noqa: E402


class TestInferDraftCategory(unittest.TestCase):
    def test_all_markdown_is_documentation(self) -> None:
        self.assertEqual(ma.infer_draft_category(["README.md", "docs/guide.md"]), "Documentation")

    def test_test_paths_are_testing(self) -> None:
        self.assertEqual(ma.infer_draft_category(["tests/test_app.py", "src/app.py"]), "Testing")

    def test_workflow_paths_are_process(self) -> None:
        self.assertEqual(ma.infer_draft_category([".github/workflows/ci.yml"]), "Process")

    def test_fix_in_a_path_is_defect(self) -> None:
        self.assertEqual(ma.infer_draft_category(["src/bugfix.py"]), "Defect")

    def test_ordinary_source_is_feature(self) -> None:
        self.assertEqual(ma.infer_draft_category(["src/app.py", "src/util.py"]), "Feature")

    def test_no_paths_does_not_crash(self) -> None:
        self.assertEqual(ma.infer_draft_category([]), "Process")


class TestRequirementDraft(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        self.git("init", "-q")
        (self.root / ".ai/requirements").mkdir(parents=True)
        (self.root / ".ai/requirements/requirements.json").write_text(
            json.dumps({"version": "1.0.0", "requirement_id_prefix": "REQ", "requirements": []}), encoding="utf-8"
        )
        self._cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self._cwd)

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True, env=self._env)
        return result.stdout

    def draft(self, **kwargs) -> int:
        base = {"commit": None, "id": None, "title": None, "category": None, "no_changelog": False, "force": False}
        base.update(kwargs)
        return ma.run_requirement_draft(argparse.Namespace(**base))

    def requirements(self) -> list[dict]:
        return json.loads((self.root / ".ai/requirements/requirements.json").read_text(encoding="utf-8"))["requirements"]

    def test_drafts_from_the_working_tree_with_no_commit(self) -> None:
        (self.root / "src").mkdir()
        (self.root / "src/app.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(self.draft(), 0)
        reqs = self.requirements()
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["status"], "proposed")
        self.assertIn("src/app.py", reqs[0]["minimum_access_scope"])
        self.assertIn("DRAFT", reqs[0]["description"])

    def test_title_is_not_double_prefixed_with_draft(self) -> None:
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.draft()
        title = self.requirements()[0]["title"]
        self.assertNotIn("DRAFT: DRAFT", title)
        changelog = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertNotIn("DRAFT: DRAFT", changelog)

    def test_free_text_fields_are_not_fragmented_by_commas(self) -> None:
        # split_csv() treats a bare comma as a list separator; a hand-written sentence must not trip it.
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.draft()
        entry = self.requirements()[0]
        self.assertEqual(len(entry["acceptance_criteria"]), 1)
        self.assertEqual(len(entry["risk_notes"]), 1)

    def test_title_comes_from_the_named_commits_subject(self) -> None:
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add a.py")
        sha = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(self.draft(commit=sha), 0)
        self.assertEqual(self.requirements()[0]["title"], "Add a.py")

    def test_a_commit_that_already_cites_a_requirement_is_not_duplicated(self) -> None:
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add a.py (REQ-009)")
        sha = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(self.draft(commit=sha), 0)
        self.assertEqual(self.requirements(), [])  # nothing drafted

    def test_force_drafts_anyway_over_an_existing_citation(self) -> None:
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add a.py (REQ-009)")
        sha = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(self.draft(commit=sha, force=True), 0)
        self.assertEqual(len(self.requirements()), 1)

    def test_explicit_title_and_category_are_used_verbatim(self) -> None:
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.draft(title="Real title", category="Feature")
        entry = self.requirements()[0]
        self.assertEqual(entry["title"], "Real title")
        self.assertEqual(entry["category"], "Feature")

    def test_no_changelog_flag_skips_the_stub(self) -> None:
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.draft(no_changelog=True)
        self.assertFalse((self.root / "CHANGELOG.md").is_file())

    def test_two_drafts_the_same_day_stack_under_one_proposed_section(self) -> None:
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.draft()
        (self.root / "b.py").write_text("y = 1\n", encoding="utf-8")
        self.draft()
        changelog = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertEqual(changelog.count("### Proposed"), 1)
        self.assertEqual(changelog.count("Generated by `omni requirement draft`"), 2)

    def test_nothing_changed_is_reported_not_silently_skipped(self) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "initial state, nothing left uncommitted")
        self.assertEqual(self.draft(), 1)
        self.assertEqual(self.requirements(), [])

    def test_outside_a_git_repository_reports_rather_than_guessing(self) -> None:
        os.chdir(self._cwd)
        with tempfile.TemporaryDirectory() as non_repo:
            os.chdir(non_repo)
            try:
                self.assertEqual(self.draft(commit="HEAD"), 1)
            finally:
                os.chdir(self._cwd)


if __name__ == "__main__":
    unittest.main()
