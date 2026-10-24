"""The population backfill (tools/label_populations.py): deterministic, no model
calls, never changes a label, and marks what it cannot recover as unknown."""
from __future__ import annotations

import gzip
import importlib.util
import json
import sys

import pytest
from helpers import ROOT


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


LP = _load("label_populations")
PL = _load("precision_ledger")
RULE = "arbiter/python.sql-injection"


def _finding(i: int, path: str = "a.py", rule: str = RULE) -> dict:
    return {"id": f"f:{i}", "rule": rule, "path": path, "line": i}


def _report() -> dict:
    # dev: 6 python + 2 go findings; held-out: 3 python
    return {"repos": [{"name": "dev", "language": "python"}, {"name": "ho", "language": "python"}],
            "raw_findings": {"dev": [_finding(i) for i in range(6)] + [_finding(10, "b.go"), _finding(11, "c.go")],
                             "ho": [_finding(20 + i) for i in range(3)]}}


def _label(fid: str, repo: str, label: str = "tp", language: str = "python", held_out: bool = False) -> dict:
    return {"finding_id": fid, "rule_id": RULE, "label": label, "labeller": "m:prosecutor+defender/v3",
            "source": "gold-model", "held_out": held_out, "repo": repo, "language": language}


def test_report_backfill_recovers_stratum_and_held_out_sizes():
    recs = [_label("f:0", "dev"), _label("f:1", "dev", "fp"), _label("f:20", "ho", held_out=True),
            _label("f:10", "dev", language="go")]
    out = LP.from_report(recs, _report(), {"ho"}, "r.json")
    got = [(r["stratum_size"], r["stratum_held_out_size"], r["rule_population"],
            r["rule_held_out_population"], r["sampling"]) for r in out]
    assert got == [(9, 3, 11, 3, "stratified")] * 3 + [(2, 0, 11, 3, "stratified")]
    assert [r["label"] for r in out] == ["tp", "fp", "tp", "tp"]
    assert all(r["population_from"] == "r.json" for r in out)


def test_an_unmatched_label_is_marked_unknown_not_guessed():
    recs = [_label("f:99", "dev"),                          # not in the report
            _label("f:0", "ho", held_out=True),             # the id exists, but in another repository
            _label("f:1", "dev", language="go"),            # the finding is python
            _label("f:2", "dev", held_out=True)]            # heldout.json says dev
    out = LP.from_report(recs, _report(), {"ho"}, "r.json")
    assert all(r["stratum_size"] is None for r in out)
    assert [r["sampling"] for r in out] == [
        "unknown: finding not in the report", "unknown: finding not in the report",
        "unknown: language 'python' in the report", "unknown: held-out split differs from heldout.json"]
    # ... and the ledger says so instead of using the label count.
    led = PL.build([{**r, "role": "labeller"} for r in out], None, "2026-10-23", set())
    assert led["rules"][RULE]["population"] is None and led["rules"][RULE]["population_unknown"] == 4


def test_census_backfill_counts_each_stratum():
    recs = [_label(f"f:{i}", "ai", language="go" if i < 3 else "python") for i in range(5)]
    out = LP.backfill(recs, "census")
    assert [(r["stratum_size"], r["rule_population"], r["sampling"]) for r in out] == \
        [(3, 5, "census")] * 3 + [(2, 5, "census")] * 2


def test_a_backfill_that_would_change_a_label_is_refused():
    before = [_label("f:0", "dev")]
    LP.unchanged_labels(before, [{**before[0], "stratum_size": 4}])
    with pytest.raises(SystemExit, match="more than its population"):
        LP.unchanged_labels(before, [{**before[0], "label": "fp", "stratum_size": 4}])


def test_backfill_is_deterministic_and_check_holds_it(tmp_path, monkeypatch):
    report = tmp_path / "report.json.gz"
    report.write_bytes(gzip.compress(json.dumps(_report()).encode(), mtime=0))
    labels = tmp_path / "labels"
    labels.mkdir()
    recs = [_label("f:0", "dev"), _label("f:20", "ho", held_out=True)]
    (labels / "s.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs))
    (labels / "c.jsonl.gz").write_bytes(gzip.compress(b"".join(
        (json.dumps(r, sort_keys=True) + "\n").encode() for r in recs), mtime=0))
    monkeypatch.setattr(LP, "ROOT", tmp_path)
    monkeypatch.setattr(LP, "heldout_repos", lambda: {"ho"})
    plan = {"s.jsonl": "report:report.json.gz", "c.jsonl.gz": "census"}
    assert LP.run(plan, labels, check=True)[0] == 2                  # both out of date
    assert LP.run(plan, labels, check=False)[0] == 0
    first = {p.name: p.read_bytes() for p in labels.iterdir()}
    assert LP.run(plan, labels, check=True)[0] == 0                  # idempotent
    LP.run(plan, labels, check=False)
    assert {p.name: p.read_bytes() for p in labels.iterdir()} == first
    s = [json.loads(x) for x in (labels / "s.jsonl").read_text().splitlines()]
    assert [(r["stratum_size"], r["stratum_held_out_size"]) for r in s] == [(9, 3), (9, 3)]


def test_the_shipped_label_files_are_backfilled():
    """Every label file the ledger reads carries a known population."""
    failures, lines = LP.run(LP.PLAN, LP.LABELS, check=True)
    assert failures == 0, lines
    assert all(" 0 with unknown population" in line for line in lines), lines
