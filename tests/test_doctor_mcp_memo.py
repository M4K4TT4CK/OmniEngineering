"""Tests for the memo behind `omni doctor`'s live MCP probe (REQ-050).

A successful probe of a stdio server is remembered in the git directory, keyed on the digest of `.mcp.json`,
PATH and each server's resolved executable (path, mtime, size); within a day a matching record stands in for
the launch, `--probe` forces the launch, and any change, failure or age invalidates the record. The probe is
counted by wrapping `probe_mcp_server`; the server is a fake stdio script in a temporary git repository.
Stdlib only. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_ai as ma  # noqa: E402

FAKE_SERVER = '''
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    if message.get("method") == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "fake", "version": "1"}}
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
    elif message.get("method") == "tools/list":
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"tools": [{"name": "ping"}]}}), flush=True)
'''


class MemoFixture(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self._cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self._cwd)
        subprocess.run(["git", "init", "-q"], check=True, capture_output=True)
        Path("server.py").write_text(FAKE_SERVER, encoding="utf-8")
        self.calls: list[tuple] = []
        original = ma.probe_mcp_server

        def counted(*args, **kwargs):
            self.calls.append(args)
            return original(*args, **kwargs)

        patcher = mock.patch.object(ma, "probe_mcp_server", side_effect=counted)
        patcher.start()
        self.addCleanup(patcher.stop)
        env_patcher = mock.patch.dict(os.environ, {ma.MCP_PROBE_NO_MEMO_ENV: ""})
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def register(self, servers: dict | None = None) -> None:
        servers = servers if servers is not None else {"fake": {"command": sys.executable, "args": ["server.py"]}}
        Path(".mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")

    def doctor(self, force_probe: bool = False) -> ma.DoctorReport:
        report = ma.DoctorReport()
        ma.validate_mcp_registrations(report, force_probe=force_probe)
        return report

    def memo_file(self) -> Path:
        memo = ma.mcp_probe_memo_file()
        assert memo is not None
        return memo

    def memo(self) -> dict:
        return json.loads(self.memo_file().read_text(encoding="utf-8"))


class TestMemoHit(MemoFixture):
    def test_second_doctor_within_a_day_does_not_launch_the_server(self) -> None:
        self.register()
        first = self.doctor()
        self.assertEqual(first.errors, [])
        self.assertEqual(len(self.calls), 1)
        self.assertIn("answers initialize and lists 1 tool(s)", first.passed[0])
        self.assertNotIn("probed", first.passed[0])

        second = self.doctor()
        self.assertEqual(second.errors, [])
        self.assertEqual(len(self.calls), 1, "the memo should have stood in for the launch")
        self.assertIn("MCP server 'fake' (fake) answers initialize and lists 1 tool(s) (probed ", second.passed[0])
        self.assertIn("run omni doctor --probe to re-check", second.passed[0])

    def test_memo_lives_in_the_git_directory_and_records_the_key(self) -> None:
        self.register()
        self.doctor()
        memo_path = self.memo_file()
        git_dir = Path(subprocess.run(["git", "rev-parse", "--git-dir"], check=True, capture_output=True, text=True).stdout.strip())
        self.assertEqual(memo_path.parent.resolve(), git_dir.resolve())
        self.assertEqual(subprocess.run(["git", "status", "--porcelain"], check=True, capture_output=True, text=True).stdout.count("omni-mcp-probe"), 0)
        memo = self.memo()
        self.assertEqual(memo["registration_digest"], ma.mcp_probe_memo_key(Path(".mcp.json").read_text(encoding="utf-8"))["registration_digest"])
        self.assertEqual(memo["path"], os.environ.get("PATH", ""))
        record = memo["servers"]["fake"]
        self.assertEqual(record["server_name"], "fake")
        self.assertEqual(record["tools"], ["ping"])
        self.assertEqual(set(record["executable"]), {"path", "mtime_ns", "size"})
        self.assertEqual(record["executable"], ma.mcp_executable_fingerprint(sys.executable))
        self.assertIsNotNone(ma._arbiter_timestamp(record["probed_at"]))

    def test_build_doctor_report_uses_the_memo_by_default(self) -> None:
        self.register()
        self.doctor()
        ma.build_doctor_report()  # what `omni gate` calls: never a forced probe
        self.assertEqual(len(self.calls), 1)

    def test_remote_servers_are_unchanged(self) -> None:
        self.register({"hosted": {"url": "https://example.invalid/mcp"}})
        report = self.doctor()
        self.assertIn("not probed", report.passed[0])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.memo()["servers"], {})


class TestInvalidation(MemoFixture):
    def test_a_changed_registration_probes_again(self) -> None:
        self.register()
        self.doctor()
        self.register({"fake": {"command": sys.executable, "args": ["server.py"], "env": {"X": "1"}}})
        self.doctor()
        self.assertEqual(len(self.calls), 2)

    @unittest.skipIf(os.name == "nt", "the wrapper is a POSIX shell script")
    def test_a_changed_executable_probes_again(self) -> None:
        wrapper = self.root / "fake-server"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{self.root / "server.py"}" "$@"\n', encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        self.register({"fake": {"command": str(wrapper), "args": []}})
        self.doctor()
        self.doctor()
        self.assertEqual(len(self.calls), 1)

        before = wrapper.stat()
        os.utime(wrapper, (before.st_atime, before.st_mtime + 10))  # same bytes, newer mtime
        self.doctor()
        self.assertEqual(len(self.calls), 2, "a newer executable mtime must invalidate the memo")
        self.doctor()
        self.assertEqual(len(self.calls), 2)

        with wrapper.open("a", encoding="utf-8") as handle:
            handle.write("# rebuilt\n")
        os.utime(wrapper, (before.st_atime, before.st_mtime + 10))  # same mtime, different size
        self.doctor()
        self.assertEqual(len(self.calls), 3, "a different executable size must invalidate the memo")

    def test_probe_flag_forces_a_launch_and_refreshes_the_memo(self) -> None:
        self.register()
        self.doctor()
        stamp = self.memo()["servers"]["fake"]["probed_at"]
        report = self.doctor(force_probe=True)
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn("probed", report.passed[0])
        self.assertGreaterEqual(self.memo()["servers"]["fake"]["probed_at"], stamp)
        self.doctor()
        self.assertEqual(len(self.calls), 2)

    def test_omni_doctor_probe_reaches_the_check(self) -> None:
        self.register()
        self.doctor()
        out = io.StringIO()
        with redirect_stdout(out):
            ma.run_doctor(argparse.Namespace(json=True, probe=True))
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(any("MCP server 'fake'" in line and "probed" not in line for line in json.loads(out.getvalue())["passed"]))
        with redirect_stdout(io.StringIO()):
            ma.run_doctor(argparse.Namespace(json=True, probe=False))
        self.assertEqual(len(self.calls), 2)

    def test_a_failed_probe_is_not_memoised(self) -> None:
        self.register({"mute": {"command": sys.executable, "args": ["-c", "print('hello, not json')"]}})
        first = self.doctor()
        self.assertEqual(len(first.errors), 1)
        self.assertNotIn("mute", self.memo()["servers"])
        second = self.doctor()
        self.assertEqual(len(second.errors), 1)
        self.assertEqual(len(self.calls), 2)

    def test_a_record_older_than_a_day_is_ignored(self) -> None:
        self.register()
        self.doctor()
        memo = self.memo()
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec="seconds")
        memo["servers"]["fake"]["probed_at"] = old
        self.memo_file().write_text(json.dumps(memo), encoding="utf-8")
        report = self.doctor()
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn("probed", report.passed[0])
        self.assertNotEqual(self.memo()["servers"]["fake"]["probed_at"], old)

    def test_a_record_from_another_path_is_ignored(self) -> None:
        self.register()
        self.doctor()
        with mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "") + os.pathsep + str(self.root / "elsewhere")}):
            self.doctor()
        self.assertEqual(len(self.calls), 2)

    def test_the_memo_can_be_switched_off(self) -> None:
        self.register()
        with mock.patch.dict(os.environ, {ma.MCP_PROBE_NO_MEMO_ENV: "1"}):
            self.doctor()
            self.doctor()
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(self.memo_file().exists())

    def test_memo_hit_judges_the_record_alone(self) -> None:
        fingerprint = {"path": "/x", "mtime_ns": 1, "size": 2}
        now = datetime.now(timezone.utc).timestamp()
        record = {"executable": fingerprint, "probed_at": datetime.fromtimestamp(now - 60, timezone.utc).isoformat(), "server_name": "s", "tools": ["t"]}
        self.assertIsNotNone(ma.mcp_probe_memo_hit(record, fingerprint, now))
        self.assertIsNone(ma.mcp_probe_memo_hit(record, {**fingerprint, "size": 3}, now))
        self.assertIsNone(ma.mcp_probe_memo_hit(record, None, now))
        self.assertIsNone(ma.mcp_probe_memo_hit(None, fingerprint, now))
        self.assertIsNone(ma.mcp_probe_memo_hit({**record, "tools": []}, fingerprint, now))
        self.assertIsNone(ma.mcp_probe_memo_hit(record, fingerprint, now + ma.MCP_PROBE_MEMO_MAX_AGE_SECONDS + 1))
        self.assertIsNone(ma.mcp_probe_memo_hit({**record, "probed_at": "yesterday"}, fingerprint, now))


class TestOutsideGit(unittest.TestCase):
    def test_without_a_git_directory_every_probe_is_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path.cwd()
            os.chdir(tmp)
            try:
                if ma.mcp_probe_memo_file() is not None:
                    self.skipTest("the temporary directory is inside a git checkout")
                Path("server.py").write_text(FAKE_SERVER, encoding="utf-8")
                Path(".mcp.json").write_text(json.dumps({"mcpServers": {"fake": {"command": sys.executable, "args": ["server.py"]}}}), encoding="utf-8")
                calls: list[tuple] = []
                original = ma.probe_mcp_server
                with mock.patch.object(ma, "probe_mcp_server", side_effect=lambda *a, **k: (calls.append(a), original(*a, **k))[1]):
                    for _ in range(2):
                        report = ma.DoctorReport()
                        ma.validate_mcp_registrations(report)
                        self.assertEqual(report.errors, [])
                self.assertEqual(len(calls), 2)
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
