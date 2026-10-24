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


def _pop(size: int, held_out_size: int | None = None, rule_size: int | None = None,
         rule_held_out: int | None = None) -> dict:
    """The population fields label_models.py writes on a label record."""
    held = size if held_out_size is None else held_out_size
    return {"stratum_size": size, "stratum_held_out_size": held,
            "rule_population": size if rule_size is None else rule_size,
            "rule_held_out_population": held if rule_held_out is None else rule_held_out}


def _pairs(n_tp: int, n_fp: int = 0, rule: str = RULE, held_out: bool = True, prefix: str = "f",
           pop: dict | None = None, **kw):
    out = []
    for i in range(n_tp + n_fp):
        label = "tp" if i < n_tp else "fp"
        out += [_rec(f"{prefix}{i}", label, "model-a", rule, held_out, **(pop or {}), **kw),
                _rec(f"{prefix}{i}", label, "model-b", rule, held_out, **(pop or {}), **kw)]
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
    pop = _pop(2000, 800)
    recs = _pairs(400, 0, pop=pop) + _pairs(500, 0, held_out=False, prefix="dev", pop=pop)
    led = PL.build(recs, None, "2026-10-22", {RULE})
    t = led["tier"]
    assert t["rules"] == [RULE] and t["overall"]["tp"] == 400       # the dev labels are not in it
    assert t["claim"] is True and t["overall"]["wilson_lower"] >= 0.99
    assert t["categories"]["CWE-89"]["claim"] is True
    assert t["categories"]["security"]["rule_ids"] == [RULE]
    none = PL.build(recs, None, "2026-10-22", set())["tier"]
    assert none["overall"] is None and none["claim"] is False


def test_a_category_claim_needs_its_own_bound():
    recs = (_pairs(400, 0, pop=_pop(400)) +
            _pairs(150, 0, rule="arbiter/python.code-injection", prefix="c", pop=_pop(150)))
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
    led = PL.build(_pairs(400, 0, pop=_pop(400)), None, "2026-10-22", {RULE}, {"planted": {RULE: 10}})
    led["rules"][RULE].update(entry)
    return led


def test_guard_passes_a_verified_rule_with_an_admitting_entry():
    assert PG.check(_ledger(), {RULE}, SNAPS) == []


def test_guard_fails_a_verified_rule_with_no_ledger_entry():
    out = PG.check(_ledger(), {RULE, OTHER}, SNAPS)
    assert len(out) == 1 and OTHER in out[0] and "no ledger entry" in out[0]


def test_guard_fails_a_verified_rule_below_the_bar():
    led = PL.build(_pairs(390, 10, pop=_pop(400)), None, "2026-10-22", set())
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
    cfg.write_text("precision:\n  verified_rules: [arbiter/python.never-labelled-rule]\n")
    assert PG.main(["check", "--config", str(cfg)]) == 1
    assert "declared verified but has no ledger entry" in capsys.readouterr().out
    cfg.write_text("version: 1\n")
    assert PG.main(["check", "--config", str(cfg)]) == 0


def test_gzipped_jsonl_labels_are_read(tmp_path):
    import gzip
    rec = {"finding_id": "f:1", "rule_id": RULE, "label": "tp", "labeller": "a",
           "source": "juliet", "held_out": True, "role": "ground_truth"}
    gz = tmp_path / "labels.jsonl.gz"
    gz.write_bytes(gzip.compress((json.dumps(rec) + "\n").encode()))
    assert PL.read_jsonl(gz) == PL.read_jsonl(_plain(tmp_path, rec))


def _plain(tmp_path, rec):
    p = tmp_path / "labels.jsonl"
    p.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    return p


def test_labeller_calibration_records_never_reach_the_ledger():
    recs = [{"finding_id": "a", "source": "owasp-java"}, {"finding_id": "b", "source": "calibration:owasp-java"},
            {"finding_id": "c", "source": "calibration:planted-tests"}]
    keep, skipped = PL.drop_calibration(recs)
    assert [r["finding_id"] for r in keep] == ["a"] and skipped == 2


def test_model_labels_from_a_superseded_prompt_are_left_out():
    recs = [{"finding_id": "a", "labeller": "claude-opus-5-5:prosecutor+defender/v1"},
            {"finding_id": "b", "labeller": "claude-opus-5-5:prosecutor+defender+adjudicator/v3"},
            {"finding_id": "c", "labeller": "ground-truth"},
            {"finding_id": "d", "labeller": "alice"}]
    keep, dropped = PL.drop_superseded_model_labels(recs, "v3")
    assert [r["finding_id"] for r in keep] == ["b", "c", "d"] and dropped == 1
    assert PL.drop_superseded_model_labels(recs, "")[1] == 0
    assert PL.current_model_prompt() == _load("label_models").PROMPT_VERSION


