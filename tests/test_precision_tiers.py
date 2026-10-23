"""Precision tiers (precision.py): which findings a tiered gate may block on.

A finding is `verified` only when its rule is declared verified, the ledger
admits the rule, and either the model confirmed the finding or the profile
cannot call a model and the rule's bound alone reaches 0.99. Everything else
is `advisory` (shown, never blocks a tiered gate) or `inferred`. The legacy
gate, the default, is unchanged.
"""
from __future__ import annotations

import copy
import json

import pytest
from helpers import ROOT

from arbiter import precision as P
from arbiter.core import Finding, Location, Report
from arbiter.engine import run_scan
from arbiter.policy import DEFAULTS, evaluate_gate, load_config
from arbiter.report import render_console, render_markdown, write_sarif


# These tests need tree-sitter grammars; the fast CI tier installs none.
_needs_ts = pytest.mark.skipif(
    not __import__("arbiter.ast", fromlist=["available"]).available(),
    reason="tree-sitter not installed")

RULE = "arbiter/python.sql-injection"


def _ledger(tp: int, fp: int = 0, kappa=None, rule: str = RULE) -> dict:
    led = P.empty_ledger()
    led["rules"][rule] = {"tp": tp, "fp": fp, "unsure": 0, "kappa": kappa}
    return led


def _cfg(**gate) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    cfg["gate"].update(gate)
    cfg["precision"]["verified_rules"] = [RULE]
    return cfg


def _f(rule: str = RULE, severity: str = "critical", **kw) -> Finding:
    return Finding(rule_id=rule, title="t", severity=severity,
                   location=Location(path="a.py", start_line=3), evidence=rule, **kw)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def test_wilson_matches_the_rule_of_three_scale():
    lo, hi = P.wilson(300, 300)
    assert 0.987 < lo < 0.988 and hi == 1.0
    assert P.wilson(0, 0) is None


def test_weighted_precision_of_one_stratum_is_its_plain_wilson():
    w = P.weighted_precision([{"tp": 29, "fp": 1, "population": 500}])
    assert w["wilson_lower"] == round(P.wilson(29, 30)[0], 4)
    assert w["n_eff"] == 30.0


def test_a_loud_thinly_labelled_rule_widens_the_weighted_interval():
    even = P.weighted_precision([{"tp": 100, "fp": 0, "population": 100},
                                 {"tp": 100, "fp": 0, "population": 100}])
    skewed = P.weighted_precision([{"tp": 100, "fp": 0, "population": 10},
                                   {"tp": 5, "fp": 0, "population": 10_000}])
    assert skewed["wilson_lower"] < even["wilson_lower"]


@pytest.mark.parametrize("tp,fp,kappa,ok", [
    (29, 0, None, False),       # too few labels
    (30, 0, None, False),       # enough labels, but 30/30 has a lower bound of 0.886
    (124, 0, None, False),      # 0.96995: with no FP, 125 labels is the least that reaches 0.97
    (125, 0, None, True),
    (100, 1, None, False),      # 0.99 observed, but the lower bound is 0.946
    (300, 1, None, True),       # lower bound 0.981
    (200, 0, 0.7, False),       # labellers do not agree well enough
    (200, 0, 0.85, True),
])
def test_admission_needs_the_count_the_bound_and_the_kappa(tp, fp, kappa, ok):
    entry = {"tp": tp, "fp": fp, "kappa": kappa}
    assert P.admission(entry, P.DEFAULT_BAR)[0] is ok


def test_admission_ignores_the_entrys_own_flag():
    assert P.admission({"tp": 1, "fp": 0, "admitted": True}, P.DEFAULT_BAR)[0] is False


# ---------------------------------------------------------------------------
# Tier assignment
# ---------------------------------------------------------------------------

def test_an_empty_ledger_makes_every_finding_advisory():
    f = _f()
    assert P.tier_for(f, P.empty_ledger(), _cfg(), "offline")[0] == "advisory"
    assert "no ledger entry" in P.tier_for(f, P.empty_ledger(), _cfg(), "offline")[1]


