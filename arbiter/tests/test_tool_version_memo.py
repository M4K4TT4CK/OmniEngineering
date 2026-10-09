"""The tool-version memo (ARB-045).

Registering the adapters used to run every analyzer's `--version`, on every
invocation, including the partial-scan gate that never runs an adapter.
`Adapter.tool_version` now remembers the answer per installed binary in
`cache_dir()/tool-versions.json`. Each test here pairs the claim with its
control: the counter the fake tool bumps is what tells a memo hit from a
probe that merely gave the same answer.

The fake tool is a Python script run through `sys.executable`, so the same
test runs on Windows, where a bare script is not an executable.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from arbiter import adapters as A
from arbiter.adapters import Adapter

FAKE_TOOL = '''import pathlib
VERSION = "1.0.0"
counter = pathlib.Path(__file__).with_name("counter.txt")
n = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
counter.write_text(str(n + 1), encoding="utf-8")
print("fake-tool " + VERSION)
'''


@pytest.fixture(autouse=True)
def _own_cache_dir(tmp_path, monkeypatch):
    """Never touch the operator's real memo, and start every test cold."""
    monkeypatch.setenv("ARBITER_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("ARBITER_NO_VERSION_MEMO", raising=False)


def _fake_tool(tmp_path: Path) -> tuple[Path, Path]:
    tool = tmp_path / "tool" / "fake_tool.py"
    tool.parent.mkdir(parents=True, exist_ok=True)
    tool.write_text(FAKE_TOOL, encoding="utf-8")
    return tool, tool.with_name("counter.txt")


def _probes(counter: Path) -> int:
    return int(counter.read_text(encoding="utf-8")) if counter.exists() else 0


def _adapter(tool: Path, name: str = "fake") -> Adapter:
    return Adapter(name=name, version_argv=[sys.executable, str(tool)])


def test_the_first_call_probes_and_the_second_is_answered_from_the_memo(tmp_path):
    tool, counter = _fake_tool(tmp_path)
    a = _adapter(tool)
    assert a.tool_version() == "fake-tool 1.0.0"
    assert _probes(counter) == 1
    assert A.version_memo_path().is_file(), "the answer was written down"

    assert a.tool_version() == "fake-tool 1.0.0"
    assert _probes(counter) == 1, "a memo hit runs nothing"
    # A second Adapter object with the same version_argv shares the entry:
    # the memo is keyed on the command, not on the Python object.
    assert _adapter(tool, "other").tool_version() == "fake-tool 1.0.0"
    assert _probes(counter) == 1

    doc = json.loads(A.version_memo_path().read_text(encoding="utf-8"))
    assert doc["schema_version"] == A.VERSION_MEMO_SCHEMA
    (entry,) = doc["tools"].values()
    assert entry["version"] == "fake-tool 1.0.0"
    stamped = [s[0] for s in entry["stamp"]]
    assert stamped == [sys.executable, str(tool)], \
        "the interpreter and the script are both part of the key"


def test_touching_the_executable_invalidates_its_entry(tmp_path):
    tool, counter = _fake_tool(tmp_path)
    a = _adapter(tool)
    a.tool_version()
    a.tool_version()
    assert _probes(counter) == 1

    # A new build: different bytes, later mtime. Re-probed, new answer.
    tool.write_text(FAKE_TOOL.replace('"1.0.0"', '"2.0.0"') + "# rebuilt\n", encoding="utf-8")
    later = tool.stat().st_mtime + 10
    os.utime(tool, (later, later))
    assert a.tool_version() == "fake-tool 2.0.0"
    assert _probes(counter) == 2
    # and the new answer is what the memo now holds
    assert a.tool_version() == "fake-tool 2.0.0"
    assert _probes(counter) == 2

    # The mtime alone is enough: the same bytes with a later timestamp is a
    # tool that may have been reinstalled, and the probe is cheap next to a
    # wrong version string.
    later += 10
    os.utime(tool, (later, later))
    a.tool_version()
    assert _probes(counter) == 3

    # The documented limit: a same-size edit under a pinned timestamp is
    # invisible to the key, so the old string is served until the memo is
    # cleared (the risk note on ARB-045).
    tool.write_text(FAKE_TOOL.replace('"1.0.0"', '"3.0.0"') + "# rebuilt\n", encoding="utf-8")
    os.utime(tool, (later, later))
    assert a.tool_version() == "fake-tool 2.0.0"
    assert _probes(counter) == 3
    A.version_memo_path().unlink()
    assert a.tool_version() == "fake-tool 3.0.0"
    assert _probes(counter) == 4


def test_the_environment_variable_bypasses_the_memo(tmp_path, monkeypatch):
    tool, counter = _fake_tool(tmp_path)
    a = _adapter(tool)
    monkeypatch.setenv("ARBITER_NO_VERSION_MEMO", "1")
    assert a.tool_version() == "fake-tool 1.0.0"
    assert a.tool_version() == "fake-tool 1.0.0"
    assert _probes(counter) == 2, "every call probes"
    assert not A.version_memo_path().exists(), "and nothing is written"

    # A memo written earlier is not read either.
    monkeypatch.delenv("ARBITER_NO_VERSION_MEMO")
    a.tool_version()
    assert _probes(counter) == 3 and A.version_memo_path().exists()
    monkeypatch.setenv("ARBITER_NO_VERSION_MEMO", "1")
    a.tool_version()
    assert _probes(counter) == 4

    # Control: only the value "1" switches it off.
    monkeypatch.setenv("ARBITER_NO_VERSION_MEMO", "")
    a.tool_version()
    assert _probes(counter) == 4


def test_a_missing_binary_is_never_probed(tmp_path, monkeypatch):
    def boom(argv):
        raise AssertionError(f"probed {argv}")
    monkeypatch.setattr(Adapter, "_probe_version", staticmethod(boom))

    missing = Adapter(name="ghost", version_argv=["arbiter-no-such-tool-xyz", "--version"])
    assert missing.tool_version() == ""
    assert not A.version_memo_path().exists(), "nothing to remember about a tool that is not there"
    assert Adapter(name="mute").tool_version() == "", "no version_argv, no probe"

    # Control: with the tool present the (patched) probe is reached.
    tool, _ = _fake_tool(tmp_path)
    with pytest.raises(AssertionError, match="probed"):
        _adapter(tool).tool_version()


def test_a_corrupt_or_unwritable_memo_falls_back_to_probing(tmp_path, monkeypatch):
    tool, counter = _fake_tool(tmp_path)
    a = _adapter(tool)
    path = A.version_memo_path()
    path.parent.mkdir(parents=True)
    for junk in ("{not json", '{"schema_version": 99, "tools": {}}',
                 '{"schema_version": 1, "tools": "nope"}', "", "[]"):
        path.write_text(junk, encoding="utf-8")
        before = _probes(counter)
        assert a.tool_version() == "fake-tool 1.0.0"
        assert _probes(counter) == before + 1, "treated as empty: probed"
        json.loads(path.read_text(encoding="utf-8"))   # and left valid behind
    # Control: the valid file it wrote is then believed.
    before = _probes(counter)
    a.tool_version()
    assert _probes(counter) == before

    # An entry whose shape is wrong is a miss, not a crash.
    doc = json.loads(path.read_text(encoding="utf-8"))
    for k in doc["tools"]:
        doc["tools"][k] = {"stamp": "stale", "version": 7}
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert a.tool_version() == "fake-tool 1.0.0"
    assert _probes(counter) == before + 1

    # A cache directory that cannot be a directory: the probe still answers,
    # on every call, and nothing is raised.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("ARBITER_CACHE_DIR", str(blocker))
    before = _probes(counter)
    assert a.tool_version() == "fake-tool 1.0.0"
    assert a.tool_version() == "fake-tool 1.0.0"
    assert _probes(counter) == before + 2
    assert blocker.read_text(encoding="utf-8") == "not a directory"


def test_register_adapters_reads_the_memo_across_processes(tmp_path):
    """The memo earns its keep between invocations, where `register_adapters`'
    in-process idempotence cannot help. Two fresh interpreters register the
    same fake adapter: the first probes, the second does not."""
    tool, counter = _fake_tool(tmp_path)
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    argv = json.dumps([sys.executable, str(tool)])
    (manifests / "fake.adapter.toml").write_text(
        f'name = "fake_memo_tool"\nscope = "repo"\n[invoke]\nversion_argv = {argv}\n',
        encoding="utf-8")
    # The shipped packs are swapped for the fake one: this is a test of the
    # memo, not of whichever analyzers happen to be installed here.
    code = ("import sys, pathlib; import arbiter.adapters as A; "
            "from arbiter.probes import REGISTRY; A.PACKS = pathlib.Path(sys.argv[1]); "
            "A.register_adapters(); "
            "print(next(p.version for p in REGISTRY if p.name == 'fake_memo_tool'))")

    def register(**extra_env) -> str:
        env = {k: v for k, v in os.environ.items() if k != "ARBITER_NO_VERSION_MEMO"}
        env.update({"ARBITER_CACHE_DIR": str(tmp_path / "cache"), **extra_env})
        r = subprocess.run([sys.executable, "-c", code, str(manifests)], env=env,
                           capture_output=True, text=True, timeout=120, check=True)
        return r.stdout.strip()

    assert register() == "fake-tool 1.0.0"
    assert _probes(counter) == 1
    assert register() == "fake-tool 1.0.0"
    assert _probes(counter) == 1, "the second process read the memo"
    assert register(ARBITER_NO_VERSION_MEMO="1") == "fake-tool 1.0.0"
    assert _probes(counter) == 2, "control: the switch makes it probe"
