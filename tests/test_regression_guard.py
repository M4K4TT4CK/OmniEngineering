"""Regression guards (tools/regression_guard.py): the committed snapshots of
the in-tree corpora still match, and each guard is proven to fail on a
seeded regression -- a rule deleted from its pack, a probe made slower --
and to pass without one. A guard that has never failed is not evidence."""
from __future__ import annotations

import importlib.util
import json
import sys
import time

import pytest
from helpers import ROOT

_spec = importlib.util.spec_from_file_location("regression_guard", ROOT / "tools" / "regression_guard.py")
rg = importlib.util.module_from_spec(_spec)
sys.modules["regression_guard"] = rg
_spec.loader.exec_module(rg)

from arbiter import ast as _ts  # noqa: E402
from arbiter import lang_rules  # noqa: E402

# Snapshots were recorded with the grammars installed; without them every
# pack rule is skipped and the guard would report them all as gone.
needs_ts = pytest.mark.skipif(not _ts.available(), reason="tree-sitter not installed")

TARGET = ROOT / "fixtures" / "planted" / "python" / "bad"
RULE = "arbiter/python.sql-injection"
GUARDS = {"perf": {"tolerance": 1.5,
                   "slack_seconds": 0.5, "min_factor": 1.0}, "targets": {}}


@pytest.fixture
def snapdir(tmp_path, monkeypatch):
    monkeypatch.setattr(rg, "SNAPSHOTS", tmp_path / "snapshots")
    return tmp_path / "snapshots"


def _record(snapdir, seconds: float) -> rg.Target:
    t = rg.Target("seed", TARGET)
    keys, _ = rg.timed_scan(t.path)
    rg.write_snapshot(t, keys)
    (snapdir / "perf-baseline.json").write_text(json.dumps(
        {"reference_seconds": 1.0, "targets": {"seed": seconds}}))
    return t


def _check(t, lines, live_reference: bool = False, **kw):
    return rg.check(GUARDS, [t], scan=rg.timed_scan, ref_seconds=None if live_reference else 1.0,
                    out=lines.append, **kw)


# --- pure arithmetic ---------------------------------------------------------

def test_snapshot_diff_counts_duplicates_and_names_lost_rules():
    saved = ["a\tx.py\t1", "a\tx.py\t1", "b\tx.py\t2"]
    d = rg.snapshot_diff(saved, ["a\tx.py\t1", "c\tx.py\t3"])
    assert d["removed"] == ["a\tx.py\t1", "b\tx.py\t2"]
    assert d["added"] == ["c\tx.py\t3"]
    assert d["rules_lost"] == ["b"]


def test_machine_factor_scales_up_but_never_tightens():
    perf = {"min_factor": 1.0, "tolerance": 1.5, "slack_seconds": 2.0}
    assert rg.machine_factor(4.0, 2.0, perf) == 2.0
    assert rg.machine_factor(1.0, 2.0, perf) == 1.0
    assert rg.perf_budget(10.0, 2.0, perf) == 32.0


# --- the committed snapshots ---------------------------------------------------

@needs_ts
def test_in_tree_snapshots_match(capsys):
    """The CI guard itself for the trees in this repository. A failure here
    is a changed finding set: if the change is intended, run
    `python tools/regression_guard.py update --targets planted,ai-corpus
    --no-perf` and commit the snapshot with the rule change."""
    guards = rg.load_guards()
    targets = rg.resolve_targets(guards, None, ["planted", "ai-corpus"])
    lines: list[str] = []
    assert rg.check(guards, targets, do_snap=True, do_perf=False, out=lines.append) == 0, "\n".join(lines)


def test_every_pinned_guard_target_is_a_gold_pin_with_a_snapshot():
    guards = rg.load_guards()
    pins = rg._pins()
    base = json.loads((ROOT / "tools" / "gold" / "snapshots" / "perf-baseline.json").read_text())
    for name, spec in guards["targets"].items():
        if not spec.get("path"):
            assert name in pins, f"{name} is not pinned in fetch_corpus.sh"
        assert (ROOT / "tools" / "gold" / "snapshots" / f"{name}.json").is_file(), name
        assert name in base["targets"], f"{name} has no time baseline"


# --- seeded regressions: each guard must fail --------------------------------

@needs_ts
def test_snapshot_guard_passes_on_the_unchanged_tree(snapdir):
    t = _record(snapdir, seconds=60.0)
    lines: list[str] = []
    assert _check(t, lines, do_snap=True, do_perf=True) == 0, lines


@needs_ts
def test_snapshot_guard_fails_when_a_rule_is_deleted(snapdir, monkeypatch):
    t = _record(snapdir, seconds=60.0)
    real = lang_rules.load_packs()
    gutted = {name: ({**data, "rules": [r for r in data["rules"] if f"arbiter/{name}.{r['id']}" != RULE
                                        and r["id"] != RULE.split("/", 1)[1]]})
              for name, data in real.items()}
    assert sum(map(len, (d["rules"] for d in gutted.values()))) < sum(map(len, (d["rules"] for d in real.values())))
    monkeypatch.setattr(lang_rules, "load_packs", lambda: gutted)
    lines: list[str] = []
    assert _check(t, lines, do_snap=True, do_perf=False) == 1
    assert any(RULE in line and "no longer fire" in line for line in lines), lines


