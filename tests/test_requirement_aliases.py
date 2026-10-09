"""Tests for REQ-044: requirement id aliases (`id_aliases` in the registry) and `omni requirement renumber`.

An alias maps an id the registry used to carry to the id it carries now (REQ-001 -> ARB-001). The gate,
`find_requirement` (so `requirement show|update|complete|archive`), `omni failure check` and the doctor
all resolve aliases; `renumber` is what writes them. Stdlib only. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_ai as ma  # noqa: E402

SCHEMA_PATH = ROOT / ".ai/schemas/requirements.schema.json"


def requirement(req_id: str, status: str = "completed", description: str = "d") -> dict:
    return {
        "id": req_id, "category": "Feature", "title": f"{req_id} title", "description": description, "priority": "low",
        "status": status, "minimum_access_scope": [], "acceptance_criteria": [], "validation_required": [],
        "documentation_required": [], "risk_notes": [],
    }


def registry(prefix: str, items: list[dict], aliases: dict[str, str] | None = None) -> dict:
    data: dict = {"version": "1.0.0", "requirement_id_prefix": prefix}
    if aliases is not None:
        data["id_aliases"] = aliases
    data["requirements"] = items
    return data


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class WorkspaceFixture(unittest.TestCase):
    """A temporary workspace the process chdirs into: a registry whose prefix is ARB, with aliases for the
    REQ ids it carried before a rename."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self._cwd)
        ma.REQUIREMENTS_PATH.parent.mkdir(parents=True)
        ma.write_json(ma.REQUIREMENTS_PATH, registry(
            "ARB", [requirement("ARB-001"), requirement("ARB-002", "pending")],
            {"REQ-001": "ARB-001", "REQ-002": "ARB-002"},
        ))

    def write_ledger(self, requirement_id: str) -> None:
        ma.write_json(ma.FAILURE_LEDGER_PATH, {"version": "1.0.0", "failure_id_prefix": "FAIL", "failures": [
            {"id": "FAIL-001", "title": "t", "symptom": "s", "status": "open", "requirement": requirement_id},
        ]})


# --- Resolution: helpers, find_requirement, the CLI ------------------------------------------------


