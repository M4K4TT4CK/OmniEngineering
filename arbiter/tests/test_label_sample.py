"""Human-label sampling (tools/label_sample.py): stratified allocation,
the labelling sheet, Wilson intervals, stratum-weighted precision and
Cohen's kappa. Everything runs on data built here."""
from __future__ import annotations

import csv
import importlib.util
import json
import sys

import pytest
from helpers import ROOT

_spec = importlib.util.spec_from_file_location("label_sample", ROOT / "tools" / "label_sample.py")
ls = importlib.util.module_from_spec(_spec)
sys.modules["label_sample"] = ls
_spec.loader.exec_module(ls)


def _report():
    raw = {"repo-a": [{"rule": "arbiter/python.loud", "path": f"a{i}.py", "line": i + 1,
                       "severity": "low", "title": "loud"} for i in range(300)]
           + [{"rule": "arbiter/python.rare", "path": "r.py", "line": 2, "severity": "high", "title": "rare"}],
           "repo-b": [{"rule": "arbiter/go.mid", "path": f"m{i}.go", "line": 1, "severity": "medium",
                       "title": "mid"} for i in range(20)]}
    return {"raw_findings": raw, "repos": [{"name": "repo-a", "language": "python"},
                                           {"name": "repo-b", "language": "go"}]}


def test_allocation_gives_every_stratum_a_row_and_respects_the_total():
    alloc = ls.allocate({"loud": 300, "rare": 1, "mid": 20}, 30)
    assert sum(alloc.values()) == 30
    assert alloc["rare"] == 1
    assert alloc["loud"] > alloc["mid"] > alloc["rare"]
    assert alloc["loud"] < 30 * 300 / 321, "less than proportional: the loud rule must not take everything"


def test_allocation_never_exceeds_a_stratum():
    alloc = ls.allocate({"a": 2, "b": 3}, 50)
    assert alloc == {"a": 2, "b": 3}


def test_sample_is_deterministic_and_carries_stratum_population(tmp_path):
    findings = ls.findings_from_report(_report())
    one = ls.stratified_sample(findings, 25, seed=1)
    two = ls.stratified_sample(findings, 25, seed=1)
    assert one == two and len(one) == 25
    sizes = {(r["rule"], r["stratum_size"]) for r in one}
    assert ("arbiter/python.rare", 1) in sizes and ("arbiter/python.loud", 300) in sizes


def test_sheet_has_blank_verdicts_and_code_context(tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "repo-a").mkdir(parents=True)
    (corpus / "repo-a" / "r.py").write_text("x = 1\nsecret = 'abc'\ny = 2\n")
    rows = [{"rule": "arbiter/python.rare", "language": "python", "repo": "repo-a", "path": "r.py",
             "line": 2, "severity": "high", "title": "rare", "stratum_size": 1}]
    out = tmp_path / "sheet.csv"
    ls.write_sheet(rows, corpus, out)
    rec = list(csv.DictReader(out.open()))[0]
    assert rec["verdict"] == "" and rec["sample_id"] == "S0001"
    assert ">    2 secret = 'abc'" in rec["context"]


def test_sample_command_refuses_a_report_without_raw_findings(tmp_path):
    rep = tmp_path / "r.json"
    rep.write_text(json.dumps({"repos": []}))
    with pytest.raises(SystemExit):
        ls.main(["sample", "--report", str(rep), "--out", str(tmp_path / "s.csv")])


def test_wilson_interval():
    assert ls.wilson(0, 0) is None
    lo, hi = ls.wilson(8, 10)
    assert lo == pytest.approx(0.49, abs=0.01) and hi == pytest.approx(0.943, abs=0.01)
    assert ls.wilson(10, 10)[1] == 1.0


def test_kappa_matches_the_textbook_example():
    # 50 items: both TP 20, both FP 15, A-TP/B-FP 5, A-FP/B-TP 10 -> kappa 0.4
    a = ["TP"] * 20 + ["FP"] * 15 + ["TP"] * 5 + ["FP"] * 10
    b = ["TP"] * 20 + ["FP"] * 15 + ["FP"] * 5 + ["TP"] * 10
    assert ls.cohens_kappa(a, b) == pytest.approx(0.4, abs=1e-3)
    assert ls.cohens_kappa(["TP"] * 5, ["TP"] * 5) is None  # chance agreement is 1
    assert ls.cohens_kappa(["TP", "FP"], ["TP", "FP"]) == 1.0


def test_precision_is_weighted_back_to_the_population():
    rows = ([{"sample_id": f"a{i}", "rule": "loud", "language": "py", "stratum_size": 900,
              "verdict": "FP"} for i in range(9)]
            + [{"sample_id": "a9", "rule": "loud", "language": "py", "stratum_size": 900, "verdict": "TP"}]
            + [{"sample_id": f"b{i}", "rule": "rare", "language": "py", "stratum_size": 100,
                "verdict": "TP"} for i in range(10)]
            + [{"sample_id": "c", "rule": "rare", "language": "py", "stratum_size": 100, "verdict": "unsure"}])
    t = ls.precision_table(rows)
    assert t["rules"]["loud"]["precision"] == 0.1
    assert t["rules"]["rare"]["abstain"] == 1 and t["rules"]["rare"]["precision"] == 1.0
    # unweighted would be 11/20 = 0.55; weighted is (900*0.1 + 100*1.0)/1000
    assert t["overall"]["precision"] == pytest.approx(0.19, abs=1e-3)
    lo, hi = t["overall"]["ci95"]
    assert lo < 0.19 < hi


def test_score_two_sheets_reports_kappa_and_disagreements(tmp_path):
    base = [{"sample_id": f"S{i}", "rule": "r", "language": "py", "stratum_size": 10} for i in range(4)]
    a = [{**r, "verdict": v} for r, v in zip(base, ["TP", "TP", "FP", "FP"])]
    b = [{**r, "verdict": v} for r, v in zip(base, ["TP", "FP", "FP", "FP"])]
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a))
    pb.write_text(json.dumps(b))
    out = tmp_path / "res.json"
    assert ls.main(["score", str(pa), str(pb), "--json", str(out)]) == 0
    res = json.loads(out.read_text())
    assert res["agreement"]["pairs"] == 4 and res["agreement"]["disagreements"] == ["S1"]
    assert res["agreement"]["kappa"] == pytest.approx(0.5, abs=1e-3)
    # consensus drops the disagreement: 1 TP, 2 FP
    assert res["precision"]["rules"]["r"]["labelled"] == 3