def test_a_new_labeller_with_the_same_counts_is_a_relabel():
    first = PL.build(_pairs(10, 1), None, "2026-10-01", set(), {"planted": {RULE: 4}})
    relabel = [{**r, "labeller": r["labeller"] + "-v3"} for r in _pairs(10, 1)]
    again = PL.build(relabel, first, "2026-10-22", set(), {"planted": {RULE: 9}})
    assert again["rules"][RULE]["updated"] == "2026-10-22"
    assert again["rules"][RULE]["snapshot_counts"] == {"planted": 9}


# ---------------------------------------------------------------------------
# Population weights (docs/precision.md, "Population weights")
# ---------------------------------------------------------------------------

def test_the_tier_claim_weights_strata_by_population_not_by_labels():
    # One rule, two languages. Go: 9,000 held-out findings, 50 labels at 90%.
    # Python: 1,000, 950 labels at 100%. Pooled 99.5%, weighted 91%.
    go = _pairs(45, 5, prefix="g", language="go", pop=_pop(9000, rule_size=10_000, rule_held_out=10_000))
    py = _pairs(950, 0, prefix="p", language="python", pop=_pop(1000, rule_size=10_000, rule_held_out=10_000))
    led = PL.build(go + py, None, "2026-10-23", {RULE})
    e = led["rules"][RULE]
    assert e["precision"] == 0.995 and e["population"] == 10_000 and e["population_unknown"] == 0
    assert [(s["language"], s["population"], s["tp"], s["fp"]) for s in e["strata"]] == [
        ("go", 9000, 45, 5), ("python", 1000, 950, 0)]
    assert e["weighted"]["precision"] == 0.91
    t = led["tier"]
    assert t["overall"]["precision"] == 0.91 and t["overall"]["pooled"]["precision"] == 0.995
    assert t["overall"]["coverage"] == 1.0 and t["claim"] is False


def test_held_out_findings_in_unlabelled_strata_count_against_the_bound():
    pop = _pop(400, rule_size=500, rule_held_out=500)                  # 100 held-out findings were never sampled
    led = PL.build(_pairs(400, 0, pop=pop), None, "2026-10-23", {RULE})
    o = led["tier"]["overall"]
    assert o["held_out_population"] == 500 and o["unlabelled_population"] == 100
    assert o["coverage"] == 0.8 and o["claim_lower"] == round(o["wilson_lower"] * 0.8, 4)
    assert led["tier"]["claim"] is False
    full = PL.build(_pairs(400, 0, pop=_pop(400)), None, "2026-10-23", {RULE})["tier"]
    assert full["claim"] is True and full["overall"]["claim_lower"] == full["overall"]["wilson_lower"]


def test_labels_with_no_population_are_reported_unknown_never_counted():
    led = PL.build(_pairs(400, 0), None, "2026-10-23", {RULE})
    e = led["rules"][RULE]
    assert e["population"] is None and e["population_unknown"] == 400 and e["weighted"] is None
    assert e["admitted"]                            # admission is per rule, on its labels
    t = led["tier"]
    assert t["overall"] is None and t["claim"] is False and t["population_unknown"] == [RULE]
    assert "population unknown" in PL.render(led)
    # One unknown label makes its whole stratum unknown: no partial guess.
    mixed = _pairs(399, 0, pop=_pop(1000)) + _pairs(1, 0, prefix="x")
    e = PL.build(mixed, None, "2026-10-23", set())["rules"][RULE]
    assert e["population"] is None and e["population_unknown"] == 400


def test_two_sizes_for_one_stratum_are_refused():
    recs = _pairs(3, 0, pop=_pop(40)) + _pairs(3, 0, prefix="y", pop=_pop(41))
    with pytest.raises(SystemExit, match="two population sizes"):
        PL.build(recs, None, "2026-10-23", set())


def test_a_sheet_with_no_stratum_size_is_unknown(tmp_path):
    sheet = tmp_path / "s.csv"
    sheet.write_text("sample_id,rule,language,stratum_size,verdict\nS1,r,python,,TP\nS2,r,python,7,TP\n")
    a, b = PL.read_sheet(sheet, "b", "gold", True)
    assert "stratum_size" not in a
    assert (b["stratum_size"], b["stratum_held_out_size"]) == (7, 7)
    assert PL.read_sheet(sheet, "b", "gold", False)[1]["stratum_held_out_size"] == 0


def test_guard_fails_a_verified_rule_whose_population_is_unknown():
    led = PL.build(_pairs(400, 0), None, "2026-10-23", {RULE}, {"planted": {RULE: 10}})
    out = PG.check(led, {RULE}, SNAPS)
    assert len(out) == 1 and "no recorded population" in out[0] and "400 label(s)" in out[0]
    assert PG.check(led, set(), SNAPS) == []        # not declared: the ledger only says so
    assert PG.check(_ledger(), {RULE}, SNAPS) == []
