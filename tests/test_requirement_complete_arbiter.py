"""Tests for `omni requirement complete` refusing to complete without a fresh, passing Arbiter report
(REQ-037). Nothing blocks a workspace without the `completion.arbiter_gate` rule; with it, a missing, stale
or failed report is refused and the message names `./omni gate`, unless `--no-arbiter-check REASON` is given,
in which case the reason lands in the requirement's risk notes. Stdlib only. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_ai as ma  # noqa: E402

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def requirement(req_id: str, status: str = "pending") -> dict:
    return {
        "id": req_id, "category": "Feature", "title": f"{req_id} title", "description": "d", "priority": "low",
        "status": status, "minimum_access_scope": [], "do_not_access": [], "acceptance_criteria": [],
        "validation_required": [], "documentation_required": [], "risk_notes": [],
    }


class CompleteFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._cwd = Path.cwd()
        os.chdir(Path(self._tmp.name).resolve())
        self.addCleanup(os.chdir, self._cwd)
        ma.write_json(ma.REQUIREMENTS_PATH, {"version": "1.0.0", "requirement_id_prefix": "REQ",
                                             "requirements": [requirement("REQ-001")]})
        # The check needs `arbiter` on PATH; the tests decide that, not the machine they run on.
        patcher = mock.patch.object(ma, "arbiter_executable", return_value="/fake/bin/arbiter")
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_rule(self) -> None:
        rule = {
            "id": ma.ARBITER_GATE_RULE_ID, "severity": "required", "statement": "s", "scope": ["completion"],
            "validation": {"type": "command", "run": "arbiter gate . --changed {base} --out arbiter-out/omni-gate",
                           "when_changed": ["**"]},
        }
        ma.write_json(Path(".ai/rules/completion-workflow.json"), {"id": "completion", "rules": [rule]})

    def git_init(self) -> str:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        subprocess.run(["git", "init", "-q"], check=True, env=GIT_ENV, capture_output=True)
        Path("tracked.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], check=True, env=GIT_ENV, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], check=True, env=GIT_ENV, capture_output=True)
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], check=True, env=GIT_ENV, capture_output=True, text=True,
        )
        return head.stdout.strip()

    def write_report(self, commit: str, passed: bool = True, reasons: list | None = None) -> None:
        ma.write_json(Path("arbiter-out/omni-gate/report.json"), {
            "repos": [{"id": "root", "commit": commit}],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "gate": {"passed": passed, "reasons": reasons or []},
            "scorecard": {"overall": 90.0, "coverage": 0.9, "withheld": False},
            "findings": [],
        })

    def complete(self, no_arbiter_check: str | None = None, note: str | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        args = argparse.Namespace(id="REQ-001", note=note, no_failure_entry=None, no_arbiter_check=no_arbiter_check)
        with redirect_stdout(out), redirect_stderr(err):
            code = ma.run_requirement_complete(args)
        return code, out.getvalue(), err.getvalue()

    def stored(self) -> dict:
        return json.loads(ma.REQUIREMENTS_PATH.read_text(encoding="utf-8"))["requirements"][0]


class TestRefusals(CompleteFixture):
    def test_without_the_rule_completion_proceeds(self) -> None:
        code, _, err = self.complete()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.stored()["status"], "completed")

    def test_missing_report_is_refused_and_names_the_gate(self) -> None:
        self.write_rule()
        code, _, err = self.complete()
        self.assertEqual(code, 1)
        self.assertIn("Refusing to complete REQ-001", err)
        self.assertIn("no Arbiter report under arbiter-out/omni-gate", err)
        self.assertIn("./omni gate", err)
        self.assertEqual(self.stored()["status"], "pending")

    def test_arbiter_not_installed_is_refused_first(self) -> None:
        self.write_rule()
        with mock.patch.object(ma, "arbiter_executable", return_value=None):
            code, _, err = self.complete()
        self.assertEqual(code, 1)
        self.assertIn("arbiter is not installed", err)
        self.assertTrue(ma.arbiter_completion_block().endswith("./omni gate"))

    def test_stale_report_is_refused(self) -> None:
        self.git_init()
        self.write_rule()
        self.write_report("0000000")
        code, _, err = self.complete()
        self.assertEqual(code, 1)
        self.assertIn("is stale (scanned 0000000", err)
        self.assertIn("./omni gate", err)
        self.assertEqual(self.stored()["status"], "pending")

    def test_failed_gate_is_refused_with_its_reasons(self) -> None:
        head = self.git_init()
        self.write_rule()
        self.write_report(head, passed=False, reasons=["1 new high finding: secrets.aws-access-key"])
        code, _, err = self.complete()
        self.assertEqual(code, 1)
        self.assertIn("Arbiter gate failed", err)
        self.assertIn("secrets.aws-access-key", err)
        self.assertIn("./omni gate", err)

    def test_fresh_passing_report_completes(self) -> None:
        head = self.git_init()
        self.write_rule()
        self.write_report(head)
        self.assertIsNone(ma.arbiter_completion_block())
        code, _, err = self.complete()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.stored()["status"], "completed")


class TestEscapeHatch(CompleteFixture):
    def test_reason_is_recorded_in_the_risk_notes(self) -> None:
        self.write_rule()
        code, _, err = self.complete(no_arbiter_check="arbiter cannot scan this host", note="other note")
        self.assertEqual(code, 0, err)
        item = self.stored()
        self.assertEqual(item["status"], "completed")
        self.assertEqual(item["risk_notes"], ["arbiter check skipped: arbiter cannot scan this host | other note"])

    def test_too_short_reason_is_rejected(self) -> None:
        self.write_rule()
        code, _, err = self.complete(no_arbiter_check="meh")
        self.assertEqual(code, 1)
        self.assertIn("--no-arbiter-check", err)
        self.assertEqual(self.stored()["status"], "pending")
        self.assertEqual(self.stored()["risk_notes"], [])

    def test_reason_is_not_needed_without_the_rule(self) -> None:
        code, _, _ = self.complete(no_arbiter_check="only matters when the rule is wired")
        self.assertEqual(code, 0)
        self.assertEqual(self.stored()["risk_notes"], [])


if __name__ == "__main__":
    unittest.main()
