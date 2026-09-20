"""Regression tests for defects that shipped once and must not return.

Each test names the failure-ledger entry it guards (`omni failure show FAIL-###`).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestLineEndings(unittest.TestCase):
    def test_launcher_and_scripts_use_lf(self) -> None:
        """FAIL-001: a CRLF shebang made `./omni` fail with `env: 'python3\\r': No such file or directory`."""
        for name in ("omni", "make_ai.py", "omni_graph.py"):
            data = (ROOT / name).read_bytes()
            self.assertNotIn(b"\r", data, f"{name} contains CR characters; the shebang breaks on Linux/WSL")


class TestViewerTemplate(unittest.TestCase):
    def test_markup_has_no_literal_unicode_escapes(self) -> None:
        """FAIL-004: `\\u2264` written in HTML text (not a JS string) rendered literally in the checkbox label."""
        template = (ROOT / ".ai" / "graph-viewer" / "viewer.html").read_text(encoding="utf-8")
        markup = template.split("<script", 1)[0]
        self.assertIsNone(re.search(r"\\u[0-9a-fA-F]{4}", markup), "use an HTML entity or the character itself in markup")


if __name__ == "__main__":
    unittest.main()
