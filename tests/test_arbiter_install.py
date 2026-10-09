"""Tests for `omni arbiter install` and `omni adopt --with-arbiter`: Arbiter (the repository evaluator this
workspace pairs with) is installed beside the workspace and wired in through three files. Pip is never run
here (`skip_pip`); the wiring is what these tests own. Stdlib only. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import shutil
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_ai as ma  # noqa: E402


class ArbiterInstallFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".ai" / "rules").mkdir(parents=True)
        self.rulepack = self.root / ".ai" / "rules" / "completion-workflow.json"
        self.rulepack.write_text(json.dumps({
            "rulepack_id": "completion", "version": "1.0.0", "title": "t", "purpose": "p", "applies_to": [],
            "rules": [{"id": "completion.changelog_gate", "severity": "required", "statement": "s"}],
        }), encoding="utf-8")

    def install(self, dry_run: bool = False, which=None, run=None) -> tuple[int, str]:
        """Drive arbiter_install with `arbiter` absent from PATH (so no baseline scan runs) unless a
        `which` stub says otherwise, and with subprocess.run replaced by `run` when one is given."""
        out = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(out))
            stack.enter_context(mock.patch.object(ma.shutil, "which", side_effect=which or (lambda name: None)))
            if run is not None:
                stack.enter_context(mock.patch.object(ma.subprocess, "run", side_effect=run))
            code = ma.arbiter_install(self.root, "../arbiter", skip_pip=True, dry_run=dry_run)
        return code, out.getvalue()

    def rules(self) -> list[dict]:
        return json.loads(self.rulepack.read_text(encoding="utf-8"))["rules"]


class TestWiring(ArbiterInstallFixture):
    def test_writes_the_three_wiring_files(self) -> None:
        code, _ = self.install()
        self.assertEqual(code, 0)
        mcp = json.loads((self.root / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(mcp["mcpServers"]["arbiter"], {"command": "arbiter", "args": ["mcp"]})
        gate = [r for r in self.rules() if r["id"] == ma.ARBITER_GATE_RULE_ID]
        self.assertEqual(len(gate), 1)
        self.assertEqual(gate[0]["validation"]["type"], "command")
        self.assertIn("arbiter gate . --changed {base}", gate[0]["validation"]["run"])
        self.assertTrue((self.root / "arbiter.yaml").is_file())
        self.assertIn("arbiter-out/", (self.root / ".gitignore").read_text(encoding="utf-8"))

    def test_rerunning_changes_nothing(self) -> None:
        self.install()
        before = {p: (self.root / p).read_text(encoding="utf-8") for p in (".mcp.json", "arbiter.yaml", ".gitignore")}
        before_rules = self.rules()
        code, output = self.install()
        self.assertEqual(code, 0)
        self.assertIn("already", output)
        for p, text in before.items():
            self.assertEqual((self.root / p).read_text(encoding="utf-8"), text)
        self.assertEqual(self.rules(), before_rules)

    def test_existing_servers_and_policy_are_kept(self) -> None:
        (self.root / ".mcp.json").write_text(json.dumps({"mcpServers": {"omni": {"command": "python", "args": ["omni", "mcp", "serve"]}}}), encoding="utf-8")
        (self.root / "arbiter.yaml").write_text("version: 1\nprofile: ci\n", encoding="utf-8")
        self.install()
        mcp = json.loads((self.root / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(set(mcp["mcpServers"]), {"omni", "arbiter"})
        self.assertEqual((self.root / "arbiter.yaml").read_text(encoding="utf-8"), "version: 1\nprofile: ci\n")

    def test_dry_run_writes_nothing(self) -> None:
        code, output = self.install(dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("would", output)
        self.assertFalse((self.root / ".mcp.json").exists())
        self.assertFalse((self.root / "arbiter.yaml").exists())
        self.assertEqual(len(self.rules()), 1)

    def test_a_missing_rulepack_is_reported(self) -> None:
        self.rulepack.unlink()
        code, output = self.install()
        self.assertEqual(code, 1)
        self.assertIn("adopt the workspace first", output)


class TestPipCommand(unittest.TestCase):
    def test_local_checkout_installs_editable_with_the_mcp_extra(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
            command = ma._arbiter_pip_command(tmp)
        self.assertEqual(command[-2], "-e")
        self.assertTrue(command[-1].endswith(f"[{ma.ARBITER_PIP_EXTRAS}]"))

    def test_git_source_installs_the_named_distribution(self) -> None:
        command = ma._arbiter_pip_command(ma.ARBITER_DEFAULT_SOURCE)
        self.assertEqual(command[-1], f"arbiter-eval[{ma.ARBITER_PIP_EXTRAS}] @ {ma.ARBITER_DEFAULT_SOURCE}")


class TestAdoptFlag(unittest.TestCase):
    def test_adopt_accepts_with_arbiter_with_and_without_a_source(self) -> None:
        parser = ma.build_parser()
        args = parser.parse_args(["adopt", "--target", "x", "--with-arbiter"])
        self.assertEqual(args.with_arbiter, ma.ARBITER_DEFAULT_SOURCE)
        args = parser.parse_args(["adopt", "--target", "x", "--with-arbiter", "../arbiter", "--skip-pip"])
        self.assertEqual(args.with_arbiter, "../arbiter")
        self.assertTrue(args.skip_pip)
        self.assertIsNone(parser.parse_args(["adopt", "--target", "x"]).with_arbiter)


# ---------------------------------------------------------------------------
# REQ-038: the baseline. Nothing real runs: subprocess.run and shutil.which are
# stubbed, and the stub writes the files `arbiter scan` / `arbiter baseline`
# would, so the code under test sees a real-looking filesystem.
# ---------------------------------------------------------------------------

class FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def fake_arbiter(root: Path, mode: str = "full", ids: tuple[str, ...] = ("f:aaa", "f:bbb")):
    """A subprocess.run stand-in for `arbiter scan`, `arbiter baseline` and the git calls the
    baseline code makes. Returns (callable, calls) so tests can assert the exact argv lists."""
    calls: list[list[str]] = []

    def run(command, **kwargs):
        command = list(command)
        calls.append(command)
        if command[:2] == ["arbiter", "scan"]:
            out = root / command[command.index("--out") + 1]
            out.mkdir(parents=True, exist_ok=True)
            (out / "report.json").write_text(json.dumps({
                "scan_scope": {"mode": mode}, "gate": {"passed": True},
                "findings": [{"id": i, "severity": "low", "status": "new", "suppressed": False} for i in ids],
            }), encoding="utf-8")
            return FakeCompleted()
        if command[:2] == ["arbiter", "baseline"]:
            report = json.loads((root / command[2]).read_text(encoding="utf-8"))
            target = root / command[command.index("--out") + 1]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({
                "schema_version": "1.0", "created": "2026-10-09T00:00:00+00:00", "system": "root",
                "ids": [f["id"] for f in report["findings"]],
            }), encoding="utf-8")
            return FakeCompleted(stdout="  baseline written\n")
        if command[:1] == ["git"] and "rev-parse" in command:
            return FakeCompleted(stdout="abc1234\n")
        if command[:1] == ["git"] and "check-ignore" in command:
            return FakeCompleted(returncode=1)
        return FakeCompleted()

    return run, calls


def arbiter_on_path(name: str):
    return "/usr/local/bin/arbiter" if name == "arbiter" else None


class TestGateRuleConstant(ArbiterInstallFixture):
    def test_the_rule_gates_against_the_baseline_in_three_formats(self) -> None:
        run = ma.ARBITER_GATE_RULE["validation"]["run"]
        self.assertIn("--baseline .arbiter/baseline.json", run)
        self.assertIn("--format json,sarif,pr-comment", run)
        self.assertTrue(run.startswith("arbiter gate . --changed {base} --profile offline"))
        self.assertEqual(ma.ARBITER_GATE_RULE["id"], ma.ARBITER_GATE_RULE_ID)

    def test_install_writes_the_constant_and_leaves_it_pristine(self) -> None:
        self.install()
        gate = [r for r in self.rules() if r["id"] == ma.ARBITER_GATE_RULE_ID]
        self.assertEqual(gate, [ma.ARBITER_GATE_RULE])
        gate[0]["validation"]["run"] = "changed"
        self.assertNotEqual(ma.ARBITER_GATE_RULE["validation"]["run"], "changed")

    def test_only_scan_output_and_the_cache_are_ignored(self) -> None:
        self.install()
        lines = (self.root / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("arbiter-out/", lines)
        self.assertIn(".arbiter/cache.json", lines)
        self.assertNotIn(".arbiter/", lines)
        self.assertNotIn(".arbiter", lines)

    def test_an_existing_ignore_gains_only_the_missing_line(self) -> None:
        (self.root / ".gitignore").write_text("arbiter-out/\n", encoding="utf-8")
        self.install()
        text = (self.root / ".gitignore").read_text(encoding="utf-8")
        self.assertEqual(text.count("arbiter-out/"), 1)
        self.assertIn(".arbiter/cache.json", text)


class TestBaselineAtInstall(ArbiterInstallFixture):
    def test_install_cuts_the_baseline_when_arbiter_is_on_path(self) -> None:
        run, calls = fake_arbiter(self.root)
        code, output = self.install(which=arbiter_on_path, run=run)
        self.assertEqual(code, 0)
        baseline = json.loads((self.root / ".arbiter" / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(baseline["ids"], ["f:aaa", "f:bbb"])
        self.assertEqual(baseline["commit"], "abc1234")
        self.assertIn("commit .arbiter/baseline.json so the gate means new since this baseline", output)
        self.assertIn(["arbiter", "scan", ".", "--profile", "offline", "--out", "arbiter-out/baseline", "--format", "json"], calls)
        self.assertIn(["arbiter", "baseline", "arbiter-out/baseline/report.json", "--out", ".arbiter/baseline.json"], calls)

    def test_install_skips_the_baseline_when_arbiter_is_absent(self) -> None:
        run, calls = fake_arbiter(self.root)
        code, output = self.install(run=run)
        self.assertEqual(code, 0)
        self.assertFalse((self.root / ".arbiter").exists())
        self.assertIn("skipped", output)
        self.assertIn("omni arbiter baseline", output)
        self.assertFalse(any(c[:1] == ["arbiter"] for c in calls))

    def test_dry_run_only_announces_the_baseline(self) -> None:
        run, calls = fake_arbiter(self.root)
        code, output = self.install(dry_run=True, which=arbiter_on_path, run=run)
        self.assertEqual(code, 0)
        self.assertIn("would cut a baseline", output)
        self.assertEqual(calls, [])


class TestWriteBaseline(ArbiterInstallFixture):
    def baseline(self, **kwargs) -> tuple[int, str, list[list[str]]]:
        run, calls = fake_arbiter(self.root, mode=kwargs.pop("mode", "full"), ids=kwargs.pop("ids", ("f:aaa", "f:bbb")))
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(ma.shutil, "which", side_effect=arbiter_on_path), \
                mock.patch.object(ma.subprocess, "run", side_effect=run):
            code = ma.arbiter_write_baseline(self.root, **kwargs)
        return code, out.getvalue(), calls

    def write_gate_report(self, passed: bool) -> None:
        out = self.root / "arbiter-out" / "omni-gate"
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json.dumps({"scan_scope": {"mode": "partial"}, "gate": {"passed": passed}, "findings": []}), encoding="utf-8")

    def test_present_baseline_is_left_alone(self) -> None:
        (self.root / ".arbiter").mkdir()
        (self.root / ".arbiter" / "baseline.json").write_text('{"ids": ["old"]}', encoding="utf-8")
        code, output, calls = self.baseline()
        self.assertEqual(code, 0)
        self.assertIn("already present", output)
        self.assertEqual(calls, [])
        self.assertEqual(json.loads((self.root / ".arbiter" / "baseline.json").read_text(encoding="utf-8")), {"ids": ["old"]})

    def test_a_partial_report_is_refused(self) -> None:
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            code, _, calls = self.baseline(mode="partial")
        self.assertEqual(code, 1)
        self.assertIn("scan_scope.mode", err.getvalue())
        self.assertFalse((self.root / ".arbiter" / "baseline.json").exists())
        self.assertFalse(any(c[:2] == ["arbiter", "baseline"] for c in calls))

    def test_the_baseline_is_augmented_with_commit_and_config_hash(self) -> None:
        (self.root / "arbiter.yaml").write_text("version: 1\n", encoding="utf-8")
        code, _, _ = self.baseline()
        self.assertEqual(code, 0)
        baseline = json.loads((self.root / ".arbiter" / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(baseline["commit"], "abc1234")
        self.assertEqual(baseline["config_hash"], ma._arbiter_config_hash(self.root))
        self.assertEqual(baseline["schema_version"], "1.0")
        self.assertEqual(baseline["ids"], ["f:aaa", "f:bbb"])

    def test_refresh_refuses_on_a_red_last_report_and_proceeds_with_force(self) -> None:
        (self.root / ".arbiter").mkdir()
        (self.root / ".arbiter" / "baseline.json").write_text('{"ids": ["f:aaa", "f:gone"]}', encoding="utf-8")
        self.write_gate_report(passed=False)
        code, output, calls = self.baseline(refresh=True)
        self.assertEqual(code, 1)
        self.assertIn("get the gate green first", output)
        self.assertEqual(calls, [])
        code, output, calls = self.baseline(refresh=True, force=True, ids=("f:aaa", "f:new"))
        self.assertEqual(code, 0)
        self.assertIn("--force", output)
        baseline = json.loads((self.root / ".arbiter" / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(baseline["ids"], ["f:aaa", "f:new"], "pruned: only ids the fresh full scan reports")

    def test_refresh_refuses_without_any_gate_report(self) -> None:
        (self.root / ".arbiter").mkdir()
        (self.root / ".arbiter" / "baseline.json").write_text('{"ids": []}', encoding="utf-8")
        code, output, calls = self.baseline(refresh=True)
        self.assertEqual(code, 1)
        self.assertIn("is missing", output)
        self.assertEqual(calls, [])

    def test_refresh_proceeds_on_a_green_last_report(self) -> None:
        (self.root / ".arbiter").mkdir()
        (self.root / ".arbiter" / "baseline.json").write_text('{"ids": ["f:gone"]}', encoding="utf-8")
        self.write_gate_report(passed=True)
        code, output, _ = self.baseline(refresh=True)
        self.assertEqual(code, 0)
        self.assertIn("refreshed", output)
        self.assertEqual(json.loads((self.root / ".arbiter" / "baseline.json").read_text(encoding="utf-8"))["ids"], ["f:aaa", "f:bbb"])

    def test_a_failed_scan_is_reported(self) -> None:
        def run(command, **kwargs):
            return FakeCompleted(returncode=2, stderr="boom\nno such probe")
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), mock.patch.object(sys, "stderr", err), \
                mock.patch.object(ma.shutil, "which", side_effect=arbiter_on_path), \
                mock.patch.object(ma.subprocess, "run", side_effect=run):
            code = ma.arbiter_write_baseline(self.root)
        self.assertEqual(code, 1)
        self.assertIn("no such probe", err.getvalue())

    def test_the_cli_subcommand_parses(self) -> None:
        args = ma.build_parser().parse_args(["arbiter", "baseline", "--target", "x", "--refresh", "--force"])
        self.assertEqual((args.arbiter_command, args.target, args.refresh, args.force), ("baseline", "x", True, True))


class DoctorFixture(unittest.TestCase):
    """A tmp cwd (the doctor validators read relative paths) with a completion rulepack."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self._cwd)
        (self.root / ".ai" / "rules").mkdir(parents=True)
        (self.root / ".ai" / "failures").mkdir(parents=True)
        self.write_rulepack(with_gate=True)

    def write_rulepack(self, with_gate: bool) -> None:
        rules = [{"id": "completion.changelog_gate", "severity": "required", "statement": "s"}]
        if with_gate:
            rules.append(json.loads(json.dumps(ma.ARBITER_GATE_RULE)))
        (self.root / ".ai" / "rules" / "completion-workflow.json").write_text(json.dumps({
            "rulepack_id": "completion", "version": "1.0.0", "title": "t", "purpose": "p", "applies_to": [], "rules": rules,
        }), encoding="utf-8")

    def write_baseline(self, **extra) -> None:
        (self.root / ".arbiter").mkdir(exist_ok=True)
        payload = {"schema_version": "1.0", "created": "2026-10-01T00:00:00+00:00", "system": "root", "ids": ["f:aaa"]}
        payload.update(extra)
        (self.root / ".arbiter" / "baseline.json").write_text(json.dumps(payload), encoding="utf-8")

    def write_ledger(self, *entries: tuple[str, str, str]) -> None:
        (self.root / ".ai" / "failures" / "failure-ledger.json").write_text(json.dumps({
            "version": "1.0.0", "failure_id_prefix": "FAIL",
            "failures": [{"id": i, "date": d, "status": s, "title": "t"} for i, d, s in entries],
        }), encoding="utf-8")