@needs_ts
def test_perf_guard_fails_when_a_probe_gets_slower(snapdir, monkeypatch):
    _, secs = rg.timed_scan(TARGET)  # warm: grammars and compiled packs loaded
    _, secs = rg.timed_scan(TARGET)
    t = _record(snapdir, seconds=secs)
    budget = rg.perf_budget(secs, 1.0, GUARDS["perf"])
    real = lang_rules.run_pack

    def slow(pack, ctx):
        time.sleep(budget + 0.5)
        return real(pack, ctx)

    monkeypatch.setattr(lang_rules, "run_pack", slow)
    lines: list[str] = []
    assert _check(t, lines, do_snap=False, do_perf=True) == 1
    assert any(line.startswith("FAIL seed: scan") for line in lines), lines


@needs_ts
def test_perf_guard_is_not_fooled_by_a_slowdown_that_also_hits_the_reference(snapdir, monkeypatch):
    """The machine factor is measured live here. A reference that ran
    Arbiter would slow down with the seeded sleep and scale the budget up
    with it; the calibration workload must stay blind to it."""
    rg.timed_scan(TARGET)
    _, secs = rg.timed_scan(TARGET)
    t = _record(snapdir, seconds=secs)
    base = json.loads((snapdir / "perf-baseline.json").read_text())
    base["reference_seconds"] = rg.reference_seconds()
    (snapdir / "perf-baseline.json").write_text(json.dumps(base))
    real = lang_rules.run_pack

    def slow(pack, ctx):
        time.sleep(rg.perf_budget(secs, 2.0, GUARDS["perf"]) + 0.5)
        return real(pack, ctx)

    monkeypatch.setattr(lang_rules, "run_pack", slow)
    started = time.perf_counter()
    rg.reference_seconds(repeats=1)
    assert time.perf_counter() - started < 5, "the calibration workload must not call into Arbiter"
    lines: list[str] = []
    assert _check(t, lines, live_reference=True, do_snap=False, do_perf=True) == 1, lines


# --- aggregate-only target (the held-out AI corpus) --------------------------

def test_aggregate_target_fails_on_a_change_but_prints_only_numbers(snapdir):
    t = rg.Target("held", ROOT / "fixtures" / "ai-corpus-heldout", aggregate=True)
    keys = ["arbiter/go.secret-rule\tgo/app/main.go\t12", "arbiter/python.y\tpython/x/app/a.py\t3"]
    score = {"detected": 4, "true_positives": 5}
    rg.write_snapshot(t, keys, score)
    saved = json.loads((snapdir / "held.json").read_text())
    assert "findings" not in saved and "by_rule" not in saved
    assert "secret-rule" not in json.dumps(saved) and saved["count"] == 2

    def scan(keys_now, score_now):
        return lambda path: (keys_now, 0.1, score_now)

    lines: list[str] = []
    assert rg.check(GUARDS, [t], do_snap=True, do_perf=False, out=lines.append,
                    agg_scan=scan(keys, score)) == 0, lines
    lines = []
    assert rg.check(GUARDS, [t], do_snap=True, do_perf=False, out=lines.append,
                    agg_scan=scan(keys[:1], {"detected": 3, "true_positives": 5})) == 1
    text = "\n".join(lines)
    assert "2 -> 1 findings" in text and "detected: 4 -> 3" in text
    assert "secret-rule" not in text and "main.go" not in text and "a.py" not in text


def test_the_heldout_guard_target_is_aggregate_only():
    guards = rg.load_guards()
    spec = guards["targets"]["ai-corpus-heldout"]
    assert spec == {"path": "fixtures/ai-corpus-heldout", "aggregate": True}
    snap = json.loads((ROOT / "tools" / "gold" / "snapshots" / "ai-corpus-heldout.json").read_text())
    assert snap["aggregate"] is True and "findings" not in snap and "by_rule" not in snap
    assert set(snap) == {"target", "aggregate", "arbiter_commit", "_about", "count", "digest", "score"}


def test_heldout_aggregate_snapshot_matches(capsys):
    """The held-out score has not moved. A failure says only that it moved;
    if the change is intended, run `python tools/regression_guard.py update
    --targets ai-corpus-heldout --no-perf` and commit the new counts. Do not
    go looking for which findings changed: that is what held out means."""
    guards = rg.load_guards()
    targets = rg.resolve_targets(guards, None, ["ai-corpus-heldout"])
    lines: list[str] = []
    assert rg.check(guards, targets, do_snap=True, do_perf=False, out=lines.append) == 0, "\n".join(lines)
