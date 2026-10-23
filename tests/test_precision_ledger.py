"""The precision ledger (tools/precision_ledger.py) and the CI precision guard
(tools/precision_guard.py). Each guard failure is seeded and shown to fire,
and shown not to fire without the seed: a guard that has never failed is not
evidence."""
from __future__ import annotations

import importlib.util
import json
import sys

import pytest
from helpers import ROOT

from arbiter import precision as P


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


PL = _load("precision_ledger")
PG = _load("precision_guard")
RULE = "arbiter/python.sql-injection"
OTHER = "arbiter/go.sql-injection"


def _rec(fid, label, labeller="a", rule=RULE, held_out=True, role="labeller", **kw):
    return {"finding_id": fid, "rule_id": rule, "label": label, "labeller": labeller,
            "source": kw.pop("source", "gold"), "held_out": held_out, "role": role, **kw}


def _pairs(n_tp: int, n_fp: int = 0, rule: str = RULE, held_out: bool = True, prefix: str = "f"):
    out = []
    for i in range(n_tp + n_fp):
        label = "tp" if i < n_tp else "fp"
        out += [_rec(f"{prefix}{i}", label, "model-a", rule, held_out),
                _rec(f"{prefix}{i}", label, "model-b", rule, held_out)]
    return out


# ---------------------------------------------------------------------------
# Shipped state
# ---------------------------------------------------------------------------

def test_the_shipped_ledger_and_its_packaged_copy_are_identical():
    assert PL.LEDGER.read_bytes() == PL.PACKAGED.read_bytes()


def test_the_packaged_ledger_is_package_data():
    text = (ROOT / "pyproject.toml").read_text()
    assert '"packs/precision.json"' in text


def test_the_shipped_guard_passes():
    from arbiter.policy import load_config
    cfg = load_config(str(ROOT / "arbiter.yaml"))
    assert PG.check(P.load_ledger(PG.LEDGER), PG.declared(cfg), PG.load_snapshots()) == []


def test_the_label_format_is_documented_where_p1b_reads_it():
    doc = (ROOT / "docs" / "precision.md").read_text()
    section = doc.split("## Label format", 1)[1].split("\n## ", 1)[0]
    for key in ("finding_id", "rule_id", "label", "labeller", "source", "held_out", "role"):
        assert f"`{key}`" in section


# ---------------------------------------------------------------------------
# Reading labels
# ---------------------------------------------------------------------------

