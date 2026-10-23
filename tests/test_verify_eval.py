"""tools/verify_eval.py: agreement and confirmed-set precision on recorded
verdict/label pairs. No network, no scan except the small sheet run."""
from __future__ import annotations

import importlib.util
import json
import sys

from helpers import ROOT

_spec = importlib.util.spec_from_file_location("verify_eval", ROOT / "tools" / "verify_eval.py")
ve = importlib.util.module_from_spec(_spec)
sys.modules["verify_eval"] = ve
_spec.loader.exec_module(ve)


def _pairs():
    rows = []
    for rule, label, verdict, n in [
        ("r/a", "TP", "confirmed", 8), ("r/a", "FP", "rejected", 3), ("r/a", "FP", "confirmed", 1),
        ("r/b", "TP", "rejected", 2), ("r/b", "FP", "unsure", 1), ("r/b", "TP", "not_verified", 1),
    ]:
        rows += [{"rule": rule, "label": label, "verdict": verdict}] * n
    return rows + [{"rule": "r/c", "label": None, "verdict": "confirmed"}]  # unlabelled: ignored


def test_score_overall_and_per_rule():
    s = ve.score(_pairs())
    o = s["overall"]
    assert o["n"] == 16 and o["decided"] == 14
    assert (o["confirmed_tp"], o["confirmed_fp"], o["rejected_tp"], o["rejected_fp"]) == (8, 1, 2, 3)
    assert o["confirmed_precision"] == round(8 / 9, 3)
    lo, hi = o["confirmed_precision_ci95"]
    assert lo < 8 / 9 < hi
    assert o["agreement"] == round(11 / 14, 3)
    assert o["tp_rejected_rate"] == round(2 / 10, 3)  # of the TPs the model judged
    assert o["not_verified"] == 1 and o["unsure_fp"] == 1
    assert -1 <= o["kappa"] <= 1
    a, b = s["rules"]["r/a"], s["rules"]["r/b"]
    assert a["agreement"] == round(11 / 12, 3) and a["tp_rejected_rate"] == 0
    assert b["confirmed_precision"] is None and b["agreement"] == 0
    assert "r/c" not in s["rules"]


def test_cost():
    assert ve.cost("claude-opus-5-5", 1_000_000, 100_000) == 6.0
    assert ve.cost("unknown", 1, 1) is None


def test_render_not_measured():
    res = {"source": "x", "prompt_version": "verify-v1", "cost_usd": 0.0,
           "usage": {"model": "claude-opus-5-5", "reasons": ["ANTHROPIC_API_KEY is not set"]},
           "score": ve.score([{"rule": "r", "label": "TP", "verdict": "not_verified"}]), "pairs": []}
    md = ve.render(res)
    assert "Not measured" in md and "ANTHROPIC_API_KEY" in md


def test_sheet_without_key_is_not_measured(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "m.py").write_text("import os\nos.system(input())\n")
    sheet = tmp_path / "s.json"
    sheet.write_text(json.dumps([{"sample_id": "S1", "rule": "arbiter/x", "repo": "repo", "path": "m.py",
                                  "line": 2, "severity": "high", "title": "t", "verdict": "TP"},
                                 {"sample_id": "S2", "rule": "arbiter/x", "repo": "repo", "path": "m.py",
                                  "line": 1, "severity": "high", "title": "t", "verdict": ""}]))
    out = tmp_path / "r.json"
    assert ve.main(["--sheet", str(sheet), "--corpus", str(tmp_path), "--cache",
                    str(tmp_path / "c.json"), "--json", str(out)]) == 0
    res = json.loads(out.read_text())
    assert res["score"]["overall"]["n"] == 1
    assert res["score"]["overall"]["not_verified"] == 1
    assert res["usage"]["calls"] == 0