class TestDoctorBaseline(DoctorFixture):
    def check(self) -> ma.DoctorReport:
        report = ma.DoctorReport()
        ma.validate_arbiter_baseline(report)
        return report

    def test_a_missing_baseline_warns(self) -> None:
        report = self.check()
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("treats every finding as new", report.warnings[0])
        self.assertIn("omni arbiter baseline", report.warnings[0])

    def test_a_baseline_older_than_the_newest_fix_warns_naming_both(self) -> None:
        self.write_baseline(created="2026-10-01T00:00:00+00:00")
        self.write_ledger(("FAIL-001", "2026-09-20", "fixed"), ("FAIL-002", "2026-10-05", "fixed"), ("FAIL-003", "2026-10-08", "open"))
        report = self.check()
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("2026-10-01", report.warnings[0])
        self.assertIn("FAIL-002", report.warnings[0])
        self.assertIn("2026-10-05", report.warnings[0])
        self.assertNotIn("FAIL-003", report.warnings[0])

    def test_a_changed_policy_warns(self) -> None:
        (self.root / "arbiter.yaml").write_text("version: 1\n", encoding="utf-8")
        self.write_baseline(config_hash="0" * 64)
        report = self.check()
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("arbiter.yaml changed", report.warnings[0])

    def test_a_current_baseline_passes(self) -> None:
        (self.root / "arbiter.yaml").write_text("version: 1\n", encoding="utf-8")
        self.write_baseline(created="2026-10-09T00:00:00+00:00", config_hash=ma._arbiter_config_hash(self.root))
        self.write_ledger(("FAIL-001", "2026-10-05", "fixed"))
        report = self.check()
        self.assertEqual(report.warnings, [])
        self.assertEqual(len(report.passed), 1)

    def test_silent_without_the_rule(self) -> None:
        self.write_rulepack(with_gate=False)
        report = self.check()
        self.assertEqual((report.warnings, report.passed, report.errors), ([], [], []))

    def test_build_doctor_report_calls_it(self) -> None:
        source = Path(ma.__file__).read_text(encoding="utf-8")
        body = source.split("def build_doctor_report(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("validate_arbiter_baseline(report)", body)


@unittest.skipUnless(shutil.which("arbiter") and shutil.which("git"), "needs the real arbiter CLI and git")
class TestRealBaseline(unittest.TestCase):
    """Baselines a tiny repository for real (native probes only, so it takes a second)."""

    def test_a_tiny_repo_gets_a_baseline_with_ids_commit_and_hash(self) -> None:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A credential-shaped literal (the documented AWS example key), assembled here so
            # this file itself never carries one; the scanned repo does.
            planted = "AKIA" + "IOSFODNN7" + "EXAMPLE"
            (root / "config.py").write_text(f'AWS_KEY = "{planted}"\n', encoding="utf-8")
            (root / "arbiter.yaml").write_text("version: 1\nprofile: offline\n", encoding="utf-8")
            for command in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "commit", "-qm", "init"]):
                subprocess.run(command, cwd=root, env=env, check=True, capture_output=True)
            out = io.StringIO()
            with redirect_stdout(out):
                code = ma.arbiter_write_baseline(root, scan_args=("--no-adapters",))
            self.assertEqual(code, 0, out.getvalue())
            baseline = json.loads((root / ".arbiter" / "baseline.json").read_text(encoding="utf-8"))
            self.assertIsInstance(baseline["ids"], list)
            self.assertTrue(baseline["ids"], "the planted credential should yield at least one finding id")
            self.assertTrue(all(isinstance(i, str) for i in baseline["ids"]))
            self.assertEqual(baseline["commit"], ma._git_short_head(root))
            self.assertEqual(baseline["config_hash"], ma._arbiter_config_hash(root))
            self.assertIn("created", baseline)
            self.assertIn("commit .arbiter/baseline.json", out.getvalue())


