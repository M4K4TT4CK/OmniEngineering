"""Tests for the gate's executable validation types: `co_changed` (pre-existing), and the two this session
adds real behaviour to -- `requirement_registry_entry` (previously declared on a rule but never executed:
`gate_rules()` only ever picked up `co_changed`) and `content_forbidden` (a new, honest, bounded safety net
for a handful of common accidental leaks).

Stdlib only. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
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
        for vtype in ("co_changed", "requirement_registry_entry", "content_forbidden"):
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


if __name__ == "__main__":
    unittest.main()
