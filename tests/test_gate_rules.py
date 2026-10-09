"""Tests for the gate's executable validation types: `co_changed` (pre-existing), and the two this session
adds real behaviour to -- `requirement_registry_entry` (previously declared on a rule but never executed:
`gate_rules()` only ever picked up `co_changed`) and `content_forbidden` (a new, honest, bounded safety net
for a handful of common accidental leaks).

Stdlib only. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_ai as ma  # noqa: E402


class GateRuleFixture(unittest.TestCase):
    """A small git repo with one rulepack file, swapped in for RULEPACK_FILES so the real rulesets never
    leak into these tests (and these tests never touch the real .ai/rules/*.json)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        self.git("init", "-q")
        self._cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self._cwd)
        self.rulepack_path = Path(".ai/rules/test-rules.json")
        self._patcher = mock.patch.object(ma, "RULEPACK_FILES", [str(self.rulepack_path)])
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True, env=self._env)
        return result.stdout

    def write_rulepack(self, rules: list[dict]) -> None:
        self.rulepack_path.parent.mkdir(parents=True, exist_ok=True)
        self.rulepack_path.write_text(json.dumps({
            "rulepack_id": "test_rules", "version": "1.0.0", "title": "t", "purpose": "p", "applies_to": [], "rules": rules,
        }), encoding="utf-8")

    def write_requirements(self, ids: list[str]) -> None:
        path = Path(".ai/requirements/requirements.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": "1.0.0", "requirement_id_prefix": "REQ",
            "requirements": [{"id": i, "category": "Feature", "title": i, "description": "d", "priority": "low",
                               "status": "completed", "minimum_access_scope": [], "acceptance_criteria": [],
                               "validation_required": [], "documentation_required": [], "risk_notes": []} for i in ids],
        }), encoding="utf-8")
        old = ma.REQUIREMENTS_PATH
        ma.REQUIREMENTS_PATH = path
        self.addCleanup(setattr, ma, "REQUIREMENTS_PATH", old)


class TestGateRulesSelection(GateRuleFixture):
    def test_an_unrecognised_validation_type_is_declared_but_not_selected(self) -> None:
        # this is the exact shape the bug had: a validation object present, but of a type gate_rules()
        # did not recognise, so it was silently never executed
        self.write_rulepack([{"id": "x.made_up_type", "severity": "required", "statement": "s", "validation": {"type": "not_a_real_type"}}])
        self.assertEqual(ma.gate_rules(), [])

    def test_a_recognised_type_is_selected_only_when_required(self) -> None:
        self.write_rulepack([
            {"id": "x.required", "severity": "required", "statement": "s", "validation": {"type": "co_changed"}},
            {"id": "x.recommended", "severity": "recommended", "statement": "s", "validation": {"type": "co_changed"}},
        ])
        self.assertEqual([r["id"] for r in ma.gate_rules()], ["x.required"])

    def test_every_declared_type_this_module_implements_is_in_the_selection_set(self) -> None:
        # if a new elif branch is added to gate_evaluate() without adding the type here, gate_rules()
        # silently drops it again -- the same bug, reintroduced
        for vtype in ("co_changed", "requirement_registry_entry", "content_forbidden", "command"):
            self.assertIn(vtype, ma.EXECUTABLE_VALIDATION_TYPES)


class TestRequirementRegistryEntry(GateRuleFixture):
    def rule(self) -> dict:
        return {"id": "x.req_entry", "severity": "required", "statement": "s",
                "validation": {"type": "requirement_registry_entry", "target": ".ai/requirements/requirements.json"}}

    def test_a_commit_citing_a_known_requirement_passes(self) -> None:
        self.write_rulepack([self.rule()])
        self.write_requirements(["REQ-001"])
        Path("a.py").write_text("x = 1\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add a.py (REQ-001)")
        parent = self.git("rev-parse", "HEAD").strip()
        Path("b.py").write_text("y = 1\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add b.py (REQ-001)")
        failures, _ = ma.gate_evaluate({"b.py"}, {}, base=parent)
        self.assertEqual(failures, [])

    def test_a_commit_citing_an_unknown_requirement_since_the_base_fails(self) -> None:
        self.write_rulepack([self.rule()])
        self.write_requirements(["REQ-001"])
        Path("a.py").write_text("x = 1\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "initial (REQ-001)")
        base = self.git("rev-parse", "HEAD").strip()
        Path("b.py").write_text("y = 1\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add b.py (REQ-404)")
        failures, _ = ma.gate_evaluate({"b.py"}, {}, base=base)
        self.assertEqual(len(failures), 1)
        self.assertIn("REQ-404", failures[0])

    def test_a_typo_in_a_changelog_stub_is_also_caught(self) -> None:
        self.write_rulepack([self.rule()])
        self.write_requirements(["REQ-001"])
        Path("a.py").write_text("x = 1\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "initial")
        base = self.git("rev-parse", "HEAD").strip()
        Path("CHANGELOG.md").write_text("# Changelog\n\n## today\n\n- `REQ-042` did a thing\n", encoding="utf-8")
        failures, _ = ma.gate_evaluate({"CHANGELOG.md"}, {}, base=base)
        self.assertEqual(len(failures), 1)
        self.assertIn("REQ-042", failures[0])

    def test_no_citation_at_all_is_not_a_failure(self) -> None:
        self.write_rulepack([self.rule()])
        self.write_requirements(["REQ-001"])
        Path("a.py").write_text("x = 1\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "initial")
        base = self.git("rev-parse", "HEAD").strip()
        Path("a.py").write_text("x = 2\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "tweak, no requirement mentioned")
        failures, _ = ma.gate_evaluate({"a.py"}, {}, base=base)
        self.assertEqual(failures, [])

    def test_a_waived_citation_is_reported_as_waived_not_a_failure(self) -> None:
        self.write_rulepack([self.rule()])
        self.write_requirements(["REQ-001"])
        Path("a.py").write_text("x = 1\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "initial (REQ-001)")
        base = self.git("rev-parse", "HEAD").strip()
        Path("b.py").write_text("y = 1\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Add b.py (REQ-404)")
        failures, waived = ma.gate_evaluate({"b.py"}, {"x.req_entry": "REQ-404 is tracked externally, not in this registry"}, base=base)
        self.assertEqual(failures, [])
        self.assertEqual(len(waived), 1)


class TestContentForbidden(GateRuleFixture):
    def rule(self) -> dict:
        return {"id": "x.no_secrets", "severity": "required", "statement": "s", "validation": {
            "type": "content_forbidden", "when_changed": ["**"],
            "patterns": [
                {"pattern": "-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", "message": "looks like a private key"},
                {"pattern": "AKIA[0-9A-Z]{16}", "message": "looks like an AWS access key id"},
            ],
        }}

    def test_a_private_key_header_is_caught(self) -> None:
        # built at runtime, not written as a literal in this file's own source: this test file is itself
        # scanned by the real gate once committed, and a literal fixture here would flag itself
        header = "".join(["-" * 5, "BEGIN RSA PRIVATE KEY", "-" * 5])
        self.write_rulepack([self.rule()])
        Path("config.py").write_text(f"KEY = '{header}\nMIIE...'\n", encoding="utf-8")
        failures, _ = ma.gate_evaluate({"config.py"}, {})
        self.assertEqual(len(failures), 1)
        self.assertIn("private key", failures[0])
        self.assertIn("config.py:1", failures[0])

    def test_an_aws_shaped_key_is_caught(self) -> None:
        fake_key = "AKIA" + "ABCDEFGHIJKLMNOP"  # see the note above: assembled, not a literal match in this source
        self.write_rulepack([self.rule()])
        Path("config.py").write_text(f"KEY = '{fake_key}'\n", encoding="utf-8")
        failures, _ = ma.gate_evaluate({"config.py"}, {})
        self.assertEqual(len(failures), 1)
        self.assertIn("AWS", failures[0])

    def test_ordinary_content_passes(self) -> None:
        self.write_rulepack([self.rule()])
        Path("config.py").write_text("greeting = 'hello world'\n", encoding="utf-8")
        failures, _ = ma.gate_evaluate({"config.py"}, {})
        self.assertEqual(failures, [])

    def test_the_line_number_of_the_match_is_reported(self) -> None:
        fake_key = "AKIA" + "ABCDEFGHIJKLMNOP"
        self.write_rulepack([self.rule()])
        Path("config.py").write_text(f"a = 1\nb = 2\nKEY = '{fake_key}'\n", encoding="utf-8")
        failures, _ = ma.gate_evaluate({"config.py"}, {})
        self.assertIn("config.py:3", failures[0])

    def test_a_rulepack_file_containing_the_pattern_source_does_not_flag_itself(self) -> None:
        # a rulepack's own JSON literally contains "-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
        # as the pattern's source text; the check must not treat that as a leaked key
        self.write_rulepack([self.rule()])
        failures, _ = ma.gate_evaluate({str(self.rulepack_path)}, {})
        self.assertEqual(failures, [])

    def test_a_file_that_no_longer_exists_is_skipped_not_an_error(self) -> None:
        self.write_rulepack([self.rule()])
        failures, _ = ma.gate_evaluate({"deleted-file-that-never-existed.py"}, {})
        self.assertEqual(failures, [])

    def test_a_binary_or_undecodable_file_is_skipped_not_a_crash(self) -> None:
        self.write_rulepack([self.rule()])
        Path("blob.bin").write_bytes(b"\xff\xfe\x00\x01AKIA")
        failures, _ = ma.gate_evaluate({"blob.bin"}, {})
        self.assertEqual(failures, [])

    def test_a_malformed_pattern_entry_is_skipped_not_a_crash(self) -> None:
        self.write_rulepack([{"id": "x.bad", "severity": "required", "statement": "s", "validation": {
            "type": "content_forbidden", "when_changed": ["**"], "patterns": [{"message": "no pattern key"}, {"pattern": "("}],
        }}])
        Path("a.py").write_text("hello\n", encoding="utf-8")
        failures, _ = ma.gate_evaluate({"a.py"}, {})
        self.assertEqual(failures, [])


class TestCommandValidation(GateRuleFixture):
    """`command` runs the project's own check as a gate. The executable here is this interpreter, so the
    tests need nothing installed and still exercise exit codes, output capture, timeouts and PATH."""

    def rule(self, run: str, **extra: object) -> dict:
        validation = {"type": "command", "run": run, "when_changed": ["src/**"], **extra}
        return {"id": "x.project_check", "severity": "required", "statement": "s", "validation": validation}

    @staticmethod
    def py(code: str) -> str:
        return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"

    def test_a_passing_command_is_not_a_failure(self) -> None:
        self.write_rulepack([self.rule(self.py("import sys; sys.exit(0)"))])
        self.assertEqual(ma.gate_evaluate({"src/a.py"}, {})[0], [])

    def test_a_failing_command_reports_its_exit_code_and_output_tail(self) -> None:
        self.write_rulepack([self.rule(self.py("print('first'); print('the real reason'); raise SystemExit(3)"))])
        failures, _ = ma.gate_evaluate({"src/a.py"}, {})
        self.assertEqual(len(failures), 1)
        self.assertIn("exited 3", failures[0])
        self.assertIn("the real reason", failures[0])

    def test_the_command_runs_only_when_a_matching_path_changed(self) -> None:
        self.write_rulepack([self.rule(self.py("raise SystemExit(1)"))])
        self.assertEqual(ma.gate_evaluate({"docs/readme.md"}, {})[0], [])
        self.assertEqual(len(ma.gate_evaluate({"src/a.py"}, {})[0]), 1)

    def test_ignored_paths_do_not_trigger_it(self) -> None:
        self.write_rulepack([self.rule(self.py("raise SystemExit(1)"), ignore=["src/generated/**"])])
        self.assertEqual(ma.gate_evaluate({"src/generated/x.py"}, {})[0], [])

    def test_base_is_substituted(self) -> None:
        self.write_rulepack([self.rule(self.py("import sys; sys.exit(0 if 'abc123' in sys.argv else 1)") + " {base}")])
        self.assertEqual(ma.gate_evaluate({"src/a.py"}, {}, base="abc123")[0], [])
        self.assertEqual(len(ma.gate_evaluate({"src/a.py"}, {}, base="other")[0]), 1)

    def test_a_missing_executable_is_a_failure_not_a_pass(self) -> None:
        self.write_rulepack([self.rule("omni-gate-test-no-such-tool-xyz --flag")])
        failures, _ = ma.gate_evaluate({"src/a.py"}, {})
        self.assertEqual(len(failures), 1)
        self.assertIn("not on PATH", failures[0])

    def test_a_hanging_command_times_out(self) -> None:
        self.write_rulepack([self.rule(self.py("import time; time.sleep(30)"), timeout=1)])
        failures, _ = ma.gate_evaluate({"src/a.py"}, {})
        self.assertEqual(len(failures), 1)
        self.assertIn("did not finish within 1s", failures[0])

    def test_a_waiver_still_applies(self) -> None:
        self.write_rulepack([self.rule(self.py("raise SystemExit(1)"))])
        failures, waived = ma.gate_evaluate({"src/a.py"}, {"x.project_check": "known red, tracked in REQ-9"})
        self.assertEqual(failures, [])
        self.assertEqual(len(waived), 1)


class TestCommandMemo(GateRuleFixture):
    """REQ-047: a `command` rule that passed is memoised on its scoped change set (the paths its globs select,
    their mtimes and sizes, and the run text) in the gate state file, and not re-run until that changes."""

    def rule(self, code: str) -> dict:
        return {"id": "x.suite", "severity": "required", "statement": "s", "validation": {
            "type": "command", "run": TestCommandValidation.py(code), "when_changed": ["src/**"], "ignore": ["src/generated/**"],
        }}

    COUNTING = "open('ran.log', 'a').write('x')"

    def runs(self) -> int:
        return len(Path("ran.log").read_text(encoding="utf-8")) if Path("ran.log").is_file() else 0

    def evaluate(self, changed: set[str], memo: dict, waivers: dict | None = None) -> tuple[list[str], list[dict]]:
        outcomes: list[dict] = []
        failures, _ = ma.gate_evaluate(changed, waivers or {}, memo=memo, outcomes=outcomes)
        return failures, outcomes

    def test_a_pass_is_memoised_and_not_re_run_on_the_same_scoped_change_set(self) -> None:
        self.write_rulepack([self.rule(self.COUNTING)])
        Path("src").mkdir()
        Path("src/a.py").write_text("x = 1\n", encoding="utf-8")
        memo: dict[str, str] = {}
        failures, outcomes = self.evaluate({"src/a.py"}, memo)
        self.assertEqual(failures, [])
        self.assertEqual(self.runs(), 1)
        self.assertEqual(outcomes[0]["status"], "pass")
        self.assertEqual(memo, {"x.suite": ma.gate_rule_memo_signature(self.rule(self.COUNTING), {"src/a.py"})})
        failures, outcomes = self.evaluate({"src/a.py"}, memo)
        self.assertEqual(failures, [])
        self.assertEqual(self.runs(), 1, "memo hit: the command must not run again")
        self.assertEqual(outcomes[0]["status"], "memo")

    def test_a_scoped_file_change_invalidates_the_memo_and_an_unscoped_one_does_not(self) -> None:
        self.write_rulepack([self.rule(self.COUNTING)])
        Path("src").mkdir()
        Path("src/a.py").write_text("x = 1\n", encoding="utf-8")
        memo: dict[str, str] = {}
        self.evaluate({"src/a.py"}, memo)
        Path("docs").mkdir()
        Path("docs/notes.md").write_text("hi\n", encoding="utf-8")
        _, outcomes = self.evaluate({"src/a.py", "docs/notes.md", "src/generated/x.py"}, memo)
        self.assertEqual(outcomes[0]["status"], "memo", "a path the rule does not watch leaves the memo valid")
        self.assertEqual(self.runs(), 1)
        Path("src/a.py").write_text("x = 2\n", encoding="utf-8")
        _, outcomes = self.evaluate({"src/a.py"}, memo)
        self.assertEqual(outcomes[0]["status"], "pass")
        self.assertEqual(self.runs(), 2, "an edit to a scoped file re-runs the rule")
        stat = Path("src/a.py").stat()
        os.utime("src/a.py", ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        _, outcomes = self.evaluate({"src/a.py"}, memo)
        self.assertEqual(self.runs(), 3, "a touch (mtime only) re-runs it too")
        Path("src/b.py").write_text("y = 1\n", encoding="utf-8")
        self.evaluate({"src/a.py", "src/b.py"}, memo)
        self.assertEqual(self.runs(), 4, "a new scoped path re-runs it")

    def test_a_different_run_text_is_a_different_memo(self) -> None:
        self.write_rulepack([self.rule(self.COUNTING)])
        Path("src").mkdir()
        Path("src/a.py").write_text("x = 1\n", encoding="utf-8")
        memo: dict[str, str] = {}
        self.evaluate({"src/a.py"}, memo)
        self.write_rulepack([self.rule(self.COUNTING + "; pass")])
        self.evaluate({"src/a.py"}, memo)
        self.assertEqual(self.runs(), 2)

    def test_a_failed_rule_is_never_memoised(self) -> None:
        self.write_rulepack([self.rule(self.COUNTING + "; raise SystemExit(1)")])
        Path("src").mkdir()
        Path("src/a.py").write_text("x = 1\n", encoding="utf-8")
        rule = self.rule(self.COUNTING + "; raise SystemExit(1)")
        memo = {"x.suite": ma.gate_rule_memo_signature(rule, {"src/a.py"})}  # a stale record must not survive a failure
        failures, outcomes = self.evaluate({"src/a.py"}, memo)
        self.assertEqual(outcomes[0]["status"], "memo", "sanity: the planted record matched")
        memo = {}
        failures, outcomes = self.evaluate({"src/a.py"}, memo)
        self.assertEqual(len(failures), 1)
        self.assertEqual(outcomes[0]["status"], "fail")
        self.assertEqual(memo, {})
        failures, outcomes = self.evaluate({"src/a.py"}, memo)
        self.assertEqual(len(failures), 1)
        self.assertEqual(self.runs(), 2, "a failed rule runs every time")
        self.assertEqual(memo, {})
        failures, outcomes = self.evaluate({"src/a.py"}, memo, waivers={"x.suite": "known red, tracked in REQ-9"})
        self.assertEqual(failures, [])
        self.assertEqual(outcomes[0]["status"], "waived")
        self.assertEqual(memo, {}, "a waived failure is still a failure, never a memoised pass")

    def test_without_a_memo_dict_nothing_is_memoised(self) -> None:
        self.write_rulepack([self.rule(self.COUNTING)])
        Path("src").mkdir()
        Path("src/a.py").write_text("x = 1\n", encoding="utf-8")
        ma.gate_evaluate({"src/a.py"}, {})
        ma.gate_evaluate({"src/a.py"}, {})
        self.assertEqual(self.runs(), 2)

    def test_omni_gate_reports_memo_hits_durations_and_honours_no_memo(self) -> None:
        self.write_rulepack([self.rule(self.COUNTING)])
        Path("src").mkdir()
        Path("src/a.py").write_text("x = 1\n", encoding="utf-8")
        Path("README.md").write_text("r\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-q", "-m", "base")
        import io
        from contextlib import redirect_stdout

        def gate(no_memo: bool = False) -> tuple[int, str]:
            out = io.StringIO()
            with redirect_stdout(out), mock.patch.object(ma, "build_doctor_report", return_value=ma.DoctorReport()):
                code = ma.run_gate(argparse.Namespace(hook=False, no_memo=no_memo))
            return code, out.getvalue()

        code, output = gate()
        self.assertEqual(code, 0, output)
        self.assertRegex(output, r"(?m)^  ran     x\.suite \d+\.\ds PASS$")
        self.assertIn("omni gate: PASS", output)
        self.assertEqual(self.runs(), 1)
        state = json.loads((self.root / ".git" / "omni-gate-last.json").read_text(encoding="utf-8"))
        self.assertIn("x.suite", state["memo"])
        code, output = gate()
        self.assertEqual(code, 0, output)
        self.assertIn("  memo    x.suite passed (memo)", output)
        self.assertEqual(self.runs(), 1)
        code, output = gate(no_memo=True)
        self.assertEqual(code, 0, output)
        self.assertRegex(output, r"(?m)^  ran     x\.suite \d+\.\ds PASS$")
        self.assertEqual(self.runs(), 2, "--no-memo runs every rule")
        code, output = gate()
        self.assertIn("passed (memo)", output)
        self.assertEqual(self.runs(), 2, "--no-memo still records the pass for the next run")

    def test_the_memo_and_the_hook_signature_share_the_state_file(self) -> None:
        ma.gate_state_save({"signature": "abc"})
        ma.gate_state_save({"memo": {"x.suite": "sig"}})
        self.assertEqual(ma.gate_state_load(), {"signature": "abc", "memo": {"x.suite": "sig"}})

    def test_omni_in_a_run_text_resolves_to_this_cli_even_off_path(self) -> None:
        with mock.patch.object(ma.shutil, "which", return_value=None):
            argv = ma._gate_command_argv(["omni", "--help"])
            self.assertIsNone(ma._gate_command_argv(["omni-gate-test-no-such-tool-xyz"]))
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(Path(argv[1]), ROOT / "omni")
        self.assertEqual(argv[2:], ["--help"])
        self.write_rulepack([{"id": "x.omni", "severity": "required", "statement": "s",
                              "validation": {"type": "command", "run": "omni --help", "when_changed": ["src/**"]}}])
        with mock.patch.object(ma.shutil, "which", return_value=None):
            self.assertEqual(ma.gate_evaluate({"src/a.py"}, {})[0], [])


class TestDefectCategoryPattern(unittest.TestCase):
    def test_default_matches_the_usual_names(self) -> None:
        with mock.patch.object(ma, "project_configuration", return_value={}):
            pattern = ma.defect_category_pattern()
        for name in ("Defect", "bug", "hotfix", "regression", "incident"):
            self.assertTrue(pattern.search(name), name)
        self.assertFalse(pattern.search("developer-tooling"))

    def test_configuration_overrides_the_default(self) -> None:
        with mock.patch.object(ma, "project_configuration", return_value={"defect_category_pattern": "tooling|defect"}):
            pattern = ma.defect_category_pattern()
        self.assertTrue(pattern.search("developer-tooling"))

    def test_an_invalid_pattern_falls_back_to_the_default(self) -> None:
        with mock.patch.object(ma, "project_configuration", return_value={"defect_category_pattern": "("}):
            self.assertIs(ma.defect_category_pattern(), ma.DEFECT_CATEGORY)


class TestVendoredWorkspaces(GateRuleFixture):
    """A subtree with its own workspace carries its own registry; its commit messages cite ids this
    registry does not hold, and the gate must read them as theirs, not as typos here."""

    def vendor(self, ids: list[str], with_marker: bool = True) -> None:
        (self.root / "subtree" / ".ai" / "requirements").mkdir(parents=True)
        if with_marker:
            (self.root / "subtree" / ".ai" / "omni-version.json").write_text('{"source": "x", "ref": "abc"}', encoding="utf-8")
        (self.root / "subtree" / ".ai" / "requirements" / "requirements.json").write_text(json.dumps({
            "version": "1.0.0", "requirement_id_prefix": "REQ",
            "requirements": [{"id": i, "category": "Feature", "title": i, "description": "d", "priority": "low",
                               "status": "completed", "minimum_access_scope": [], "acceptance_criteria": [],
                               "validation_required": [], "documentation_required": [], "risk_notes": []} for i in ids],
        }), encoding="utf-8")

    def rule(self) -> dict:
        return {"id": "x.req_entry", "severity": "required", "statement": "s",
                "validation": {"type": "requirement_registry_entry", "target": ".ai/requirements/requirements.json"}}

    def commits_citing(self, text: str) -> str:
        Path("a.py").write_text("x = 1\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        parent = self.git("rev-parse", "HEAD").strip()
        Path("b.py").write_text("y = 1\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", text)
        return parent

    def test_an_id_from_a_vendored_registry_is_known(self) -> None:
        self.write_rulepack([self.rule()])
        self.write_requirements(["REQ-001"])
        self.vendor(["REQ-900"])
        parent = self.commits_citing("Vendored change (REQ-900)")
        self.assertEqual(ma.vendored_requirement_ids(), {"REQ-900"})
        self.assertEqual(ma.gate_evaluate({"b.py"}, {}, base=parent)[0], [])

    def test_a_directory_without_the_adoption_marker_is_not_a_workspace(self) -> None:
        self.write_rulepack([self.rule()])
        self.write_requirements(["REQ-001"])
        self.vendor(["REQ-900"], with_marker=False)
        parent = self.commits_citing("Vendored change (REQ-900)")
        self.assertEqual(ma.vendored_workspace_dirs(), [])
        self.assertEqual(len(ma.gate_evaluate({"b.py"}, {}, base=parent)[0]), 1)

    def test_doctor_reports_tooling_drift_in_a_vendored_workspace(self) -> None:
        self.vendor(["REQ-900"])
        Path("make_ai.py").write_text("# root\n", encoding="utf-8")
        (self.root / "subtree" / "make_ai.py").write_text("# root\n", encoding="utf-8")
        report = ma.DoctorReport()
        ma.validate_vendored_workspaces(report)
        self.assertEqual(report.warnings, [])
        self.assertTrue(any("subtree/" in line for line in report.passed))
        (self.root / "subtree" / "make_ai.py").write_text("# older\n", encoding="utf-8")
        report = ma.DoctorReport()
        ma.validate_vendored_workspaces(report)
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("cd subtree && python omni update --source ..", report.warnings[0])


if __name__ == "__main__":
    unittest.main()
