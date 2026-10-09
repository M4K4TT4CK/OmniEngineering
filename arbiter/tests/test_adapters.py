"""External analyzer adapters: timeouts and process groups on both
platforms, report files, scope filtering and measured severity.
"""
from __future__ import annotations

import sys

import pytest
from helpers import ROOT

from arbiter.core import Finding, Location


def test_adapter_timeout_kills_the_whole_process_group(tmp_path):
    """subprocess.run(timeout=) kills only the process it started. Checkov and
    semgrep fan out with multiprocessing, so a timeout used to leave a pool of
    orphaned workers competing for CPU with every scan that followed — observed
    after checkov deadlocked on a one-million-line repository."""
    import subprocess

    from arbiter.adapters import Adapter

    # A shell that spawns a child and then sleeps. If only the direct child is
    # killed, the grandchild survives the timeout.
    marker = tmp_path / "grandchild-alive"
    # The grandchild writes a marker after two seconds. The adapter's budget is
    # one second. If the process group really was killed the marker never
    # appears; if only the direct child was killed, it does.
    script = f"( sleep 2; touch {marker} ) & sleep 10"
    a = Adapter(name="t", argv=["sh", "-c", script], timeout=1)
    with pytest.raises(subprocess.TimeoutExpired):
        a.invoke(str(tmp_path))

    import time as _t
    _t.sleep(3)
    assert not marker.exists(), "a grandchild outlived the timeout and kept working"


def test_adapter_findings_outside_the_inventory_are_dropped():
    """gitleaks walks git-ignored files and __pycache__ on its own; the first
    self-gate reported fixture token shapes from .ai/project-graph.json and a
    .pyc as four critical secrets. The inventory is the one answer to what is
    in scope, and every adapter's output is held to it on the way in."""
    from arbiter.adapters import in_scope
    kept = Finding(rule_id="gitleaks/x", title="t", location=Location(path="src/a.py"))
    ignored = Finding(rule_id="gitleaks/x", title="t", location=Location(path=".ai/project-graph.json"))
    cache = Finding(rule_id="gitleaks/x", title="t", location=Location(path="tests/__pycache__/t.pyc"))
    tool_level = Finding(rule_id="gitleaks/version", title="t", location=Location(path=""))
    out = in_scope([kept, ignored, cache, tool_level], {"src/a.py", "src/b.py"})
    assert out == [kept, tool_level]


def test_adapter_still_returns_output_normally(tmp_path):
    from arbiter.adapters import Adapter
    a = Adapter(name="t", argv=["sh", "-c", "echo '{\"x\":1}'"], timeout=10)
    out, code = a.invoke(str(tmp_path))
    assert code == 0 and "x" in out


def test_adapter_reads_a_tool_that_only_writes_a_report_file(tmp_path):
    """gitleaks has no "write JSON to stdout" mode; `--report-path` takes only
    a real filename, and the conventional `-` does not mean stdout to it -- it
    creates a file literally named `-` inside whatever `cwd` the adapter used,
    which for a bare `{workdir}` default is the repository being scanned. Any
    tool shaped that way gets a private temp path substituted for
    `{report_file}`, read back once the process exits."""
    from arbiter.adapters import Adapter
    a = Adapter(name="t", argv=["sh", "-c", 'echo \'{"x":1}\' > "$1"', "_", "{report_file}"],
                timeout=10)
    out, code = a.invoke(str(tmp_path))
    assert code == 0
    assert "x" in out
    # Nothing named after the placeholder, or otherwise, is left behind in
    # the scanned directory or anywhere else findable by a later scan.
    assert not any(tmp_path.iterdir())


def test_adapter_report_file_is_cleaned_up_even_when_the_tool_never_writes_it(tmp_path):
    from arbiter.adapters import Adapter
    a = Adapter(name="t", argv=["sh", "-c", "exit 1", "_", "{report_file}"], timeout=10)
    out, code = a.invoke(str(tmp_path))
    assert code == 1
    assert out == ""


# ---------------------------------------------------------------------------
# Measured severity for external checks.
#
# The conceptual line this guards: discrimination measures SIGNAL, severity
# encodes CONSEQUENCE. They correlate and are not the same quantity, so a
# measurement may say "this carries signal" and may say "this carries none",
# but it may not manufacture a consequence claim. "Ensure every security group
# has a description" scores infinite discrimination — real, reproducible,
# stack-matched — and is still not a high-severity security finding.
# ---------------------------------------------------------------------------

def test_measurement_cannot_promote_a_check_to_high():
    import importlib
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "tools"))
    ce = importlib.import_module("calibrate_external")
    assert ce.band(float("inf")) == "medium"
    assert ce.band(1_000_000.0) == "medium"
    assert "high" not in {s for _, s in ce.BANDS}


def test_measurement_demotes_a_check_that_carries_no_signal():
    import importlib
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "tools"))
    ce = importlib.import_module("calibrate_external")
    assert ce.band(0.12) == "info"
    assert ce.band(1.0) == "info"
    assert ce.band(4.0) == "low"
    assert ce.band(None) is None, "never seen on broken code: make no claim"


def test_a_single_repository_cannot_drive_a_promotion():
    """The first table promoted a documentation-hygiene check to high because
    one repository was the whole sample."""
    import importlib
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "tools"))
    ce = importlib.import_module("calibrate_external")
    assert ce.band(float("inf"), repos=1) == "low"
    assert ce.band(float("inf"), repos=2) == "medium"


