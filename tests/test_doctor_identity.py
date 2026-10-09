"""Tests for doctor's commit-identity check (REQ-049): a commit since the gate base (or among the last 20
when there is no base) whose author or committer email is not `git config user.email`, or whose domain is
`.local` or `localhost`, is a warning that names the commit, the email and the fix; it is never an error,
and a repository with no configured email skips the check. Stdlib only. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_ai as ma  # noqa: E402

CONFIGURED = "dev@example.invalid"


class IdentityFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        # Keep the developer's own global git config out of it: the check reads `git config user.email`.
        self._env = mock.patch.dict(os.environ, {
            "HOME": str(self.root), "GIT_CONFIG_GLOBAL": str(self.root / "no-global-gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
        })
        self._env.start()
        self.addCleanup(self._env.stop)
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        self._cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self._cwd)

    def git(self, *args: str, email: str | None = None, committer: str | None = None) -> str:
        env = dict(os.environ)
        if email:
            env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL=committer or email)
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True, env=env).stdout

    def commit(self, name: str, email: str, committer: str | None = None) -> str:
        (self.root / name).write_text(f"{name}\n", encoding="utf-8")
        self.git("add", name)
        self.git("commit", "-q", "-m", f"add {name}", email=email, committer=committer)
        return self.git("rev-parse", "--short", "HEAD").strip()

    def check(self) -> ma.DoctorReport:
        report = ma.DoctorReport()
        ma.validate_commit_identity(report)
        return report


class TestCommitIdentity(IdentityFixture):
    def test_commits_under_the_configured_email_are_quiet(self) -> None:
        # the adopt loop's shape: `git config user.email ci@example.invalid`, then commits with it
        self.git("config", "user.email", CONFIGURED)
        self.commit("a.txt", CONFIGURED)
        self.commit("b.txt", CONFIGURED)
        report = self.check()
        self.assertEqual(report.warnings, [])
        self.assertEqual(report.errors, [])
        self.assertTrue(any("carry the configured identity" in line for line in report.passed), report.passed)

    def test_a_dot_local_author_is_a_warning_naming_commit_email_and_fix(self) -> None:
        self.git("config", "user.email", CONFIGURED)
        short = self.commit("a.txt", "omni@local")
        report = self.check()
        self.assertEqual(report.errors, [], "never an error")
        self.assertEqual(len(report.warnings), 1)
        warning = report.warnings[0]
        self.assertIn(short, warning)
        self.assertIn("omni@local", warning)
        self.assertIn("git commit --amend --reset-author", warning)
        self.assertIn("author", warning)

    def test_a_committer_email_that_differs_from_the_configured_one_is_named(self) -> None:
        self.git("config", "user.email", CONFIGURED)
        short = self.commit("a.txt", CONFIGURED, committer="someone@else.example")
        warning = self.check().warnings[0]
        self.assertIn(f"{short} committer someone@else.example differs from git config user.email ({CONFIGURED})", warning)

    def test_a_localhost_or_dot_local_domain_warns_even_when_it_matches_the_configuration(self) -> None:
        self.git("config", "user.email", "me@localhost")
        self.commit("a.txt", "me@localhost")
        self.assertIn("machine-local domain `localhost`", self.check().warnings[0])
        self.git("config", "user.email", "me@box.local")
        self.commit("b.txt", "me@box.local")
        self.assertIn("machine-local domain `box.local`", self.check().warnings[0])

    def test_an_older_commit_needs_a_rebase_not_an_amend(self) -> None:
        self.git("config", "user.email", CONFIGURED)
        bad = self.commit("a.txt", "omni@local")
        self.commit("b.txt", CONFIGURED)
        warning = self.check().warnings[0]
        self.assertIn(bad, warning)
        self.assertIn("git rebase -i", warning)
        self.assertNotIn("--amend", warning)

    def test_only_commits_since_the_gate_base_are_checked_when_there_is_one(self) -> None:
        self.git("config", "user.email", CONFIGURED)
        self.commit("a.txt", "omni@local")  # on main, before the branch point: not this change's business
        self.git("checkout", "-q", "-b", "feature")
        good = self.commit("b.txt", CONFIGURED)
        report = self.check()
        self.assertEqual(report.warnings, [], report.warnings)
        self.assertTrue(any("since the gate base" in line for line in report.passed), report.passed)
        bad = self.commit("c.txt", "omni@local")
        warning = self.check().warnings[0]
        self.assertIn(bad, warning)
        self.assertNotIn(good, warning)

    def test_without_a_base_the_last_twenty_are_checked(self) -> None:
        self.git("config", "user.email", CONFIGURED)
        self.commit("a.txt", "omni@local")
        self.commit("b.txt", CONFIGURED)
        report = self.check()  # on main itself: merge-base with main is HEAD, so there is no base to diff from
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("among the last 20", report.warnings[0])

    def test_no_configured_email_skips_the_check(self) -> None:
        self.commit("a.txt", "omni@local")
        report = self.check()
        self.assertEqual(report.warnings, [])
        self.assertEqual(report.passed, [])

    def test_the_check_is_part_of_doctor(self) -> None:
        self.assertIn("validate_commit_identity(report)", (ROOT / "make_ai.py").read_text(encoding="utf-8").split("def build_doctor_report", 1)[1].split("def run_doctor", 1)[0])


if __name__ == "__main__":
    unittest.main()