class TestResolution(WorkspaceFixture):
    def test_aliases_are_read_from_the_registry(self) -> None:
        self.assertEqual(ma.requirement_id_aliases(), {"REQ-001": "ARB-001", "REQ-002": "ARB-002"})
        self.assertEqual(ma.requirement_id_aliases({"requirements": []}), {})
        self.assertEqual(ma.requirement_id_aliases({"id_aliases": {"REQ-001": 7, "REQ-002": "ARB-002"}}), {"REQ-002": "ARB-002"})

    def test_resolve_follows_an_alias_once_and_passes_unknown_ids_through(self) -> None:
        self.assertEqual(ma.resolve_requirement_id("REQ-001"), "ARB-001")
        self.assertEqual(ma.resolve_requirement_id("ARB-001"), "ARB-001")
        self.assertEqual(ma.resolve_requirement_id("REQ-404"), "REQ-404")
        # an explicit alias table is honoured and never chained
        self.assertEqual(ma.resolve_requirement_id("A-001", {"A-001": "B-001", "B-001": "C-001"}), "B-001")

    def test_find_requirement_resolves_an_old_id(self) -> None:
        found = ma.find_requirement("REQ-002")
        self.assertIsNotNone(found)
        self.assertEqual(found[2]["id"], "ARB-002")
        self.assertEqual(ma.normalize_requirement_id("req-001"), "ARB-001")
        self.assertEqual(ma.normalize_requirement_id("2"), "ARB-002")
        self.assertIsNone(ma.find_requirement("REQ-404"))

    def test_requirement_show_on_an_old_id_prints_the_new_entry(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = ma.run_requirement_show(argparse.Namespace(id="REQ-001", json=False))
        self.assertEqual(code, 0)
        self.assertTrue(out.getvalue().startswith("ARB-001  [completed]"), out.getvalue())
        self.assertNotIn("REQ-001", out.getvalue())

    def test_requirement_update_on_an_old_id_changes_the_new_entry(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = ma.run_requirement_update(argparse.Namespace(id="REQ-002", status="blocked", title=None, description=None, note=None))
        self.assertEqual(code, 0)
        self.assertIn("Updated ARB-002", out.getvalue())
        items = {item["id"]: item for item in read_json(ma.REQUIREMENTS_PATH)["requirements"]}
        self.assertEqual(items["ARB-002"]["status"], "blocked")

    def test_requirement_archive_accepts_an_old_id(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = ma.run_requirement_archive(argparse.Namespace(id="REQ-001", keep_recent=25, dry_run=False))
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual([item["id"] for item in read_json(ma.REQUIREMENTS_ARCHIVE_PATH)["requirements"]], ["ARB-001"])


# --- Failure ledger ------------------------------------------------------------------------------


class TestFailureCheck(WorkspaceFixture):
    def check(self) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = ma.run_failure_check(argparse.Namespace())
        return code, out.getvalue()

    def test_an_aliased_requirement_passes(self) -> None:
        self.write_ledger("REQ-001")
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_an_unknown_requirement_still_fails(self) -> None:
        self.write_ledger("REQ-009")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("REQ-009 is not in the registry", out)


# --- Gate ----------------------------------------------------------------------------------------


class TestGateAliases(WorkspaceFixture):
    """A commit that cites an id the registry has since renumbered must still pass the
    `requirement_registry_entry` rule; an id nobody ever registered must still fail."""

    def setUp(self) -> None:
        super().setUp()
        self._env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        self.git("init", "-q")
        rulepack_path = Path(".ai/rules/test-rules.json")
        rulepack_path.parent.mkdir(parents=True, exist_ok=True)
        rulepack_path.write_text(json.dumps({
            "rulepack_id": "test_rules", "version": "1.0.0", "title": "t", "purpose": "p", "applies_to": [],
            "rules": [{"id": "x.req_entry", "severity": "required", "statement": "s",
                       "validation": {"type": "requirement_registry_entry", "target": ".ai/requirements/requirements.json"}}],
        }), encoding="utf-8")
        patcher = mock.patch.object(ma, "RULEPACK_FILES", [str(rulepack_path)])
        patcher.start()
        self.addCleanup(patcher.stop)
        Path("a.py").write_text("x = 1\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "initial")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True, env=self._env).stdout

    def commit_citing(self, subject: str) -> list[str]:
        Path("b.py").write_text(f"y = {subject!r}\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", subject)
        failures, _ = ma.gate_evaluate({"b.py"}, {}, base=self.base)
        return failures

    def test_a_commit_citing_an_aliased_id_passes(self) -> None:
        self.assertEqual(self.commit_citing("Add b.py (REQ-001)"), [])

    def test_a_commit_citing_the_current_id_passes(self) -> None:
        self.assertEqual(self.commit_citing("Add b.py (ARB-002)"), [])

    def test_a_commit_citing_an_unknown_id_under_either_prefix_fails(self) -> None:
        failures = self.commit_citing("Add b.py (REQ-404, ARB-405)")
        self.assertEqual(len(failures), 1)
        self.assertIn("REQ-404", failures[0])
        self.assertIn("ARB-405", failures[0])

    def test_a_vendored_workspace_alias_is_honoured(self) -> None:
        vendored = Path("sub/.ai/requirements")
        vendored.mkdir(parents=True)
        Path("sub/.ai/omni-version.json").write_text("{}", encoding="utf-8")
        ma.write_json(vendored / "requirements.json", registry("SUB", [requirement("SUB-007")], {"OLD-007": "SUB-007"}))
        self.assertEqual(self.commit_citing("Pull subtree (OLD-007)"), [])
        self.assertEqual(len(self.commit_citing("Pull subtree (OLD-008)")), 1)


# --- Doctor --------------------------------------------------------------------------------------


class TestDoctorAliases(WorkspaceFixture):
    def validate(self) -> ma.DoctorReport:
        report = ma.DoctorReport()
        ma.validate_requirement_aliases(report)
        return report

    def test_valid_aliases_pass_silently(self) -> None:
        report = self.validate()
        self.assertEqual(report.errors, [])
        self.assertTrue(any("alias" in message for message in report.passed), report.passed)

    def test_no_aliases_is_fine(self) -> None:
        ma.write_json(ma.REQUIREMENTS_PATH, registry("ARB", [requirement("ARB-001")]))
        report = self.validate()
        self.assertEqual((report.errors, report.passed), ([], []))

    def test_a_dangling_alias_is_an_error(self) -> None:
        ma.write_json(ma.REQUIREMENTS_PATH, registry("ARB", [requirement("ARB-001")], {"REQ-009": "ARB-009"}))
        report = self.validate()
        self.assertEqual(len(report.errors), 1)
        self.assertIn("REQ-009 -> ARB-009", report.errors[0])

    def test_an_alias_to_an_archived_requirement_is_valid(self) -> None:
        ma.write_json(ma.REQUIREMENTS_ARCHIVE_PATH, registry("ARB", [requirement("ARB-009")]))
        ma.write_json(ma.REQUIREMENTS_PATH, registry("ARB", [requirement("ARB-001")], {"REQ-009": "ARB-009"}))
        self.assertEqual(self.validate().errors, [])

    def test_an_alias_key_that_is_still_a_live_id_is_an_error(self) -> None:
        ma.write_json(ma.REQUIREMENTS_PATH, registry("ARB", [requirement("ARB-001"), requirement("ARB-002")], {"ARB-001": "ARB-002"}))
        report = self.validate()
        self.assertEqual(len(report.errors), 1)
        self.assertIn("ARB-001 is both a live requirement id and an alias", report.errors[0])

    def test_a_malformed_alias_is_an_error(self) -> None:
        ma.write_json(ma.REQUIREMENTS_PATH, registry("ARB", [requirement("ARB-001")], {"req-1": "ARB-001"}))
        self.assertEqual(len(self.validate().errors), 1)

    def test_duplicate_id_check_ignores_alias_keys(self) -> None:
        # a vendored workspace legitimately owns REQ-001 now that the root only remembers it as an alias
        vendored = Path("sub/.ai/requirements")
        vendored.mkdir(parents=True)
        Path("sub/.ai/omni-version.json").write_text("{}", encoding="utf-8")
        ma.write_json(vendored / "requirements.json", registry("REQ", [requirement("REQ-001")]))
        report = ma.DoctorReport()
        ma.validate_requirement_ids(report)
        self.assertEqual(report.errors, [])
        full = ma.DoctorReport()
        ma.validate_requirements(read_json(ma.REQUIREMENTS_PATH), full)
        self.assertEqual(full.errors, [])


# --- Renumber ------------------------------------------------------------------------------------


class TestRenumber(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self._cwd)
        ma.REQUIREMENTS_PATH.parent.mkdir(parents=True)
        ma.write_json(ma.REQUIREMENTS_PATH, registry(
            "REQ", [requirement("REQ-001", description="supersedes REQ-003"), requirement("REQ-002", "pending")],
            {"OLD-001": "REQ-001"},
        ))
        ma.write_json(ma.REQUIREMENTS_ARCHIVE_PATH, registry("REQ", [requirement("REQ-003", "withdrawn")]))
        ma.write_json(ma.FAILURE_LEDGER_PATH, {"version": "1.0.0", "failure_id_prefix": "FAIL", "failures": [
            {"id": "FAIL-001", "title": "t", "symptom": "s", "status": "open", "requirement": "REQ-002"},
        ]})
        ma.GATE_WAIVERS_PATH.write_text(
            json.dumps({"rule": "x.rule", "reason": "r", "requirement": "REQ-001", "date": "2026-01-01"}) + "\n",
            encoding="utf-8",
        )
        self.files = {
            "CHANGELOG.md": "# Changelog\n\n- Did a thing (REQ-001)\n- And another (REQ-002, REQ-003)\n",
            ".ai/notes/plan.md": "Plan for REQ-002; not REQUEST-001 or REQ-0010 or xREQ-001.\n",
            ".ai/rules/pack.json": json.dumps({"rules": [{"id": "r", "statement": "see REQ-001"}]}) + "\n",
            "docs/guide.md": "REQ-001\n",
            "tests/x.py": "REQ = 'REQ-001'\n",
            "src/x.py": "# REQ-001\n",
            "arbiter-out/report.md": "REQ-001\n",
        }
        for relpath, text in self.files.items():
            path = Path(relpath)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def renumber(self, prefix: str, dry_run: bool = False, paths: list[str] | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ma.run_requirement_renumber(argparse.Namespace(prefix=prefix, dry_run=dry_run, paths=paths))
        return code, out.getvalue(), err.getvalue()

    def snapshot(self) -> dict[str, str]:
        return {
            path.as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(Path(".").rglob("*")) if path.is_file()
        }

    def test_refuses_a_bad_prefix(self) -> None:
        before = self.snapshot()
        for bad in ("arb", "AR1", "", "A-B"):
            code, _, err = self.renumber(bad)
            self.assertEqual(code, 1, bad)
            self.assertIn("upper-case", err)
        self.assertEqual(self.snapshot(), before)

    def test_rewrites_registries_ledger_waivers_and_governance_text_only(self) -> None:
        code, out, err = self.renumber("ARB")
        self.assertEqual(code, 0, err)

        reg = read_json(ma.REQUIREMENTS_PATH)
        self.assertEqual(list(reg), ["version", "requirement_id_prefix", "id_aliases", "requirements"])
        self.assertEqual(reg["requirement_id_prefix"], "ARB")
        self.assertEqual([item["id"] for item in reg["requirements"]], ["ARB-001", "ARB-002"])
        self.assertEqual(reg["requirements"][0]["description"], "supersedes ARB-003")
        self.assertEqual(reg["id_aliases"], {"OLD-001": "ARB-001", "REQ-001": "ARB-001", "REQ-002": "ARB-002", "REQ-003": "ARB-003"})

        archive = read_json(ma.REQUIREMENTS_ARCHIVE_PATH)
        self.assertEqual(archive["requirement_id_prefix"], "ARB")
        self.assertEqual([item["id"] for item in archive["requirements"]], ["ARB-003"])
        self.assertNotIn("id_aliases", archive)

        self.assertEqual(read_json(ma.FAILURE_LEDGER_PATH)["failures"][0]["requirement"], "ARB-002")
        waiver = json.loads(ma.GATE_WAIVERS_PATH.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(waiver["requirement"], "ARB-001")

        self.assertEqual(Path("CHANGELOG.md").read_text(encoding="utf-8"), "# Changelog\n\n- Did a thing (ARB-001)\n- And another (ARB-002, ARB-003)\n")
        self.assertEqual(Path(".ai/notes/plan.md").read_text(encoding="utf-8"), "Plan for ARB-002; not REQUEST-001 or REQ-0010 or xREQ-001.\n")
        self.assertIn("see ARB-001", Path(".ai/rules/pack.json").read_text(encoding="utf-8"))
        self.assertEqual(Path("docs/guide.md").read_text(encoding="utf-8"), "ARB-001\n")
        for untouched in ("tests/x.py", "src/x.py", "arbiter-out/report.md"):
            self.assertEqual(Path(untouched).read_text(encoding="utf-8"), self.files[untouched], untouched)

        self.assertIn("3 requirement id(s) (2 active, 1 archived); 4 alias(es)", out)
        self.assertIn("CHANGELOG.md: 3 change(s)", out)
        self.assertIn(".ai/notes/plan.md: 1 change(s)", out)
        self.assertNotIn("tests/x.py", out)

        # the renamed workspace is healthy and the old ids still resolve
        report = ma.DoctorReport()
        ma.validate_requirement_aliases(report)
        self.assertEqual(report.errors, [])
        self.assertEqual(ma.find_requirement("REQ-003")[2]["id"], "ARB-003")
        self.assertEqual(ma.next_requirement_id(reg), "ARB-004")

    def test_second_run_is_a_no_op(self) -> None:
        self.renumber("ARB")
        before = self.snapshot()
        code, out, _ = self.renumber("ARB")
        self.assertEqual(code, 0)
        self.assertIn("already use the prefix ARB", out)
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_reports_and_writes_nothing(self) -> None:
        before = self.snapshot()
        code, out, _ = self.renumber("ARB", dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("Dry run: no files written.", out)
        self.assertIn("CHANGELOG.md: 3 change(s)", out)
        self.assertEqual(self.snapshot(), before)

    def test_paths_replaces_the_default_allow_list(self) -> None:
        code, out, _ = self.renumber("ARB", paths=["docs/**/*.md"])
        self.assertEqual(code, 0)
        self.assertEqual(Path("docs/guide.md").read_text(encoding="utf-8"), "ARB-001\n")
        self.assertEqual(Path("CHANGELOG.md").read_text(encoding="utf-8"), self.files["CHANGELOG.md"])
        self.assertNotIn("CHANGELOG.md", out)
        # the registries, ledger and waivers are structural, not part of the allow list
        self.assertEqual(read_json(ma.REQUIREMENTS_PATH)["requirement_id_prefix"], "ARB")
        self.assertEqual(read_json(ma.FAILURE_LEDGER_PATH)["failures"][0]["requirement"], "ARB-002")

    def test_vendored_workspaces_are_never_touched(self) -> None:
        vendored = Path("sub/.ai/requirements")
        vendored.mkdir(parents=True)
        Path("sub/.ai/omni-version.json").write_text("{}", encoding="utf-8")
        ma.write_json(vendored / "requirements.json", registry("REQ", [requirement("REQ-001")]))
        Path("sub/README.md").write_text("REQ-001\n", encoding="utf-8")
        self.renumber("ARB", paths=["**/*.md", "**/*.json"])
        self.assertEqual(Path("sub/README.md").read_text(encoding="utf-8"), "REQ-001\n")
        self.assertEqual(read_json(vendored / "requirements.json")["requirements"][0]["id"], "REQ-001")

    def test_cli_wiring(self) -> None:
        args = ma.build_parser().parse_args(["requirement", "renumber", "--prefix", "ARB", "--dry-run", "--paths", "a/*.md", "b/*.md"])
        self.assertEqual((args.requirement_command, args.prefix, args.dry_run, args.paths), ("renumber", "ARB", True, ["a/*.md", "b/*.md"]))


# --- Schema --------------------------------------------------------------------------------------


def schema_problems(data: dict, schema: dict) -> list[str]:
    """The subset of JSON Schema the registry schema uses (required, properties, additionalProperties,
    propertyNames, pattern, enum, arrays of objects), enough to say whether the schema accepts the data."""
    problems: list[str] = []

    def check(value, node, where: str) -> None:
        if "enum" in node and value not in node["enum"]:
            problems.append(f"{where}: not in enum")
        kind = node.get("type")
        if kind == "object":
            if not isinstance(value, dict):
                problems.append(f"{where}: not an object")
                return
            for key in node.get("required", []):
                if key not in value:
                    problems.append(f"{where}: missing {key}")
            names = node.get("propertyNames", {})
            extra = node.get("additionalProperties", True)
            for key, item in value.items():
                if "pattern" in names and not re.fullmatch(names["pattern"], key):
                    problems.append(f"{where}.{key}: key does not match {names['pattern']}")
                if key in node.get("properties", {}):
                    check(item, node["properties"][key], f"{where}.{key}")
                elif extra is False:
                    problems.append(f"{where}.{key}: not allowed")
                elif isinstance(extra, dict):
                    check(item, extra, f"{where}.{key}")
        elif kind == "array":
            if not isinstance(value, list):
                problems.append(f"{where}: not an array")
                return
            for index, item in enumerate(value):
                check(item, node.get("items", {}), f"{where}[{index}]")
        elif kind == "string":
            if not isinstance(value, str):
                problems.append(f"{where}: not a string")
            elif "pattern" in node and not re.search(node["pattern"], value):
                problems.append(f"{where}: does not match {node['pattern']}")

    check(data, schema, "$")
    return problems


class TestSchema(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = read_json(SCHEMA_PATH)

    def test_the_registry_validates_with_and_without_aliases(self) -> None:
        self.assertEqual(schema_problems(registry("REQ", [requirement("REQ-001")]), self.schema), [])
        self.assertEqual(schema_problems(registry("ARB", [requirement("ARB-001")], {"REQ-001": "ARB-001"}), self.schema), [])
        self.assertEqual(schema_problems(registry("ARB", [requirement("ARB-001")], {}), self.schema), [])

    def test_malformed_aliases_are_rejected(self) -> None:
        self.assertTrue(schema_problems(registry("ARB", [requirement("ARB-001")], {"req-1": "ARB-001"}), self.schema))
        self.assertTrue(schema_problems(registry("ARB", [requirement("ARB-001")], {"REQ-001": "arb-1"}), self.schema))
        self.assertTrue(schema_problems(registry("ARB", [requirement("ARB-001")], {"REQ-001": 1}), self.schema))
        self.assertTrue(schema_problems({**registry("ARB", [requirement("ARB-001")]), "aliases": {}}, self.schema))

    def test_the_real_registry_still_validates(self) -> None:
        self.assertEqual(schema_problems(read_json(ROOT / ".ai/requirements/requirements.json"), self.schema), [])


if __name__ == "__main__":
    unittest.main()
