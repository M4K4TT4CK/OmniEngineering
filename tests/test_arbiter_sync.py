"""Tests for `omni arbiter sync` (REQ-049): the subtree pull, the editable reinstall, the version record,
the baseline refresh and doctor run in order from one command, every step is printed before it runs, the
first failing step stops the sequence naming itself, `--dry-run` prints the commands only, and pip mode
upgrades from the recorded source. Nothing real runs: subprocess.run and shutil.which are stubbed.
Stdlib only. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_ai as ma  # noqa: E402


class FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class SyncFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        (self.root / ".ai").mkdir()
        self.version_file = self.root / ma.OMNI_VERSION_FILE
        self.version_file.write_text(json.dumps({
            "source": "../OmniEngineering", "ref": "abc",
            "arbiter": {"source": ma.ARBITER_DEFAULT_SOURCE, "version": "0.1.0", "recorded_at": "2026-10-01T00:00:00+00:00"},
        }), encoding="utf-8")

    def vendor(self) -> None:
        (self.root / "arbiter").mkdir()
        (self.root / "arbiter" / "pyproject.toml").write_text("[project]\nname='arbiter-eval'\n", encoding="utf-8")

    def sync(self, failing: str | None = None, **overrides) -> tuple[int, str, str, list[list[str]]]:
        """Run the command with every subprocess stubbed; `failing` names a step's first argv token
        (e.g. "pip") whose fake exits 1. Returns (exit code, stdout, stderr, the argv lists run)."""
        calls: list[list[str]] = []

        def run(command, **kwargs):
            command = list(command)
            calls.append(command)
            if command[:2] == ["arbiter", "--version"]:
                return FakeCompleted(stdout="arbiter 0.2.0\n")
            if command[:1] == ["git"] and "log" in command:
                return FakeCompleted(stdout="")  # no subtree marker commit: unsquashed pull
            if failing and failing in " ".join(command):
                return FakeCompleted(returncode=1, stderr="boom: the step broke\n")
            return FakeCompleted(stdout="ok\n")

        args = argparse.Namespace(target=str(self.root), source=None, branch="main", skip_baseline=False, dry_run=False)
        for key, value in overrides.items():
            setattr(args, key, value)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), \
                mock.patch.object(ma.subprocess, "run", side_effect=run), \
                mock.patch.object(ma.shutil, "which", side_effect=lambda name: "/usr/local/bin/arbiter" if name == "arbiter" else None):
            code = ma.run_arbiter_sync(args)
        return code, out.getvalue(), err.getvalue(), calls

    @staticmethod
    def step_names(output: str) -> list[str]:
        return [line.split("] ", 1)[1].split(":", 1)[0] for line in output.splitlines() if line.startswith("[")]


class TestSubtreeMode(SyncFixture):
    def test_the_steps_run_in_order_and_the_version_is_re_recorded(self) -> None:
        self.vendor()
        code, out, err, calls = self.sync()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.step_names(out), ["subtree pull", "pip install", "record version", "baseline", "doctor"])
        pull = calls[1]  # calls[0] is the subtree-mode probe (git log for the marker commit)
        self.assertEqual(pull[:4], ["git", "subtree", "pull", "--prefix=arbiter"])
        self.assertEqual(pull[4], "https://github.com/paukennick/arbiter", "the recorded git+ source minus its prefix")
        self.assertEqual(pull[5:], ["main", "-m", "Pull Arbiter main into arbiter/"])
        pip = calls[2]
        self.assertEqual(pip[:4], [sys.executable, "-m", "pip", "install"])
        self.assertEqual(pip[4:], ["-e", f"./arbiter[{ma.ARBITER_PIP_EXTRAS}]"])
        self.assertIn(["arbiter", "--version"], calls, "the version record asks the installed Arbiter")
        baseline = next(c for c in calls if c[-2:] == ["arbiter", "baseline"] or "baseline" in c)
        self.assertEqual(baseline[0], sys.executable)
        self.assertTrue(baseline[1].endswith("omni"), baseline)
        self.assertEqual(baseline[2:], ["arbiter", "baseline"], "no baseline file yet, so an initial cut, not --refresh")
        doctor = calls[-1]
        self.assertEqual(doctor[2:], ["doctor"])
        recorded = json.loads(self.version_file.read_text(encoding="utf-8"))["arbiter"]
        self.assertEqual(recorded["version"], "0.2.0")
        self.assertEqual(recorded["source"], ma.ARBITER_DEFAULT_SOURCE)
        self.assertIn("Arbiter sync complete.", out)

    def test_an_existing_baseline_is_refreshed(self) -> None:
        self.vendor()
        (self.root / ".arbiter").mkdir()
        (self.root / ".arbiter" / "baseline.json").write_text('{"ids": []}', encoding="utf-8")
        _, out, _, calls = self.sync()
        baseline = next(c for c in calls if "baseline" in c)
        self.assertEqual(baseline[2:], ["arbiter", "baseline", "--refresh"])
        self.assertIn("--refresh", out)

    def test_a_failing_step_stops_the_sequence_naming_it(self) -> None:
        self.vendor()
        code, out, err, calls = self.sync(failing="pip")
        self.assertEqual(code, 1)
        self.assertIn("step 2/5 (pip install) failed with exit 1", err)
        self.assertIn("boom: the step broke", err)
        self.assertIn("rerun `omni arbiter sync`", err)
        self.assertEqual(self.step_names(out), ["subtree pull", "pip install"], "nothing after the failed step ran")
        self.assertFalse(any("doctor" in c for c in calls))
        self.assertFalse(any("baseline" in c for c in calls))
        self.assertEqual(json.loads(self.version_file.read_text(encoding="utf-8"))["arbiter"]["version"], "0.1.0", "not re-recorded")

    def test_a_subtree_conflict_says_to_resolve_commit_and_rerun(self) -> None:
        self.vendor()
        code, _, err, _ = self.sync(failing="subtree")
        self.assertEqual(code, 1)
        self.assertIn("step 1/5 (subtree pull)", err)
        self.assertIn("Resolve the conflict in arbiter/", err)
        self.assertIn("commit the merge", err)

    def test_dry_run_prints_every_command_and_runs_nothing(self) -> None:
        self.vendor()
        code, out, _, calls = self.sync(dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(self.step_names(out), ["subtree pull", "pip install", "record version", "baseline", "doctor"])
        self.assertIn("would run git subtree pull --prefix=arbiter https://github.com/paukennick/arbiter main", out)
        self.assertIn(f"-e './arbiter[{ma.ARBITER_PIP_EXTRAS}]'", out)
        self.assertIn("would re-record", out)
        self.assertIn("arbiter baseline", out)
        self.assertIn("doctor", out)
        self.assertEqual([c for c in calls if c[:1] != ["git"]], [], "only the read-only subtree-mode probe may run")
        self.assertEqual(json.loads(self.version_file.read_text(encoding="utf-8"))["arbiter"]["version"], "0.1.0")

    def test_skip_baseline_and_a_branch_and_source_of_choice(self) -> None:
        self.vendor()
        code, out, _, calls = self.sync(skip_baseline=True, branch="release", source="https://example.invalid/arbiter.git")
        self.assertEqual(code, 0)
        self.assertEqual(self.step_names(out), ["subtree pull", "pip install", "record version", "baseline", "doctor"])
        self.assertIn("baseline: skipped (--skip-baseline)", out)
        pull = calls[1]
        self.assertEqual(pull[4:6], ["https://example.invalid/arbiter.git", "release"])
        self.assertEqual(pull[-1], "Pull Arbiter release into arbiter/")
        self.assertFalse(any("baseline" in c for c in calls))
        self.assertEqual(json.loads(self.version_file.read_text(encoding="utf-8"))["arbiter"]["source"], "https://example.invalid/arbiter.git")

    def test_a_recorded_local_path_inside_the_target_is_not_pulled_from(self) -> None:
        # `omni adopt --with-arbiter ./arbiter` records the subtree's own path; a subtree cannot pull from itself
        self.vendor()
        payload = json.loads(self.version_file.read_text(encoding="utf-8"))
        payload["arbiter"]["source"] = str(self.root / "arbiter")
        self.version_file.write_text(json.dumps(payload), encoding="utf-8")
        _, out, _, calls = self.sync(dry_run=True)
        self.assertIn("https://github.com/paukennick/arbiter main", out)


class TestPipMode(SyncFixture):
    def test_pip_mode_upgrades_from_the_recorded_source_then_records_baselines_and_doctors(self) -> None:
        code, out, err, calls = self.sync()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.step_names(out), ["pip upgrade", "record version", "baseline", "doctor"])
        self.assertIn("pip mode", out)
        pip = calls[0]
        self.assertEqual(pip[:4], [sys.executable, "-m", "pip", "install"])
        self.assertEqual(pip[-1], "--upgrade")
        self.assertIn(f"arbiter-eval[{ma.ARBITER_PIP_EXTRAS}] @ {ma.ARBITER_DEFAULT_SOURCE}", pip)
        self.assertFalse(any(c[:2] == ["git", "subtree"] for c in calls))
        self.assertEqual(json.loads(self.version_file.read_text(encoding="utf-8"))["arbiter"]["version"], "0.2.0")

    def test_a_failing_doctor_names_the_last_step(self) -> None:
        code, out, err, _ = self.sync(failing="doctor")
        self.assertEqual(code, 1)
        self.assertIn("step 4/4 (doctor) failed", err)
        self.assertIn("Fix the errors doctor lists above", err)


class TestParser(unittest.TestCase):
    def test_sync_accepts_its_flags(self) -> None:
        args = ma.build_parser().parse_args(["arbiter", "sync", "--source", "x", "--branch", "dev", "--dry-run", "--skip-baseline"])
        self.assertEqual((args.arbiter_command, args.source, args.branch, args.dry_run, args.skip_baseline), ("sync", "x", "dev", True, True))
        args = ma.build_parser().parse_args(["arbiter", "sync"])
        self.assertEqual((args.source, args.branch, args.dry_run, args.skip_baseline, args.target), (None, "main", False, False, "."))


if __name__ == "__main__":
    unittest.main()