def test_an_undeclared_rule_is_advisory_however_good_its_ledger():
    cfg = _cfg()
    cfg["precision"]["verified_rules"] = []
    tier, why = P.tier_for(_f(), _ledger(1000), cfg, "offline")
    assert tier == "advisory" and "probation" in why


def test_a_model_found_finding_is_inferred():
    assert P.tier_for(_f(provenance="inferred"), _ledger(1000), _cfg(), "offline")[0] == "inferred"


@pytest.mark.parametrize("verdict,tier", [("confirmed", "verified"), ("rejected", "advisory"),
                                          ("unsure", "advisory")])
def test_the_model_verdict_decides_an_admitted_rules_finding(verdict, tier):
    f = _f()
    f.verification = {"verdict": verdict, "reason": "because"}   # p2-verify's field
    got, why = P.tier_for(f, _ledger(200), _cfg(), "assisted")
    assert got == tier
    if tier == "advisory":
        assert verdict in why and "because" in why


def test_the_verdict_is_read_from_a_dict_too():
    assert P._verdict({"verification": {"verdict": "confirmed"}}) == ("confirmed", "")
    assert P._verdict(_f()) == ("", "")


@pytest.mark.parametrize("tp,profile,tier", [
    (200, "offline", "advisory"),     # admitted (0.981) but below the 0.99 a rule needs alone
    (500, "offline", "verified"),     # lower bound 0.9924
    (500, "ci", "verified"),          # ci cannot call a model either
    (500, "connected", "advisory"),   # could have been model-checked and was not
])
def test_without_a_verdict_only_a_099_rule_on_a_deterministic_profile_is_verified(tp, profile, tier):
    f = _f()
    assert P.tier_for(f, _ledger(tp), _cfg(), profile)[0] == tier
    g = _f()
    g.verification = {"verdict": "not_verified", "reason": "budget"}
    assert P.tier_for(g, _ledger(tp), _cfg(), profile)[0] == tier


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def _report(*findings: Finding) -> Report:
    r = Report()
    r.findings = list(findings)
    return r


def test_legacy_gate_is_unchanged_by_tiers():
    f = _f()
    f.tier = "advisory"
    g = evaluate_gate(_report(f), _cfg())
    assert not g["passed"] and g["tier_mode"] == "legacy" and g["failing_ids"] == [f.id]


def test_an_advisory_critical_finding_does_not_fail_a_tiered_gate():
    f = _f()
    f.tier = "advisory"
    g = evaluate_gate(_report(f), _cfg(tier_mode="tiered"))
    assert g["passed"] and g["considered"] == 0 and g["tiers"] == ["verified"]


def test_a_verified_critical_finding_fails_a_tiered_gate():
    f = _f()
    f.tier = "verified"
    g = evaluate_gate(_report(f), _cfg(tier_mode="tiered"))
    assert not g["passed"] and g["failing_ids"] == [f.id]


def test_a_tiered_gate_can_opt_in_advisory():
    f = _f()
    f.tier = "advisory"
    assert not evaluate_gate(_report(f), _cfg(tier_mode="tiered", tiers=["verified", "advisory"]))["passed"]


def test_inferred_needs_both_switches_to_block():
    f = _f(provenance="inferred")
    f.tier = "inferred"
    assert evaluate_gate(_report(f), _cfg(tier_mode="tiered", tiers=["inferred"]))["passed"]
    assert not evaluate_gate(_report(f), _cfg(tier_mode="tiered", tiers=["inferred"],
                                              gate_on_inferred=True))["passed"]


def test_a_misspelt_tier_mode_is_an_error(tmp_path):
    (tmp_path / "arbiter.yaml").write_text("gate:\n  tier_mode: teired\n")
    with pytest.raises(RuntimeError, match="tier_mode"):
        load_config(None, str(tmp_path))
    with pytest.raises(RuntimeError, match="tier_mode"):
        evaluate_gate(_report(), _cfg(tier_mode="strict"))