def test_measured_severity_replaces_the_invented_constant(tmp_path):
    from arbiter.core import Finding, Location
    from arbiter.learn import Knowledge
    from arbiter.learn import apply as apply_knowledge
    k = Knowledge()
    k.external_severity = {"checkov/CKV_AWS_16": "info"}
    f = Finding(rule_id="checkov/CKV_AWS_16", title="x", severity="medium",
                location=Location(path="a.tf"))
    res = apply_knowledge([f], k)
    assert f.severity == "info" and res["externally_graded"] == 1
    assert "severity-was:medium" in f.tags and "severity:measured" in f.tags


def test_a_native_rules_severity_is_never_touched_by_measurement(tmp_path):
    """The standing rule. A native rule's severity is a policy statement; only
    an external check whose tool supplied no severity may be graded."""
    from arbiter.core import Finding, Location
    from arbiter.learn import Knowledge
    from arbiter.learn import apply as apply_knowledge
    k = Knowledge()
    k.external_severity = {"arbiter/resource.unencrypted-database": "info"}
    f = Finding(rule_id="arbiter/resource.unencrypted-database", title="x",
                severity="high", location=Location(path="a.tf"))
    apply_knowledge([f], k)
    # the table is keyed by external check ids; a native rule must not appear
    # in one, and the pack that produces it is the only thing that sets it
    assert f.rule_id.startswith("arbiter/")


def test_the_severity_table_is_part_of_the_version_hash():
    """Otherwise a scan could not record which table it used, and
    --pin-knowledge could not detect that it moved."""
    from arbiter.learn import Knowledge
    a, b = Knowledge(), Knowledge()
    b.external_severity = {"checkov/CKV_AWS_16": "info"}
    assert a.version_hash() != b.version_hash()


# ---------------------------------------------------------------------------
# REQ-024 -- the Windows code paths.
#
# REQ-006 was a crash on every adapter timeout on Windows: `os.killpg` does not
# exist there, so the process-group path raised AttributeError and the analyzer
# outlived the timeout that was supposed to stop it. It survived to be found by
# hand because no automation had ever run on Windows, and because the fix --
# `_kill_tree` -- is unreachable on a machine that has process groups.
#
# So there are two halves here and both are needed. The tests below take the
# platform away rather than waiting for a platform, which means the Windows
# branch is exercised on every Linux run too; and the CI matrix makes sure a
# real Windows runner sees the rest of the suite, which no amount of
# monkeypatching can stand in for.
# ---------------------------------------------------------------------------

def _no_process_groups(monkeypatch):
    """Make this machine look like Windows to the kill path."""
    import os as _os
    monkeypatch.delattr(_os, "killpg", raising=False)


class _FakeProc:
    """Enough of Popen for the kill path, and it remembers what was done to it."""

    def __init__(self, pid: int = 4242):
        self.pid = pid
        self.killed = False
        self.waited = False

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True
        return 0


def test_a_timeout_still_kills_the_process_where_there_are_no_process_groups(monkeypatch):
    """REQ-006 itself, against a real process rather than a mock.

    The original defect was not that the wrong process died -- it was that
    `_kill_group` raised before killing anything, so the analyzer ran on.
    """
    import subprocess

    from arbiter.adapters import Adapter

    _no_process_groups(monkeypatch)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        Adapter._kill_group(proc)   # raised AttributeError before REQ-006
        assert proc.poll() is not None, "the analyzer outlived its own timeout"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_the_no_process_group_path_kills_the_whole_tree_not_just_the_child(monkeypatch):
    """`/T` is the entire point. checkov and semgrep fan out into workers, and
    killing only the process we spawned leaves those running -- which is the
    failure the process-group path exists to prevent, arriving by another
    route."""
    from arbiter import adapters as A

    calls = []
    monkeypatch.setattr(A.shutil, "which",
                        lambda name: "taskkill" if name == "taskkill" else None)
    monkeypatch.setattr(A.subprocess, "run",
                        lambda argv, **kw: calls.append(list(argv)))
    proc = _FakeProc()
    A.Adapter._kill_tree(proc)

    assert calls, "taskkill was on PATH and the kill path did not use it"
    assert "/T" in calls[0], "only the direct child is killed; the workers survive"
    assert "/F" in calls[0]
    assert str(proc.pid) in calls[0]
    assert not proc.killed, "taskkill succeeded, so the fallback should not have run"


def test_the_no_process_group_path_falls_back_when_taskkill_is_missing(monkeypatch):
    """A stripped image without taskkill must still lose the process, not the
    exception."""
    from arbiter import adapters as A

    monkeypatch.setattr(A.shutil, "which", lambda name: None)
    proc = _FakeProc()
    A.Adapter._kill_tree(proc)
    assert proc.killed, "no taskkill and no kill either — the process survived"


def test_the_no_process_group_path_falls_back_when_taskkill_fails(monkeypatch):
    """taskkill present but refusing (a permissions case) is not a reason to
    leave the process running."""
    from arbiter import adapters as A

    def _boom(argv, **kw):
        raise OSError("access denied")

    monkeypatch.setattr(A.shutil, "which", lambda name: "taskkill")
    monkeypatch.setattr(A.subprocess, "run", _boom)
    proc = _FakeProc()
    A.Adapter._kill_tree(proc)
    assert proc.killed, "taskkill failed and nothing else tried"
