"""REQ-041: the adopt loop proven end to end. A fresh directory adopts the workspace with Arbiter wired in
(`omni adopt --with-arbiter`), becomes a git repository, gets a real defect planted (`verify=False`), and
`omni gate` must fail on `completion.arbiter_gate` alone while the required `completion.tests` rule (REQ-047)
runs and passes with no suite registered; the defect is fixed and the same gate must pass.
This mirrors the adopt-loop job in .github/workflows/ci.yml, so a break in the loop shows up here before
it shows up there. The loop needs the real `arbiter` and `git` on PATH and is skipped without them; the
adoption regression test below runs everywhere. Stdlib only. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import functools
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_ai as ma  # noqa: E402

GATE_TIMEOUT = 180  # seconds per `omni gate` call; the rule's own `arbiter gate` command runs verbatim inside it
SEED_ID = "REQ-900"  # the adopted registry is a copy of this repository's, so REQ-001 already exists there
PLANTED = "import requests\n\n\ndef fetch(url):\n    return requests.get(url, verify=False)\n"
TESTS_RULE_RAN = r"(?m)^  ran +completion\.tests \d+\.\ds PASS$"  # the gate's per-rule line for a run, passed command rule


class TestAdoptCopiesNestedCliFilesWithTheirDirectory(unittest.TestCase):
    """`--include-cli` lists the graph-viewer files, which live under `.ai` and so are already in place
    once `.ai` is copied whole; reporting them as "skip existing" made every such adoption exit 1."""

    def test_include_cli_into_an_empty_directory_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = ma.build_parser().parse_args(["adopt", "--target", tmp, "--tools", "none", "--include-cli"])
            out = io.StringIO()
            with redirect_stdout(out):
                code = ma.run_adopt(args)
            self.assertEqual(code, 0, out.getvalue())
            self.assertNotIn("skip existing", out.getvalue())
            self.assertIn("copied: .ai/graph-viewer/viewer.html (with .ai)", out.getvalue())
            self.assertTrue((Path(tmp) / ".ai" / "graph-viewer" / "viewer.html").is_file())

    def test_dry_run_reports_the_same_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = ma.build_parser().parse_args(["adopt", "--target", tmp, "--tools", "none", "--include-cli", "--dry-run"])
            out = io.StringIO()
            with redirect_stdout(out):
                code = ma.run_adopt(args)
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn("copy: .ai/graph-viewer/viewer.html (with .ai)", out.getvalue())
            self.assertFalse((Path(tmp) / ".ai").exists())


@unittest.skipUnless(shutil.which("arbiter") and shutil.which("git"), "needs the real `arbiter` and `git` on PATH")
class TestAdoptLoop(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        self.transcript: list[str] = []  # every command's output, shown whole when something is not as expected

    # -- helpers -----------------------------------------------------------------------------------

    def diagnosis(self) -> str:
        return "\n\n".join(self.transcript)

    def run_in_repo(self, *argv: str, timeout: float = 60, check: bool = True) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            list(argv), cwd=self.root, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=self.env,
        )
        self.transcript.append(f"$ {' '.join(argv)}\n{completed.stdout}{completed.stderr}(exit {completed.returncode})")
        if check:
            self.assertEqual(completed.returncode, 0, self.diagnosis())
        return completed

    def omni(self, *argv: str, timeout: float = 60, check: bool = True) -> subprocess.CompletedProcess[str]:
        # The adopted copy of the CLI, from the adopted directory: exactly what an adopter runs.
        return self.run_in_repo(sys.executable, "omni", *argv, timeout=timeout, check=check)

    def gate(self, expected_code: int) -> str:
        completed = self.omni("gate", timeout=GATE_TIMEOUT, check=False)
        if completed.returncode != expected_code:
            self.omni("doctor", check=False)  # the gate ran doctor first; its full report makes a red run diagnosable
            self.fail(f"expected `omni gate` to exit {expected_code}, got {completed.returncode}\n\n{self.diagnosis()}")
        return completed.stdout + completed.stderr

    def adopt(self) -> None:
        # The same argv as the CI job, through the same parser. CI installs Arbiter's [mcp] extra only, so no
        # adapter (semgrep, bandit, ...) exists there and the baseline scan is native probes only; a developer
        # machine with those tools on PATH would spend minutes on them for the copied CLI's two large files, so
        # the baseline cut here says so explicitly. The two gate runs below use the rule's command verbatim.
        argv = [
            "adopt", "--target", str(self.root), "--tools", "all", "--include-cli", "--include-legal",
            "--with-arbiter", ma.ARBITER_DEFAULT_SOURCE, "--skip-pip",
        ]
        args = ma.build_parser().parse_args(argv)
        baseline = functools.partial(ma.arbiter_write_baseline, scan_args=("--no-adapters",))
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(ma, "arbiter_write_baseline", baseline):
            code = ma.run_adopt(args)
        self.transcript.append(f"$ omni {' '.join(argv)}\n{out.getvalue()}(exit {code})")
        self.assertEqual(code, 0, self.diagnosis())
        self.assertTrue((self.root / ma.ARBITER_BASELINE_PATH).is_file(), self.diagnosis())

    # -- the loop ----------------------------------------------------------------------------------

    def test_planted_defect_fails_the_gate_on_the_arbiter_rule_alone_and_the_fix_passes(self) -> None:
        self.adopt()
        # Doctor, which every gate runs first, wants a README; CHANGELOG.md is what the co_changed rules watch.
        (self.root / "README.md").write_text("# Adopt loop\n", encoding="utf-8")
        (self.root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
        self.run_in_repo("git", "init", "-q")
        self.run_in_repo("git", "symbolic-ref", "HEAD", "refs/heads/main")
        self.run_in_repo("git", "config", "user.email", "ci@example.invalid")
        self.run_in_repo("git", "config", "user.name", "ci")
        self.run_in_repo("git", "add", "-A")
        self.run_in_repo("git", "commit", "-q", "-m", "Adopt the workspace with Arbiter (REQ-001)")

        # The seed requirement and the changelog line ride in the same uncommitted change as the code, so
        # the requirements_registry and changelog_gate co_changed rules are satisfied and cannot mask Arbiter.
        self.omni("requirement", "add", "--id", SEED_ID, "--title", "Adopt loop seed",
                  "--description", "Seed requirement for the adopt loop", "--category", "Feature")
        app = self.root / "src" / "app.py"
        app.parent.mkdir()
        app.write_text(PLANTED, encoding="utf-8")
        with (self.root / "CHANGELOG.md").open("a", encoding="utf-8") as handle:
            handle.write(f"- {SEED_ID}: src/app.py fetches a url\n")

        output = self.gate(1)
        self.assertIn(ma.ARBITER_GATE_RULE_ID, output, self.diagnosis())
        self.assertNotIn("data.privacy", output, self.diagnosis())
        self.assertNotIn("changelog_gate", output, self.diagnosis())
        failed_rules = [line for line in output.splitlines() if line.startswith("  - ")]
        self.assertEqual(
            [line for line in failed_rules if not line.startswith(f"  - {ma.ARBITER_GATE_RULE_ID}:")], [],
            f"rules other than {ma.ARBITER_GATE_RULE_ID} failed, so the Arbiter verdict is masked\n\n{self.diagnosis()}",
        )
        self.assertTrue((self.root / ma.ARBITER_GATE_OUT_DIR / "report.sarif").is_file(), self.diagnosis())
        # REQ-047: the tests rule is required, and the adopted registry has no suites, so `omni test run
        # --impacted` has nothing to run and passes; it must have run (not been skipped or memoised) here.
        self.assertRegex(output, TESTS_RULE_RAN, f"completion.tests did not run and pass\n\n{self.diagnosis()}")

        app.write_text(PLANTED.replace("verify=False", "verify=True"), encoding="utf-8")
        output = self.gate(0)
        self.assertIn("omni gate: PASS", output, self.diagnosis())
        self.assertRegex(output, TESTS_RULE_RAN, f"the fix changed src/app.py, so completion.tests must run again\n\n{self.diagnosis()}")


if __name__ == "__main__":
    unittest.main()
