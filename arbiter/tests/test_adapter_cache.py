"""Adapters run concurrently and replay when nothing they read changed (ARB-047).

Two fake adapters, each a Python script started through `sys.executable`
so the same test runs on Windows. Every script bumps a counter file on
each run: the counter is what separates "replayed from the memo" from
"ran again and happened to agree". Every claim has its control.
"""
from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

import pytest

from arbiter import adapters as A
from arbiter import cache as C
from arbiter.engine import adapter_workers, run_scan
from arbiter.policy import load_config
from arbiter.probes import REGISTRY

NAMES = ("fake_alpha", "fake_beta")

# The script reads its own knobs from files beside it: a sleep, and a
# "mode" that changes what it prints without any file in the scanned tree
# changing, which is how a divergence is manufactured.
SCRIPT = '''import json, pathlib, sys, time
here = pathlib.Path(__file__).parent
name = pathlib.Path(__file__).stem
counter = here / (name + ".count")
n = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
counter.write_text(str(n + 1), encoding="utf-8")
sleep = here / (name + ".sleep")
if sleep.exists():
    time.sleep(float(sleep.read_text(encoding="utf-8")))
mode = here / (name + ".mode")
mode = mode.read_text(encoding="utf-8").strip() if mode.exists() else "a"
if mode == "crash":
    sys.exit(2)
rows = [{"rule": "R1", "msg": "planted by " + name + " (" + mode + ")",
         "file": "a.py", "line": 1}]
if mode == "b":
    rows.append({"rule": "R2", "msg": "a second finding", "file": "b.py", "line": 1})
print(json.dumps(rows))
'''

MANIFEST = '''name = "{name}"
dimensions = ["quality"]
checks = 2
scope = "repo"

[requires]
binaries = []
network = false
timeout = 60

[invoke]
argv = {argv}
parse = "json"
ok_exit = [0]

[map]
findings = "$"
rule_id = "rule"
title = "msg"
path = "file"
line = "line"
severity_const = "low"
dimension = "quality"
'''


class Fakes:
    def __init__(self, home: Path) -> None:
        self.home = home

    def count(self, name: str) -> int:
        p = self.home / f"{name}.count"
        return int(p.read_text(encoding="utf-8")) if p.exists() else 0

    def counts(self) -> tuple[int, ...]:
        return tuple(self.count(n) for n in NAMES)

    def sleep(self, seconds: float) -> None:
        for n in NAMES:
            (self.home / f"{n}.sleep").write_text(str(seconds), encoding="utf-8")

    def mode(self, name: str, mode: str) -> None:
        (self.home / f"{name}.mode").write_text(mode, encoding="utf-8")


@pytest.fixture
def fakes(tmp_path):
    """Register the two fake adapters for this test only. REGISTRY is
    process-global, and a fake left behind would run on every later
    fixture scan in the suite."""
    home = tmp_path / "fakes"
    home.mkdir()
    for name in NAMES:
        script = home / f"{name}.py"
        script.write_text(SCRIPT, encoding="utf-8")
        argv = json.dumps([sys.executable, str(script), "{workdir}"])
        (home / f"{name}.adapter.toml").write_text(
            MANIFEST.format(name=name, argv=argv), encoding="utf-8")
    A.register_adapters()            # the shipped ones, if nothing has yet
    before = len(REGISTRY)
    A.register_adapters([str(home)])
    assert [p.name for p in REGISTRY[before:]] == list(NAMES)
    assert all(p.external for p in REGISTRY[before:])
    yield Fakes(home)
    del REGISTRY[before:]
    for name in NAMES:
        A._REGISTERED.discard(name)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "b.py").write_text("y = 2\n", encoding="utf-8")
    return root


def _cfg(parallel: int | None = None) -> dict:
    cfg = copy.deepcopy(load_config(None))
    cfg["cache"] = {"enabled": True}
    if parallel is not None:
        cfg.setdefault("probes", {})["adapters_parallel"] = parallel
    return cfg


def _scan(root: Path, cfg: dict, **kw):
    kw.setdefault("only", list(NAMES))
    kw.setdefault("use_adapters", False)
    kw.setdefault("cache_path", str(root.parent / "cache.json"))
    return run_scan([str(root)], cfg, **kw)


def _outcome(report, name: str):
    return next(p for p in report.probes if p.name == name)


def _snapshot(report) -> list[tuple]:
    return sorted((f.id, f.probe, f.rule_id, f.location.path) for f in report.findings)


def _stats(report) -> dict:
    return (report.scan_scope or {}).get("cache") or {}


# --------------------------------------------------------------------------