@_needs_ts
def test_a_real_scan_with_a_seeded_ledger_gates_on_the_verified_finding_only(tmp_path):
    """End to end: the planted Python sql-injection finding is verified by a
    ledger at 0.99+ under the offline profile and fails a tiered gate; every
    other finding stays advisory and does not."""
    led = _ledger(500)
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(led))
    cfg = _cfg(tier_mode="tiered")
    cfg["gate"]["fail_on"] = {"severity": "medium"}
    cfg["precision"]["ledger"] = str(path)
    rep = run_scan([str(ROOT / "fixtures" / "planted" / "python" / "bad")], cfg, use_cache=False, use_adapters=False)
    verified = [f for f in rep.active() if f.tier == "verified"]
    assert verified and {f.rule_id for f in verified} == {RULE}
    assert set(rep.gate["failing_ids"]) == {f.id for f in verified if f.severity in ("critical", "high", "medium")}
    assert rep.scan_scope["tiers"]["counts"]["verified"] == len(verified)
    cfg["precision"]["ledger"] = None
    rep2 = run_scan([str(ROOT / "fixtures" / "planted" / "python" / "bad")], cfg, use_cache=False, use_adapters=False)
    assert rep2.gate["passed"], "with the shipped (empty) ledger a tiered gate blocks nothing"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def test_an_old_report_loads_as_advisory_or_inferred_never_verified():
    d = _f().to_dict()
    for k in ("tier", "tier_reason", "requirements"):
        d.pop(k)
    assert Finding.from_dict(d).tier == "advisory"
    d["provenance"] = "inferred"
    assert Finding.from_dict(d).tier == "inferred"
    d["tier"] = "verified"
    assert Finding.from_dict(d).tier == "verified"   # a report written with tiers keeps them


@_needs_ts
def test_report_round_trips_tiers_requirements_and_the_rules_block():
    rep = run_scan([str(ROOT / "fixtures" / "planted" / "python" / "bad")], _cfg(), use_cache=False, use_adapters=False)
    sql = next(f for f in rep.active() if f.rule_id == RULE)
    assert sql.requirements and all(r.startswith("ARB-") for r in sql.requirements)
    row = next(r for r in rep.rules if r["id"] == RULE)
    assert row["requirements"] == sql.requirements
    back = Report.from_dict(json.loads(json.dumps(rep.to_dict())))
    assert back.rules == rep.rules
    again = next(f for f in back.findings if f.id == sql.id)
    assert (again.tier, again.tier_reason, again.requirements) == (sql.tier, sql.tier_reason, sql.requirements)


def test_text_markdown_and_sarif_show_the_tier(tmp_path):
    v, a = _f(), _f(rule="arbiter/python.weak-hash", severity="high")
    v.tier, v.tier_reason = "verified", "ledger-admitted and model-confirmed"
    a.tier, a.tier_reason = "advisory", "rule on probation: not declared verified"
    rep = _report(v, a)
    rep.gate = evaluate_gate(rep, _cfg(tier_mode="tiered"))
    text = render_console(rep, color=False)
    assert "tiers: 1 verified · 1 advisory · 0 inferred" in text
    assert "[advisory] rule on probation" in text
    md = render_markdown(rep)
    assert "**Tiers:** 1 verified · 1 advisory" in md and "verified (ledger-admitted" in md
    write_sarif(rep, str(tmp_path / "r.sarif"))
    res = {r["ruleId"]: r for r in json.loads((tmp_path / "r.sarif").read_text())["runs"][0]["results"]}
    assert res[RULE]["level"] == "error" and res[RULE]["properties"]["tier"] == "verified"
    assert res["arbiter/python.weak-hash"]["level"] == "note"
    assert res["arbiter/python.weak-hash"]["properties"]["tier_reason"].startswith("rule on probation")


def test_sarif_levels_stay_by_severity_under_the_legacy_gate(tmp_path):
    a = _f(severity="high")
    a.tier = "advisory"
    rep = _report(a)
    rep.gate = evaluate_gate(rep, _cfg())
    write_sarif(rep, str(tmp_path / "r.sarif"))
    assert json.loads((tmp_path / "r.sarif").read_text())["runs"][0]["results"][0]["level"] == "error"
