"""Tests for `omni requirement archive`: only terminal requirements (completed or withdrawn) leave the live
registry, and `--id` refuses to move live work rather than silently hiding it. Stdlib only. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_ai as ma  # noqa: E402


def requirement(req_id: str, status: str) -> dict:
    return {
        "id": req_id, "category": "Feature", "title": f"{req_id} title", "description": "d", "priority": "low",
        "status": status, "minimum_access_scope": [], "do_not_access": [], "acceptance_criteria": [],
        "validation_required": [], "documentation_required": [], "risk_notes": [],
    }


class ArchiveFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._cwd = Path.cwd()
        os.chdir(self._tmp.name)
        self.addCleanup(os.chdir, self._cwd)
        ma.REQUIREMENTS_PATH.parent.mkdir(parents=True)
        self.write([
            requirement("REQ-001", "completed"),
            requirement("REQ-002", "completed"),
            requirement("REQ-003", "withdrawn"),
            requirement("REQ-004", "pending"),
            requirement("REQ-005", "completed"),
        ])

    def write(self, items: list[dict]) -> None:
        ma.write_json(ma.REQUIREMENTS_PATH, {"version": "1.0.0", "requirement_id_prefix": "REQ", "requirements": items})

    def archive(self, ids: str | None = None, keep_recent: int = 25, dry_run: bool = False) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ma.run_requirement_archive(argparse.Namespace(id=ids, keep_recent=keep_recent, dry_run=dry_run))
        return code, out.getvalue(), err.getvalue()

    def active_ids(self) -> list[str]:
        return [item["id"] for item in json.loads(ma.REQUIREMENTS_PATH.read_text(encoding="utf-8"))["requirements"]]

    def archived_ids(self) -> list[str]:
        if not ma.REQUIREMENTS_ARCHIVE_PATH.is_file():
            return []
        return [item["id"] for item in json.loads(ma.REQUIREMENTS_ARCHIVE_PATH.read_text(encoding="utf-8"))["requirements"]]


class TestDefaultSweep(ArchiveFixture):
    def test_moves_old_completed_and_every_withdrawn_entry(self) -> None:
        code, out, _ = self.archive(keep_recent=1)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.active_ids(), ["REQ-004", "REQ-005"])
        self.assertEqual(self.archived_ids(), ["REQ-001", "REQ-002", "REQ-003"])

    def test_pending_work_never_moves(self) -> None:
        self.archive(keep_recent=0)
        self.assertIn("REQ-004", self.active_ids())
        self.assertNotIn("REQ-004", self.archived_ids())

    def test_dry_run_writes_nothing(self) -> None:
        code, out, _ = self.archive(keep_recent=0, dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("Dry run", out)
        self.assertEqual(len(self.active_ids()), 5)
        self.assertEqual(self.archived_ids(), [])


class TestTargetedArchive(ArchiveFixture):
    def test_id_moves_exactly_the_named_terminal_entries(self) -> None:
        code, _, _ = self.archive(ids="REQ-002,REQ-003")
        self.assertEqual(code, 0)
        self.assertEqual(self.active_ids(), ["REQ-001", "REQ-004", "REQ-005"])
        self.assertEqual(self.archived_ids(), ["REQ-002", "REQ-003"])

    def test_id_refuses_live_work_and_writes_nothing(self) -> None:
        code, _, err = self.archive(ids="REQ-002,REQ-004")
        self.assertEqual(code, 1)
        self.assertIn("Refusing to archive REQ-004", err)
        self.assertEqual(len(self.active_ids()), 5)
        self.assertEqual(self.archived_ids(), [])

    def test_unknown_id_is_an_error(self) -> None:
        code, _, err = self.archive(ids="REQ-999")
        self.assertEqual(code, 1)
        self.assertIn("REQ-999", err)


class TestStatusVocabulary(unittest.TestCase):
    def test_withdrawn_is_a_valid_terminal_status(self) -> None:
        self.assertIn("withdrawn", ma.REQUIREMENT_STATUSES)
        self.assertEqual(ma.TERMINAL_REQUIREMENT_STATUSES, {"completed", "withdrawn"})
        self.assertEqual(ma.requirement_shape_problems(requirement("REQ-001", "withdrawn")), [])

    def test_schema_enum_matches_the_cli_vocabulary(self) -> None:
        schema = json.loads((ROOT / ".ai/schemas/requirements.schema.json").read_text(encoding="utf-8"))
        enum = schema["properties"]["requirements"]["items"]["properties"]["status"]["enum"]
        self.assertEqual(sorted(enum), sorted(ma.REQUIREMENT_STATUSES))


if __name__ == "__main__":
    unittest.main()
