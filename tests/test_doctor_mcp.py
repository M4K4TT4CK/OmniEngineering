"""Tests for `omni doctor`'s live check of the MCP servers `.mcp.json` registers.

The check launches each stdio server and takes it through the real handshake (initialize, then tools/list)
rather than reading the registration file, because the registration can look right while the server
behind it no longer starts. `omni mcp serve` is the server under test here, so the probe and the server
are checked against each other. Stdlib only. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_ai as ma  # noqa: E402


class McpRegistrationFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self._cwd)

    def register(self, servers: dict) -> None:
        Path(".mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")

    def doctor(self) -> ma.DoctorReport:
        report = ma.DoctorReport()
        ma.validate_mcp_registrations(report)
        return report


class TestLiveProbe(McpRegistrationFixture):
    def test_omni_mcp_serve_answers_with_its_tools(self) -> None:
        self.register({"omni": {"command": sys.executable, "args": [str(ROOT / "omni"), "mcp", "serve"]}})
        report = self.doctor()
        self.assertEqual(report.errors, [])
        self.assertEqual(len(report.passed), 1)
        self.assertIn("lists", report.passed[0])

    def test_probe_returns_the_servers_tool_names(self) -> None:
        tools, server_name = ma.probe_mcp_server(sys.executable, [str(ROOT / "omni"), "mcp", "serve"], None, self.root)
        self.assertIsNotNone(tools)
        self.assertIn("graph_why", tools or [])
        self.assertIn("requirement_show", tools or [])
        self.assertTrue(server_name)

    def test_missing_command_is_an_error(self) -> None:
        self.register({"ghost": {"command": "omni-doctor-test-no-such-command-xyz", "args": []}})
        report = self.doctor()
        self.assertEqual(len(report.errors), 1)
        self.assertIn("command not found", report.errors[0])

    def test_server_that_exits_without_answering_is_an_error(self) -> None:
        self.register({"mute": {"command": sys.executable, "args": ["-c", "print('hello, not json')"]}})
        report = self.doctor()
        self.assertEqual(len(report.errors), 1)
        self.assertIn("exited before answering", report.errors[0])

    def test_server_that_hangs_times_out(self) -> None:
        tools, detail = ma.probe_mcp_server(
            sys.executable, ["-c", "import time; time.sleep(30)"], None, self.root, timeout=1.0,
        )
        self.assertIsNone(tools)
        self.assertIn("no answer within", detail)


class TestRegistrationShape(McpRegistrationFixture):
    def test_absent_registration_is_not_a_finding(self) -> None:
        report = self.doctor()
        self.assertEqual((report.errors, report.warnings, report.passed), ([], [], []))

    def test_remote_server_is_passed_without_probing(self) -> None:
        self.register({"hosted": {"url": "https://example.invalid/mcp"}})
        report = self.doctor()
        self.assertEqual(report.errors, [])
        self.assertIn("not probed", report.passed[0])

    def test_malformed_entries_are_reported_by_name(self) -> None:
        self.register({
            "no-command": {"args": ["x"]},
            "bad-args": {"command": sys.executable, "args": "mcp serve"},
            "bad-env": {"command": sys.executable, "args": [], "env": {"A": 1}},
            "not-an-object": "python",
        })
        report = self.doctor()
        self.assertEqual(len(report.errors), 4)
        for name in ("no-command", "bad-args", "bad-env", "not-an-object"):
            self.assertTrue(any(f"'{name}'" in error for error in report.errors), name)

    def test_invalid_json_is_an_error(self) -> None:
        Path(".mcp.json").write_text("{not json", encoding="utf-8")
        report = self.doctor()
        self.assertEqual(len(report.errors), 1)
        self.assertIn("Invalid JSON", report.errors[0])


if __name__ == "__main__":
    unittest.main()