def test_two_adapters_run_at_once(tmp_path, fakes):
    root = _repo(tmp_path)
    fakes.sleep(1.0)

    t0 = time.monotonic()
    parallel = _scan(root, _cfg(parallel=2), use_cache=False)
    wall = time.monotonic() - t0
    assert fakes.counts() == (1, 1)
    assert wall < 2.0, f"two one-second adapters took {wall:.2f}s: they ran one after the other"
    for name in NAMES:
        assert _outcome(parallel, name).status == "ran"

    # Control: with the pool off the same scan takes at least the sum.
    t0 = time.monotonic()
    serial = _scan(root, _cfg(parallel=1), use_cache=False)
    wall = time.monotonic() - t0
    assert wall >= 2.0
    assert fakes.counts() == (2, 2)

    # And the order of the report does not depend on the scheduler.
    assert [p.name for p in parallel.probes] == [p.name for p in REGISTRY]
    assert [p.name for p in serial.probes] == [p.name for p in REGISTRY]
    assert _snapshot(parallel) == _snapshot(serial)
    assert len(parallel.findings) == 2


def test_the_worker_count_comes_from_configuration():
    import os
    assert adapter_workers({}) == min(4, os.cpu_count() or 1)
    assert adapter_workers({"probes": {"adapters_parallel": 3}}) == 3
    assert adapter_workers({"probes": {"adapters_parallel": "2"}}) == 2
    assert adapter_workers({"probes": {"adapters_parallel": 0}}) == 1, "never fewer than one"
    assert adapter_workers({"probes": {"adapters_parallel": "many"}}) == 1


def test_a_repeat_scan_replays_every_adapter(tmp_path, fakes):
    root = _repo(tmp_path)
    cfg = _cfg()

    first = _scan(root, cfg)
    assert fakes.counts() == (1, 1)
    assert _stats(first)["misses"] == 2 and _stats(first)["hits"] == 0

    second = _scan(root, cfg)
    assert fakes.counts() == (1, 1), "nothing changed, so neither adapter ran"
    assert _stats(second)["hits"] == 2 and _stats(second)["misses"] == 0
    for name in NAMES:
        oc = _outcome(second, name)
        assert oc.status == "ran" and oc.finding_count == 1
        assert oc.reason == "replayed from cache (nothing the tool reads changed)"
    assert _snapshot(second) == _snapshot(first), "the replay is the same report"
    assert len(second.findings) == 2

    # Control: --no-cache runs them, and reads nothing.
    third = _scan(root, cfg, use_cache=False)
    assert fakes.counts() == (2, 2)
    assert "cache" not in (third.scan_scope or {})
    assert _outcome(third, NAMES[0]).reason == ""

    # The entries live in the one cache file, one per adapter.
    doc = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    assert all(len(doc["entries"][n]) == 1 for n in NAMES)


def test_one_changed_file_reruns_every_adapter(tmp_path, fakes):
    root = _repo(tmp_path)
    cfg = _cfg()
    _scan(root, cfg)
    _scan(root, cfg)
    assert fakes.counts() == (1, 1)

    # One byte in one file: all or nothing, so both run.
    (root / "b.py").write_bytes(b"y = 2\n\n")
    rep = _scan(root, cfg)
    assert fakes.counts() == (2, 2), "an adapter is not file-local; one changed file re-runs it"
    assert _stats(rep) == {"hits": 0, "misses": 2, "verified": 0, "divergent": 0,
                           "path": str(tmp_path / "cache.json")}
    # and the new tree is memoised in turn
    _scan(root, cfg)
    assert fakes.counts() == (2, 2)

    # The analyzer's own configuration counts as something it reads, even
    # as a dotfile the inventory may not carry.
    (root / ".bandit").write_text("[bandit]\nskips = B101\n", encoding="utf-8")
    _scan(root, cfg)
    assert fakes.counts() == (3, 3)
    _scan(root, cfg)
    assert fakes.counts() == (3, 3)
    (root / ".semgrep").mkdir()
    (root / ".semgrep" / "rules.yml").write_text("rules: []\n", encoding="utf-8")
    _scan(root, cfg)
    assert fakes.counts() == (4, 4)
    (root / ".semgrep" / "rules.yml").write_text("rules: [] # edited\n", encoding="utf-8")
    _scan(root, cfg)
    assert fakes.counts() == (5, 5)

    # A changed tool version is a different question too.
    probe = next(p for p in REGISTRY if p.name == NAMES[0])
    probe.version = "fake 2"
    _scan(root, cfg)
    assert fakes.counts() == (6, 5)

    # The key itself: same inputs, same key; any part changed, another key.
    k = C.adapter_key("n", "v", "r", "i")
    assert k == C.adapter_key("n", "v", "r", "i")
    assert len({k, C.adapter_key("m", "v", "r", "i"), C.adapter_key("n", "w", "r", "i"),
                C.adapter_key("n", "v", "s", "i"), C.adapter_key("n", "v", "r", "j")}) == 5