# ---------------------------------------------------------------------------
# REQ-042: version tracking and `omni arbiter update`.
# ---------------------------------------------------------------------------

class TestInstalledVersion(unittest.TestCase):
    def test_parses_the_last_version_token_of_arbiter_version(self) -> None:
        def run(command, **kwargs):
            self.assertEqual(list(command), ["arbiter", "--version"])
            return FakeCompleted(stdout="arbiter 0.1.0\n")
        with mock.patch.object(ma.shutil, "which", side_effect=arbiter_on_path), mock.patch.object(ma.subprocess, "run", side_effect=run):
            self.assertEqual(ma.installed_arbiter_version(), "0.1.0")

    def test_falls_back_to_distribution_metadata(self) -> None:
        import importlib.metadata as metadata
        with mock.patch.object(ma.shutil, "which", side_effect=lambda name: None), \
                mock.patch.object(metadata, "version", return_value="0.2.0") as version:
            self.assertEqual(ma.installed_arbiter_version(), "0.2.0")
        version.assert_called_once_with("arbiter-eval")

    def test_none_when_neither_works(self) -> None:
        import importlib.metadata as metadata
        with mock.patch.object(ma.shutil, "which", side_effect=lambda name: None), \
                mock.patch.object(metadata, "version", side_effect=metadata.PackageNotFoundError("arbiter-eval")):
            self.assertIsNone(ma.installed_arbiter_version())


class VersionFileFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".ai" / "rules").mkdir(parents=True)
        self.version_file = self.root / ma.OMNI_VERSION_FILE

    def write_version(self, **extra) -> None:
        payload = {"source": "/src/OmniEngineering", "ref": "deadbeef", "last_synced_at": "2026-10-01T00:00:00+00:00"}
        payload.update(extra)
        self.version_file.write_text(json.dumps(payload), encoding="utf-8")

    def read_version(self) -> dict:
        return json.loads(self.version_file.read_text(encoding="utf-8"))


class TestRecordVersion(VersionFileFixture):
    def test_merges_the_arbiter_block_and_keeps_the_rest(self) -> None:
        self.write_version()
        with mock.patch.object(ma, "installed_arbiter_version", return_value="0.1.0"), redirect_stdout(io.StringIO()):
            ma.record_arbiter_version(self.root, "../arbiter")
        payload = self.read_version()
        self.assertEqual(payload["ref"], "deadbeef")
        self.assertEqual(payload["arbiter"]["source"], "../arbiter")
        self.assertEqual(payload["arbiter"]["version"], "0.1.0")
        self.assertIn("recorded_at", payload["arbiter"])

    def test_records_unknown_when_no_version_is_found(self) -> None:
        self.write_version()
        with mock.patch.object(ma, "installed_arbiter_version", return_value=None), redirect_stdout(io.StringIO()):
            ma.record_arbiter_version(self.root, "x")
        self.assertEqual(self.read_version()["arbiter"]["version"], "unknown")

    def test_skips_with_a_note_when_the_file_is_absent(self) -> None:
        out = io.StringIO()
        with mock.patch.object(ma, "installed_arbiter_version", return_value="0.1.0"), redirect_stdout(out):
            ma.record_arbiter_version(self.root, "x")
        self.assertFalse(self.version_file.exists())
        self.assertIn("not recorded", out.getvalue())

    def test_install_records_the_version_even_with_skip_pip(self) -> None:
        self.write_version()
        (self.root / ".ai" / "rules" / "completion-workflow.json").write_text(json.dumps({"rules": []}), encoding="utf-8")
        with mock.patch.object(ma, "installed_arbiter_version", return_value="0.1.0"), \
                mock.patch.object(ma.shutil, "which", side_effect=lambda name: None), redirect_stdout(io.StringIO()):
            ma.arbiter_install(self.root, "../arbiter", skip_pip=True)
        recorded = self.read_version()["arbiter"]
        self.assertEqual((recorded["source"], recorded["version"]), ("../arbiter", "0.1.0"))

    def test_write_omni_version_file_preserves_the_arbiter_key(self) -> None:
        self.write_version(arbiter={"source": "s", "version": "0.1.0", "recorded_at": "t"})
        with mock.patch.object(ma, "git_current_ref", return_value="cafebabe"):
            ma.write_omni_version_file(Path("/src/Omni"), self.root)
        payload = self.read_version()
        self.assertEqual(payload["ref"], "cafebabe")
        self.assertEqual(payload["source"], str(Path("/src/Omni")))
        self.assertEqual(payload["arbiter"], {"source": "s", "version": "0.1.0", "recorded_at": "t"})


