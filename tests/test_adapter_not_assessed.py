"""An analyzer that could not run is not assessed, never a clean pass (ARB-059).

Found preparing for an interview: with semgrep's ruleset missing, semgrep
exited 7 with a JSON body of errors and no results, and the scan recorded it
as `ran` with nothing found. A primed cache made it worse: the scan replayed
the findings of the last run that did have rules.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from arbiter import adapters as A
from arbiter.engine import run_scan
from arbiter.policy import load_config
from arbiter.probes import REGISTRY

NAME = "fake_ruled"

# Prints one finding when its "ruleset" exists; otherwise behaves like
# semgrep without a config: exit 7, errors in JSON, no results.
SCRIPT = '''import json, pathlib, sys
here = pathlib.Path(__file__).parent
if (here / "rules" / "marker").exists():
    print(json.dumps({"results": [{"rule": "R1", "file": "a.py", "line": 1}], "errors": []}))
    sys.exit(0)
print(json.dumps({"results": [], "errors": [{"message": "unable to find a config"}]}))
sys.exit(7)
'''

MANIFEST = '''name = "{name}"
dimensions = ["quality"]
checks = 1
scope = "repo"

[requires]
binaries = []
files = {files}
network = false
timeout = 60

[invoke]
argv = {argv}
parse = "json"
ok_exit = [0, 1]

[map]
findings = "$.results[*]"
rule_id = "rule"
title = "rule"
path = "file"
line = "line"
severity_const = "low"
dimension = "quality"
'''


@pytest.fixture
def fake(tmp_path):
    """Register one fake adapter for this test only; REGISTRY is global."""
    home = tmp_path / "fake"
    (home / "rules").mkdir(parents=True)
    script = home / f"{NAME}.py"
    script.write_text(SCRIPT, encoding="utf-8")
    marker = home / "rules" / "marker"

    def register(files: list[str]) -> None:
        (home / f"{NAME}.adapter.toml").write_text(
            MANIFEST.format(name=NAME, files=json.dumps(files),
                            argv=json.dumps([sys.executable, str(script), "{workdir}"])),
            encoding="utf-8")
        A.register_adapters()
        A.register_adapters([str(home)])

    yield SimpleNamespace(marker=marker, register=register)
    REGISTRY[:] = [p for p in REGISTRY if p.name != NAME]
    A._REGISTERED.discard(NAME)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    return root


def _scan(root: Path, use_cache: bool):
    cfg = copy.deepcopy(load_config(None))
    cfg["cache"] = {"enabled": use_cache}
    report = run_scan([str(root)], cfg, only=[NAME], use_adapters=False,
                      use_cache=use_cache, cache_path=str(root.parent / "cache.json"))
    return next(p for p in report.probes if p.name == NAME), report


def test_a_missing_required_file_is_not_assessed_and_named(tmp_path, fake):
    fake.register([str(fake.marker)])
    outcome, report = _scan(_repo(tmp_path), use_cache=False)
    assert outcome.status == "skipped"
    assert str(fake.marker) in outcome.reason
    assert "tools/install_tools.sh" in outcome.reason
    assert not [f for f in report.findings if f.probe == NAME]


def test_the_required_file_is_checked_before_any_cache_replay(tmp_path, fake):
    root = _repo(tmp_path)
    fake.register([str(fake.marker)])
    fake.marker.write_text("ok", encoding="utf-8")
    first, _ = _scan(root, use_cache=True)
    assert first.status == "ran" and first.finding_count == 1

    fake.marker.unlink()
    second, report = _scan(root, use_cache=True)
    assert second.status == "skipped", "replayed a run whose ruleset is gone"
    assert not [f for f in report.findings if f.probe == NAME]


def test_installed_rules_still_run(tmp_path, fake):
    fake.register([str(fake.marker)])
    fake.marker.write_text("ok", encoding="utf-8")
    outcome, report = _scan(_repo(tmp_path), use_cache=False)
    assert outcome.status == "ran"
    assert [f.rule_id for f in report.findings if f.probe == NAME] == [f"{NAME}/R1"]


def test_a_failed_exit_with_an_error_body_is_an_error_not_a_pass(tmp_path, fake):
    """No declared files: the failed exit itself has to be caught."""
    fake.register([])
    outcome, _ = _scan(_repo(tmp_path), use_cache=False)
    assert outcome.status == "error"
    assert "exited 7" in outcome.reason
    assert "unable to find a config" in outcome.reason


def _ctx(root: Path) -> SimpleNamespace:
    files = [SimpleNamespace(path="a.py")]
    return SimpleNamespace(repos=[SimpleNamespace(id="root", path=str(root))],
                           inventory=SimpleNamespace(by_repo={"root": files}))


def test_a_failed_exit_that_still_parsed_findings_keeps_them(tmp_path):
    """Control: a tool that exits non-zero but reports results (semgrep on a
    partial rule error) keeps them, as before."""
    body = json.dumps({"results": [{"rule": "R1", "file": "a.py", "line": 1}]})
    a = A.Adapter(name="t", argv=[sys.executable, "-c", f"print({body!r}); raise SystemExit(2)"],
                  mapping={"findings": "$.results[*]", "rule_id": "rule", "path": "file", "line": "line"},
                  ok_exit=[0, 1], timeout=30)
    found = a.run(_ctx(tmp_path))
    assert [f.rule_id for f in found] == ["t/R1"]


def test_shipped_semgrep_requires_its_ruleset(tmp_path, monkeypatch):
    """The real manifest declares the ruleset marker under the cache dir."""
    semgrep = next(a for a in A.load_all() if a.name == "semgrep")
    assert semgrep.files == ["{cache}/semgrep-rules/.arbiter-ref"]

    from arbiter.probes import Probe
    monkeypatch.setenv("ARBITER_CACHE_DIR", str(tmp_path))
    probe = Probe(name="semgrep", dimensions=["security"], checks=1,
                  run=lambda ctx: [], files=semgrep.files)
    blocked, why = probe.prevented()
    assert blocked and "semgrep-rules" in why

    (tmp_path / "semgrep-rules").mkdir()
    (tmp_path / "semgrep-rules" / ".arbiter-ref").write_text("ref", encoding="utf-8")
    assert probe.prevented() == (False, "")