def test_the_memo_does_not_cross_from_one_configuration_to_another(tmp_path, fakes):
    root = _repo(tmp_path)
    cfg = _cfg()
    _scan(root, cfg)
    changed = copy.deepcopy(cfg)
    changed["quality"]["max_file_lines"] = 123
    _scan(root, changed)
    assert fakes.counts() == (2, 2), "a changed configuration is a different question"
    _scan(root, copy.deepcopy(changed))
    assert fakes.counts() == (2, 2), "control: the same configuration replays"


def test_verify_cache_detects_a_divergence(tmp_path, fakes):
    root = _repo(tmp_path)
    cfg = _cfg()
    only = [NAMES[0]]
    _scan(root, cfg, only=only)
    assert fakes.counts() == (1, 0)

    # Control: an honest entry verifies clean, and the re-run is reported.
    rep = _scan(root, cfg, only=only, verify_cache=True)
    assert fakes.counts() == (2, 0)
    assert _stats(rep)["verified"] == 1 and _stats(rep)["divergent"] == 0
    assert _outcome(rep, NAMES[0]).reason == C.VERIFIED_REASON
    assert not [f for f in rep.findings if f.rule_id == C.DIVERGENCE_RULE]

    # The tool now answers differently although nothing in the tree did.
    fakes.mode(NAMES[0], "b")
    plain = _scan(root, cfg, only=only)
    assert fakes.counts() == (2, 0) and len(plain.findings) == 1, \
        "without verification the stale entry is believed"

    rep = _scan(root, cfg, only=only, verify_cache=True)
    assert fakes.counts() == (3, 0)
    div = [f for f in rep.findings if f.rule_id == C.DIVERGENCE_RULE]
    assert len(div) == 1
    assert div[0].probe == NAMES[0] and div[0].severity == "high"
    assert div[0].dimension == "assurance" and div[0].repo_id == "root"
    assert "cached=1,fresh=2" in div[0].evidence
    assert _outcome(rep, NAMES[0]).reason == C.DIVERGED_REASON
    assert _stats(rep)["divergent"] == 1
    assert {f.rule_id for f in rep.findings} == {C.DIVERGENCE_RULE, f"{NAMES[0]}/R1",
                                                 f"{NAMES[0]}/R2"}, \
        "the fresh result is what gets reported"

    # The entry was replaced: a plain scan now replays the new answer.
    rep = _scan(root, cfg, only=only)
    assert fakes.counts() == (3, 0)
    assert {f.rule_id for f in rep.findings} == {f"{NAMES[0]}/R1", f"{NAMES[0]}/R2"}

    # One memoised adapter per scan, not all of them.
    _scan(root, cfg)
    assert fakes.counts() == (3, 1)
    rep = _scan(root, cfg, verify_cache=True)
    assert _stats(rep) == {"hits": 2, "misses": 0, "verified": 1, "divergent": 0,
                           "path": str(tmp_path / "cache.json")}
    assert sum(fakes.counts()) == 5


def test_a_crashing_adapter_is_an_error_beside_a_running_one(tmp_path, fakes):
    root = _repo(tmp_path)
    cfg = _cfg(parallel=2)
    fakes.mode(NAMES[1], "crash")
    rep = _scan(root, cfg)
    assert _outcome(rep, NAMES[0]).status == "ran"
    crashed = _outcome(rep, NAMES[1])
    assert crashed.status == "error" and "exited 2" in crashed.reason
    assert crashed.finding_count == 0
    assert [f.probe for f in rep.findings] == [NAMES[0]]

    # Nothing was memoised for the one that crashed: fixed, it runs.
    fakes.mode(NAMES[1], "a")
    rep = _scan(root, cfg)
    assert fakes.counts() == (1, 2)
    assert _outcome(rep, NAMES[0]).reason == C.REPLAY_REASON
    assert _outcome(rep, NAMES[1]).status == "ran" and _outcome(rep, NAMES[1]).reason == ""


def test_a_partial_scan_still_holds_every_adapter_back(tmp_path, fakes):
    root = _repo(tmp_path)
    rep = _scan(root, _cfg(), only_files=["a.py"], use_cache=False)
    assert rep.scan_scope["mode"] == "partial"
    for name in NAMES:
        oc = _outcome(rep, name)
        assert oc.status == "skipped" and oc.reason.startswith("partial scan")
    assert fakes.counts() == (0, 0)