def test_jsonl_labels_are_validated(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text(json.dumps(_rec("x", "TP")) + "\n# a comment\n\n")
    assert PL.read_jsonl(p)[0]["label"] == "tp"
    p.write_text(json.dumps({"finding_id": "x", "rule_id": RULE, "label": "tp"}) + "\n")
    with pytest.raises(SystemExit, match="missing labeller, source, held_out"):
        PL.read_jsonl(p)
    p.write_text(json.dumps(_rec("x", "maybe")) + "\n")
    with pytest.raises(SystemExit, match="tp, fp or unsure"):
        PL.read_jsonl(p)
    p.write_text(json.dumps({**_rec("x", "tp"), "held_out": "yes"}) + "\n")
    with pytest.raises(SystemExit, match="held_out"):
        PL.read_jsonl(p)


def test_label_sample_sheets_read_as_label_records(tmp_path):
    sheet = tmp_path / "alice.csv"
    sheet.write_text("sample_id,rule,language,repo,path,line,severity,title,stratum_size,context,"
                     "verdict,labeller,notes\nS0001,arbiter/python.sql-injection,python,r,a.py,3,high,t,40,"
                     ",FP,,constant table name\nS0002,arbiter/python.sql-injection,python,r,a.py,9,high,t,40,,,,\n")
    recs = PL.read_sheet(sheet, "gold-1", "gold", True)
    assert [(r["finding_id"], r["label"], r["labeller"]) for r in recs] == [
        ("gold-1:S0001", "fp", "alice"), ("gold-1:S0002", "unsure", "alice")]
    assert recs[0]["note"] == "constant table name" and recs[0]["stratum_size"] == 40


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------

def test_final_verdicts_agree_adjudicate_and_abstain():
    v = PL.final_verdicts([
        _rec("agree", "tp", "a"), _rec("agree", "tp", "b"),
        _rec("split", "tp", "a"), _rec("split", "fp", "b"),
        _rec("ruled", "tp", "a"), _rec("ruled", "fp", "b"), _rec("ruled", "fp", "judge", role="adjudicator"),
        _rec("truth", "fp", "a"), _rec("truth", "tp", "owasp", role="ground_truth"),
        _rec("mixed", "tp", "a", held_out=True), _rec("mixed", "tp", "b", held_out=False),
    ])
    assert {k: x["label"] for k, x in v.items()} == {
        "agree": "tp", "split": "unsure", "ruled": "fp", "truth": "tp", "mixed": "tp"}
    assert v["mixed"]["held_out"] is False      # held out only if every record says so
    assert v["split"]["pair"] == ("tp", "fp")


def test_one_finding_under_two_rules_is_refused():
    with pytest.raises(SystemExit, match="more than one rule"):
        PL.final_verdicts([_rec("x", "tp", rule=RULE), _rec("x", "tp", rule=OTHER)])


def test_a_ledger_entry_counts_bounds_kappa_and_admission():
    led = PL.build(_pairs(400, 0) + _pairs(10, 1, rule=OTHER, prefix="g"), None, "2026-10-22", set())
    e = led["rules"][RULE]
    assert (e["tp"], e["fp"], e["n"], e["held_out"]["tp"]) == (400, 0, 400, 400)
    assert e["admitted"] and e["wilson_lower"] == round(P.wilson(400, 400)[0], 4)
    assert e["kappa"] is None and e["observed_agreement"] == 1.0   # all TP: kappa undefined
    assert e["labellers"] == ["model-a", "model-b"] and e["sources"] == ["gold"]
    o = led["rules"][OTHER]
    assert not o["admitted"] and "label(s)" in o["admitted_reason"]


def test_labellers_who_disagree_too_often_keep_a_rule_out():
    recs = []
    for i in range(300):
        a, b = ("tp", "tp") if i % 10 else ("fp", "tp")     # b never says fp
        recs += [_rec(f"k{i}", a, "a"), _rec(f"k{i}", b, "b")]
        if a != b:
            recs.append(_rec(f"k{i}", "tp", "judge", role="adjudicator"))
    e = PL.build(recs, None, "2026-10-22", set())["rules"][RULE]
    assert e["tp"] == 300 and e["kappa"] is not None and e["kappa"] < 0.8
    assert not e["admitted"] and "kappa" in e["admitted_reason"]


def test_the_tier_claim_uses_held_out_labels_of_verified_rules_only():
    recs = _pairs(400, 0) + _pairs(500, 0, held_out=False, prefix="dev")
    led = PL.build(recs, None, "2026-10-22", {RULE})
    t = led["tier"]
    assert t["rules"] == [RULE] and t["overall"]["tp"] == 400       # the dev labels are not in it
    assert t["claim"] is True and t["overall"]["wilson_lower"] >= 0.99
    assert t["categories"]["CWE-89"]["claim"] is True
    assert t["categories"]["security"]["rule_ids"] == [RULE]
    none = PL.build(recs, None, "2026-10-22", set())["tier"]
    assert none["overall"] is None and none["claim"] is False


def test_a_category_claim_needs_its_own_bound():
    recs = _pairs(400, 0) + _pairs(150, 0, rule="arbiter/python.code-injection", prefix="c")
    t = PL.build(recs, None, "2026-10-22", {RULE, "arbiter/python.code-injection"})["tier"]
    assert t["categories"]["CWE-89"]["claim"] is True
    assert t["categories"]["CWE-95"]["claim"] is False      # 150/150 is admitted, but < 0.99
    assert t["categories"]["security"]["claim"] is True     # the two pooled reach it


def test_a_rebuild_keeps_fp_notes_and_the_date_of_unchanged_rules():
    first = PL.build(_pairs(10, 1) + [_rec("f10", "fp", "model-a", note="constant SQL")],
                     None, "2026-10-01", set(), {"planted": {RULE: 4}})
    first["rules"][RULE]["fp_notes"].append("hand-written note")
    same = PL.build(_pairs(10, 1), first, "2026-10-22", set(), {"planted": {RULE: 9}})
    e = same["rules"][RULE]
    assert e["updated"] == "2026-10-01" and e["snapshot_counts"] == {"planted": 4}
    assert e["fp_notes"] == ["constant SQL", "hand-written note"]
    more = PL.build(_pairs(12, 1), first, "2026-10-22", set(), {"planted": {RULE: 9}})
    assert more["rules"][RULE]["updated"] == "2026-10-22"
    assert more["rules"][RULE]["snapshot_counts"] == {"planted": 9}


def test_the_cli_builds_from_jsonl_and_sheets(tmp_path, monkeypatch):
    jl = tmp_path / "l.jsonl"
    jl.write_text("".join(json.dumps(r) + "\n" for r in _pairs(3, 1)))
    out = tmp_path / "ledger.json"
    monkeypatch.setattr(PL, "LEDGER", tmp_path / "none.json")
    assert PL.main(["build", str(jl), "--out", str(out), "--date", "2026-10-22"]) == 0
    assert json.loads(out.read_text())["rules"][RULE]["tp"] == 3


# ---------------------------------------------------------------------------
# The guard, seeded
# ---------------------------------------------------------------------------

SNAPS = {"planted": {"by_rule": {RULE: 10}, "recorded": "2026-10-20"}}


def _ledger(**entry) -> dict:
    led = PL.build(_pairs(400, 0), None, "2026-10-22", {RULE}, {"planted": {RULE: 10}})
    led["rules"][RULE].update(entry)
    return led


def test_guard_passes_a_verified_rule_with_an_admitting_entry():
    assert PG.check(_ledger(), {RULE}, SNAPS) == []


def test_guard_fails_a_verified_rule_with_no_ledger_entry():
    out = PG.check(_ledger(), {RULE, OTHER}, SNAPS)
    assert len(out) == 1 and OTHER in out[0] and "no ledger entry" in out[0]


def test_guard_fails_a_verified_rule_below_the_bar():
    led = PL.build(_pairs(390, 10), None, "2026-10-22", set())
    out = PG.check(led, {RULE}, {})
    assert len(out) == 1 and "below the bar" in out[0] and "lower bound" in out[0]


def test_guard_fails_a_hand_edited_admitted_flag():
    led = PL.build(_pairs(20, 0), None, "2026-10-22", set())
    assert PG.check(led, set(), {}) == []
    led["rules"][RULE]["admitted"] = True
    out = PG.check(led, set(), {})
    assert len(out) == 1 and "admitted=True" in out[0]


def test_guard_fails_a_snapshot_jump_after_the_labels():
    jumped = {"planted": {"by_rule": {RULE: 13}, "recorded": "2026-10-23"}}
    out = PG.check(_ledger(), {RULE}, jumped)
    assert len(out) == 1 and "10 -> 13" in out[0] and "+30%" in out[0]


def test_guard_allows_a_small_change_and_a_jump_the_ledger_postdates():
    small = {"planted": {"by_rule": {RULE: 12}, "recorded": "2026-10-23"}}
    assert PG.check(_ledger(), {RULE}, small) == []                   # +20% is the limit
    older = {"planted": {"by_rule": {RULE: 13}, "recorded": "2026-10-21"}}
    assert PG.check(_ledger(), {RULE}, older) == []                   # re-labelled after it
    fewer = {"planted": {"by_rule": {RULE: 2}, "recorded": "2026-10-23"}}
    assert PG.check(_ledger(), {RULE}, fewer) == []                   # recall, not precision


def test_guard_fails_a_rule_that_starts_firing_in_a_snapshot_after_its_labels():
    led = _ledger(snapshot_counts={"planted": 0})
    out = PG.check(led, {RULE}, {"planted": {"by_rule": {RULE: 3}, "recorded": "2026-10-23"}})
    assert len(out) == 1 and "(new)" in out[0]


def test_guard_fails_when_the_packaged_copy_differs():
    out = PG.check(_ledger(), {RULE}, SNAPS, packaged_equal=False)
    assert len(out) == 1 and "differ" in out[0]


def test_guard_reads_declarations_from_config():
    cfg = {"precision": {"verified_rules": ["arbiter/python.sql-*", "house/no-print"]}}
    got = PG.declared(cfg)
    assert RULE in got and "house/no-print" in got and OTHER not in got


def test_guard_cli_fails_on_a_seeded_declaration(tmp_path, capsys):
    cfg = tmp_path / "arbiter.yaml"
    cfg.write_text("precision:\n  verified_rules: [arbiter/python.sql-injection]\n")
    assert PG.main(["check", "--config", str(cfg)]) == 1
    assert "declared verified but has no ledger entry" in capsys.readouterr().out
    cfg.write_text("version: 1\n")
    assert PG.main(["check", "--config", str(cfg)]) == 0