class TestDoctorVersion(DoctorFixture):
    def check(self, installed) -> ma.DoctorReport:
        report = ma.DoctorReport()
        with mock.patch.object(ma.shutil, "which", side_effect=arbiter_on_path), \
                mock.patch.object(ma, "installed_arbiter_version", return_value=installed):
            ma.validate_arbiter_version(report)
        return report

    def write_version(self, **extra) -> None:
        payload = {"source": "s", "ref": "r"}
        payload.update(extra)
        (self.root / ma.OMNI_VERSION_FILE).write_text(json.dumps(payload), encoding="utf-8")

    def test_warns_on_a_mismatch(self) -> None:
        self.write_version(arbiter={"source": "s", "version": "0.1.0"})
        report = self.check("0.2.0")
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("recorded 0.1.0, installed 0.2.0", report.warnings[0])
        self.assertIn("omni arbiter update", report.warnings[0])

    def test_silent_when_equal(self) -> None:
        self.write_version(arbiter={"source": "s", "version": "0.1.0"})
        self.assertEqual(self.check("0.1.0").warnings, [])

    def test_silent_without_a_record_or_an_install(self) -> None:
        self.write_version()
        report = self.check("0.1.0")
        self.assertEqual((report.warnings, report.passed), ([], []))
        self.write_version(arbiter={"source": "s", "version": "0.1.0"})
        report = ma.DoctorReport()
        with mock.patch.object(ma.shutil, "which", side_effect=lambda name: None):
            ma.validate_arbiter_version(report)
        self.assertEqual((report.warnings, report.passed), ([], []))

    def test_build_doctor_report_calls_it(self) -> None:
        source = Path(ma.__file__).read_text(encoding="utf-8")
        body = source.split("def build_doctor_report(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("validate_arbiter_version(report)", body)


RECORDED_SOURCE = "https://example.invalid/arbiter"  # a URL, so `git subtree pull` gets it verbatim
SUBTREE_UNSQUASHED = "Add 'arbiter/' from commit 'd10b'\n\ngit-subtree-dir: arbiter\ngit-subtree-mainline: 1d93\ngit-subtree-split: d10b\n"
SUBTREE_SQUASHED = "Squashed 'arbiter/' changes from 5c21..033b\n\n033b lib4\n\ngit-subtree-dir: arbiter\ngit-subtree-split: 033b\n"


class TestArbiterUpdate(VersionFileFixture):
    def setUp(self) -> None:
        super().setUp()
        self.rulepack = self.root / ".ai" / "rules" / "completion-workflow.json"
        stale = json.loads(json.dumps(ma.ARBITER_GATE_RULE))
        stale["validation"]["run"] = "arbiter gate . --changed {base} --profile offline --out arbiter-out/omni-gate --format json"
        self.rulepack.write_text(json.dumps({"rulepack_id": "completion", "rules": [stale]}), encoding="utf-8")
        self.write_version(arbiter={"source": RECORDED_SOURCE, "version": "0.1.0", "recorded_at": "t"})

    def vendor(self) -> None:
        (self.root / "arbiter" / ".ai").mkdir(parents=True)
        (self.root / "arbiter" / "pyproject.toml").write_text("[project]\nname='arbiter-eval'\n", encoding="utf-8")
        (self.root / "arbiter" / ".ai" / "omni-version.json").write_text("{}", encoding="utf-8")

    def update(self, *flags: str, subtree_log: str = "") -> tuple[int, str, list[list[str]]]:
        calls: list[list[str]] = []

        def run(command, **kwargs):
            command = list(command)
            calls.append(command)
            if command[:1] == ["git"] and "log" in command:
                return FakeCompleted(stdout=subtree_log)
            return FakeCompleted()

        args = ma.build_parser().parse_args(["arbiter", "update", "--target", str(self.root), *flags])
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(ma.subprocess, "run", side_effect=run), \
                mock.patch.object(ma, "installed_arbiter_version", return_value="0.3.0"), \
                mock.patch.object(ma.shutil, "which", side_effect=arbiter_on_path):
            code = ma.run_arbiter_update(args)
        return code, out.getvalue(), calls

    def rule_run(self) -> str:
        rules = json.loads(self.rulepack.read_text(encoding="utf-8"))["rules"]
        return rules[0]["validation"]["run"]

    def test_pip_upgrades_from_the_recorded_source_and_the_version_is_re_recorded(self) -> None:
        code, output, calls = self.update()
        self.assertEqual(code, 0)
        self.assertIn(ma._arbiter_pip_command(RECORDED_SOURCE) + ["--upgrade"], calls)
        self.assertEqual(self.read_version()["arbiter"]["version"], "0.3.0")
        self.assertEqual(self.read_version()["ref"], "deadbeef")
        self.assertIn("omni arbiter baseline --refresh", output)
        self.assertNotIn("subtree pull", " ".join(" ".join(c) for c in calls))

    def test_source_flag_wins_and_skip_pip_runs_no_pip(self) -> None:
        code, _, calls = self.update("--source", "git+https://example.invalid/other", "--skip-pip")
        self.assertEqual(code, 0)
        self.assertFalse(any("pip" in c for c in calls))
        self.assertEqual(self.read_version()["arbiter"]["source"], "git+https://example.invalid/other")

    def test_the_rule_is_rewritten_unless_kept(self) -> None:
        self.update("--skip-pip")
        self.assertEqual(self.rule_run(), ma.ARBITER_GATE_RULE["validation"]["run"])
        rules = json.loads(self.rulepack.read_text(encoding="utf-8"))["rules"]
        self.assertEqual(rules[0]["validation"], ma.ARBITER_GATE_RULE["validation"])

    def test_keep_rule_leaves_it(self) -> None:
        before = self.rule_run()
        code, output, _ = self.update("--skip-pip", "--keep-rule")
        self.assertEqual(code, 0)
        self.assertEqual(self.rule_run(), before)
        self.assertIn("--keep-rule", output)

    def test_subtree_pull_runs_in_the_mode_the_history_was_added_with(self) -> None:
        self.vendor()
        code, _, calls = self.update("--skip-pip", subtree_log=SUBTREE_UNSQUASHED)
        self.assertEqual(code, 0)
        self.assertIn(["git", "subtree", "pull", "--prefix", "arbiter", RECORDED_SOURCE, "main"], calls)
        code, _, calls = self.update("--skip-pip", "--squash", subtree_log=SUBTREE_SQUASHED)
        self.assertEqual(code, 0)
        self.assertIn(["git", "subtree", "pull", "--prefix", "arbiter", RECORDED_SOURCE, "main", "--squash"], calls)

    def test_git_plus_sources_are_stripped_and_checkouts_resolved_for_git(self) -> None:
        self.vendor()
        _, _, calls = self.update("--skip-pip", "--source", "git+https://example.invalid/other", subtree_log=SUBTREE_UNSQUASHED)
        self.assertIn(["git", "subtree", "pull", "--prefix", "arbiter", "https://example.invalid/other", "main"], calls)
        with tempfile.TemporaryDirectory() as checkout:
            self.assertEqual(ma._arbiter_git_source(checkout), str(Path(checkout).resolve()))
        self.assertEqual(ma._arbiter_git_source("arbiter-eval"), "arbiter-eval")

    def test_a_mode_mismatch_is_refused_unless_forced(self) -> None:
        self.vendor()
        code, output, calls = self.update("--skip-pip", "--squash", subtree_log=SUBTREE_UNSQUASHED)
        self.assertEqual(code, 1)
        self.assertIn("history is unsquashed", output)
        self.assertIn(f"git subtree pull --prefix arbiter {RECORDED_SOURCE} main --squash", output)
        self.assertFalse(any("subtree" in c for c in calls))
        code, output, calls = self.update("--skip-pip", subtree_log=SUBTREE_SQUASHED)
        self.assertEqual(code, 1)
        self.assertIn("history is squash", output)
        code, _, calls = self.update("--skip-pip", "--squash", "--force", subtree_log=SUBTREE_UNSQUASHED)
        self.assertEqual(code, 0)
        self.assertIn(["git", "subtree", "pull", "--prefix", "arbiter", RECORDED_SOURCE, "main", "--squash"], calls)

    def test_subtree_mode_reads_the_marker_commit(self) -> None:
        for log, expected in ((SUBTREE_UNSQUASHED, "unsquashed"), (SUBTREE_SQUASHED, "squash"), ("", None), ("Merge commit 'x'\n", None)):
            with mock.patch.object(ma.subprocess, "run", return_value=FakeCompleted(stdout=log)) as run:
                self.assertEqual(ma.arbiter_subtree_mode(self.root), expected)
            self.assertEqual(run.call_args[0][0][-3:], ["-1", "--grep=^git-subtree-dir: arbiter$", "--format=%B"])

    def test_dry_run_runs_and_writes_nothing(self) -> None:
        self.vendor()
        before_rule = self.rule_run()
        before_version = self.read_version()
        code, output, calls = self.update("--dry-run", subtree_log=SUBTREE_UNSQUASHED)
        self.assertEqual(code, 0)
        self.assertEqual([c for c in calls if "log" not in c], [], "only the read-only subtree mode probe ran")
        self.assertIn("would run", output)
        self.assertIn("pip install", output)
        self.assertIn("git subtree pull --prefix arbiter", output)
        self.assertEqual(self.rule_run(), before_rule)
        self.assertEqual(self.read_version(), before_version)

    def test_the_cli_subcommand_parses(self) -> None:
        args = ma.build_parser().parse_args(["arbiter", "update", "--keep-rule", "--squash", "--force", "--skip-pip", "--dry-run"])
        self.assertEqual(args.arbiter_command, "update")
        self.assertTrue(args.keep_rule and args.squash and args.force and args.skip_pip and args.dry_run)
        self.assertIsNone(args.source)


class TestUpdateWarnsOnRuleDrift(VersionFileFixture):
    def test_warns_only_when_the_run_text_differs(self) -> None:
        rulepack = self.root / ".ai" / "rules" / "completion-workflow.json"
        current = json.loads(json.dumps(ma.ARBITER_GATE_RULE))
        rulepack.write_text(json.dumps({"rules": [current]}), encoding="utf-8")
        with redirect_stdout(io.StringIO()) as out:
            self.assertFalse(ma.warn_arbiter_rule_drift(self.root))
        self.assertEqual(out.getvalue(), "")
        current["validation"]["run"] = "arbiter gate . --changed {base}"
        rulepack.write_text(json.dumps({"rules": [current]}), encoding="utf-8")
        with redirect_stdout(io.StringIO()) as out:
            self.assertTrue(ma.warn_arbiter_rule_drift(self.root))
        self.assertIn("omni arbiter update", out.getvalue())
        self.assertIn("left alone", out.getvalue())
        self.assertEqual(ma.ARBITER_GATE_RULE["validation"]["run"].count("--baseline"), 1, "the constant is untouched")

    def test_run_update_calls_it(self) -> None:
        source = Path(ma.__file__).read_text(encoding="utf-8")
        body = source.split("def run_update(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("warn_arbiter_rule_drift(target_root)", body)


if __name__ == "__main__":
    unittest.main()
