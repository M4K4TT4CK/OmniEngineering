"""Tests for the portable git pre-commit hook (`omni hook install-git`).

The Claude Code Stop hook only enforces the gate inside Claude Code. This installs a plain
`.githooks/pre-commit` script and points `core.hooksPath` at it, so the same `omni gate` blocks a
commit for any assistant, or no assistant at all, on Linux, macOS or Windows (Git for Windows always
runs hooks through its own bundled sh). Stdlib only. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_ai as ma  # noqa: E402


class TestPreCommitHookInstall(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True, env=env)
        self._env = env
        self._cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self._cwd)

    def install(self, force: bool = False, with_graph_rebuild: bool = False) -> int:
        return ma.run_hook_install_git(argparse.Namespace(force=force, with_graph_rebuild=with_graph_rebuild))

    def hooks_path(self) -> str | None:
        result = subprocess.run(["git", "config", "--local", "core.hooksPath"], cwd=self.root, capture_output=True, text=True)
        return result.stdout.strip() or None

    def test_writes_the_hook_and_points_hookspath_at_it(self) -> None:
        self.assertEqual(self.install(), 0)
        hook = self.root / ".githooks" / "pre-commit"
        self.assertTrue(hook.is_file())
        text = hook.read_text(encoding="utf-8")
        self.assertIn(ma.PRE_COMMIT_HOOK_MARKER, text)
        self.assertIn("omni gate", text)
        self.assertNotIn("\r", text, "the hook must be LF-only or it breaks the same way FAIL-001 did")
        self.assertEqual(self.hooks_path(), ".githooks")

    def test_running_twice_is_a_no_op_not_a_duplicate(self) -> None:
        self.assertEqual(self.install(), 0)
        first = (self.root / ".githooks" / "pre-commit").read_text(encoding="utf-8")
        self.assertEqual(self.install(), 0)
        self.assertEqual((self.root / ".githooks" / "pre-commit").read_text(encoding="utf-8"), first)

    def test_a_foreign_hook_is_not_clobbered_without_force(self) -> None:
        hook = self.root / ".githooks" / "pre-commit"
        hook.parent.mkdir(parents=True)
        hook.write_text("#!/bin/sh\necho someone-elses-hook\n", encoding="utf-8")
        self.assertEqual(self.install(force=False), 1)
        self.assertIn("someone-elses-hook", hook.read_text(encoding="utf-8"))
        self.assertEqual(self.install(force=True), 0)
        self.assertIn(ma.PRE_COMMIT_HOOK_MARKER, hook.read_text(encoding="utf-8"))

    def test_with_graph_rebuild_also_writes_a_post_commit_hook(self) -> None:
        self.assertEqual(self.install(with_graph_rebuild=True), 0)
        post = (self.root / ".githooks" / "post-commit").read_text(encoding="utf-8")
        self.assertIn(ma.POST_COMMIT_HOOK_MARKER, post)
        self.assertIn("graph build", post)
        self.assertNotIn("\r", post)
        # it must background the build (not block the commit) and use a lock so two runs don't pile up
        self.assertRegex(post, r"\)\s*&\s*\n")
        self.assertIn('mkdir "$lock"', post)

    def test_without_the_flag_no_post_commit_hook_is_written(self) -> None:
        self.assertEqual(self.install(), 0)
        self.assertFalse((self.root / ".githooks" / "post-commit").is_file())

    @unittest.skipUnless(os.name == "posix", "runs a shell script directly")
    def test_the_post_commit_hook_rebuilds_the_graph_in_the_background_without_blocking(self) -> None:
        import time

        self.assertEqual(self.install(with_graph_rebuild=True), 0)
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        for name in ("omni", "make_ai.py", "omni_graph.py"):  # the hook execs "$root/omni"; it must actually be there
            (self.root / name).write_bytes((ROOT / name).read_bytes())
        (self.root / "omni").chmod(0o755)
        started = time.monotonic()
        # DEVNULL, not capture_output: a pipe's write end is inherited by the backgrounded grandchild too, so
        # capturing output would make this wait for EOF on it -- i.e. for the whole background build to finish,
        # exactly the non-blocking behaviour under test. Plain `git` invoking a hook never captures its output
        # this way either; hooks inherit git's own stdout/stderr, which is never something that reaches EOF.
        result = subprocess.run(
            ["sh", str(self.root / ".githooks" / "post-commit")], cwd=self.root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 0)
        self.assertLess(elapsed, 2.0, "the hook must return immediately; the rebuild belongs in the background")
        deadline = time.monotonic() + 30
        while not (self.root / ".ai" / "project-graph.json").is_file() and time.monotonic() < deadline:
            time.sleep(0.5)
        self.assertTrue((self.root / ".ai" / "project-graph.json").is_file(), "the background build never produced a graph")
        self.assertFalse((self.root / ".ai" / ".graph-build.lock").is_dir(), "the lock must be released once the build finishes")

    @unittest.skipUnless(os.name == "posix", "runs a shell script directly")
    def test_a_build_already_in_flight_is_not_duplicated(self) -> None:
        self.assertEqual(self.install(with_graph_rebuild=True), 0)
        lock = self.root / ".ai" / ".graph-build.lock"
        lock.mkdir(parents=True)
        result = subprocess.run(
            ["sh", str(self.root / ".githooks" / "post-commit")], cwd=self.root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        import time
        time.sleep(0.5)
        self.assertTrue(lock.is_dir(), "a contended run must leave the existing lock alone, not race it")

    def test_outside_a_git_repository_it_reports_rather_than_guessing(self) -> None:
        os.chdir(self._cwd)  # leave the temp repo
        with tempfile.TemporaryDirectory() as non_repo:
            os.chdir(non_repo)
            try:
                self.assertEqual(self.install(), 1)
            finally:
                os.chdir(self._cwd)

    @unittest.skipUnless(os.name == "posix", "the executable bit and a shell-run hook only apply on POSIX")
    def test_the_installed_hook_actually_runs_and_blocks_an_unscaffolded_commit(self) -> None:
        # This repo has no .ai/ workspace, so `omni gate` (which the hook calls) fails doctor and the
        # commit must be refused -- proving the hook is wired up, not just written to disk.
        self.assertEqual(self.install(), 0)
        omni_source = ROOT / "omni"
        (self.root / "omni").write_bytes(omni_source.read_bytes())
        (self.root / "omni").chmod(0o755)
        (self.root / "make_ai.py").write_bytes((ROOT / "make_ai.py").read_bytes())
        (self.root / "omni_graph.py").write_bytes((ROOT / "omni_graph.py").read_bytes())
        (self.root / "README.md").write_text("hi\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True, env=self._env)
        result = subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=self.root, capture_output=True, text=True, env=self._env)
        self.assertNotEqual(result.returncode, 0, "the pre-commit hook should have blocked this commit")
        self.assertIn("omni gate", (result.stdout + result.stderr))


if __name__ == "__main__":
    unittest.main()
