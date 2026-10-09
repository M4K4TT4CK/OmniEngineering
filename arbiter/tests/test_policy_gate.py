"""Policy and gate: suppressions, coverage honesty, claim integrity, control
coverage and the judgement pass's effect on the gate.
"""
from __future__ import annotations

import json

from helpers import ROOT

from arbiter.core import Finding, Location
from arbiter.engine import run_scan
from arbiter.policy import apply_suppressions, load_config

# --------------------------------------------------------------------------
# coverage honesty
# --------------------------------------------------------------------------

def test_skipped_probes_are_recorded_with_a_reason(legacy_report):
    skipped = [p for p in legacy_report.probes if p.status == "skipped"]
    assert skipped
    assert all(p.reason for p in skipped), "every skip must say why"


def test_grade_is_withheld_when_coverage_is_thin(legacy_report):
    assert legacy_report.scorecard.coverage < 0.6
    assert legacy_report.scorecard.withheld
    assert legacy_report.scorecard.overall is None


def test_coverage_counts_only_probes_that_ran(legacy_report):
    for dim in legacy_report.scorecard.dimensions.values():
        assert dim.checks_run <= dim.checks_applicable


# --------------------------------------------------------------------------
# policy
# --------------------------------------------------------------------------

def test_expired_suppressions_stop_suppressing():
    f = Finding(rule_id="arbiter/x", title="t", location=Location(path="a.py"))
    cfg = {"suppress": [{"rule": "arbiter/x", "reason": "r", "expires": "2000-01-01"}]}
    apply_suppressions([f], cfg)
    assert not f.suppressed


def test_live_suppressions_apply():
    f = Finding(rule_id="arbiter/x", title="t", location=Location(path="a.py"))
    cfg = {"suppress": [{"rule": "arbiter/*", "reason": "accepted", "expires": "2099-01-01"}]}
    apply_suppressions([f], cfg)
    assert f.suppressed and f.suppression_reason == "accepted"


def test_gate_fails_on_critical(legacy_report):
    assert legacy_report.gate["passed"] is False
    assert any("critical" in r for r in legacy_report.gate["reasons"])


# --------------------------------------------------------------------------
# Claim integrity
#
# Verdict correctness on arbitrary code is undecidable. Claim integrity is a
# property of Arbiter's own execution, so it can be enumerated rather than
# sampled — these tests are a proof over a bounded space, not an estimate.
# --------------------------------------------------------------------------

def test_real_reports_pass_their_own_integrity_check(legacy_report, system_report, plan_report):
    for rep in (legacy_report, system_report, plan_report):
        assert rep.integrity["ok"], rep.integrity["violations"]


def test_every_invariant_is_enforced():
    """A declared invariant nobody can trip is decoration."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("integ", ROOT / "tools" / "integrity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tested, unenforced = mod.check_enforcement()
    assert tested >= 8
    assert unenforced == [], f"invariants declared but not enforced: {unenforced}"


def test_bounded_state_space_is_exhaustively_clean():
    import importlib.util
    spec = importlib.util.spec_from_file_location("integ", ROOT / "tools" / "integrity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    checked, failures = mod.enumerate_space(3, 2, (0.0, 0.6, 1.0))
    assert checked >= 2000
    assert failures == [], f"{len(failures)} reports asserted more than they verified"


def test_a_dimension_with_no_basis_cannot_claim_complete(legacy_report):
    """interface used to score 100 on a single repository because nothing
    ran, and the claim had to read as partial. Since REQ-033 a probe that
    declares itself not applicable is not scored at all: there is no
    dimension claim to get wrong, and the coverage claim names the probe so
    the omission is visible rather than silent."""
    claims = {c["id"]: c for c in legacy_report.claims}
    assert "dimension:interface" not in claims
    assert "interface" not in legacy_report.scorecard.dimensions
    assert "not applicable here: interface" in claims["coverage"]["statement"]
    # and the general rule still holds for every dimension that IS scored
    for cid, c in claims.items():
        if cid.startswith("dimension:") and c["basis"] == []:
            assert c["scope"] == "partial", cid


def test_wilson_bound_refuses_to_flatter_small_samples():
    from arbiter.claims import wilson_lower_bound
    assert wilson_lower_bound(10, 10) < 0.80, "ten for ten is not six nines"
    assert wilson_lower_bound(0, 0) == 0.0
    assert wilson_lower_bound(999_999, 1_000_000) > 0.99999


def test_sample_size_maths_is_stated_not_assumed():
    from arbiter.claims import observations_needed
    assert observations_needed(1e-6) > 2_900_000
    assert observations_needed(1e-2) < 400


# ---------------------------------------------------------------------------
# Control coverage.
#
# The failure mode this guards against is the one every compliance scanner
# has: reporting "87% compliant" where most of that 87% is controls nothing
# ever looked at. A control with no evidence must never read as a pass.
# ---------------------------------------------------------------------------

def _fw(tmp_path, controls, declared=100):
    import yaml as _yaml

    from arbiter.controls import load_pack
    p = tmp_path / "f.yaml"
    p.write_text(_yaml.safe_dump({
        "framework": {"id": "TEST", "title": "Test", "declared_controls": declared},
        "controls": controls,
    }))
    return load_pack(p)


def _outcome(name, status="ran", applicable=True, reason=""):
    from arbiter.core import ProbeOutcome
    return ProbeOutcome(name=name, status=status, applicable=applicable, reason=reason)


def test_a_control_nothing_checked_is_never_a_pass(tmp_path):
    from arbiter.controls import NOT_ASSESSED, evaluate
    fw = _fw(tmp_path, [{"id": "SC-28", "automatable": "partial",
                         "satisfied_by": ["arbiter/resource.unencrypted-database"]}])
    # the probe was prevented from running — not inapplicable, prevented
    res = evaluate(fw, [], [_outcome("resource_policy", "skipped", applicable=True,
                                     reason="missing binary")])
    assert res["controls"][0]["state"] == NOT_ASSESSED
    assert res["counts"]["satisfied"] == 0


def test_a_control_whose_checks_ran_clean_is_satisfied(tmp_path):
    from arbiter.controls import SATISFIED, evaluate
    fw = _fw(tmp_path, [{"id": "SC-28", "automatable": "partial",
                         "satisfied_by": ["arbiter/resource.unencrypted-database"]}])
    res = evaluate(fw, [], [_outcome("resource_policy")])
    assert res["controls"][0]["state"] == SATISFIED


def test_a_fired_check_violates_its_control(tmp_path):
    from arbiter.controls import VIOLATED, evaluate
    fw = _fw(tmp_path, [{"id": "SC-28", "automatable": "partial",
                         "satisfied_by": ["arbiter/resource.unencrypted-database"]}])
    f = Finding(rule_id="arbiter/resource.unencrypted-database", title="x",
                location=Location(path="a.tf"))
    res = evaluate(fw, [f], [_outcome("resource_policy")])
    assert res["controls"][0]["state"] == VIOLATED
    assert res["controls"][0]["evidence"] == [f.id]


def test_an_unknown_value_is_not_assessed_not_satisfied(tmp_path):
    """A Terraform plan's after_unknown means the check could not conclude.
    Treating that as a pass is exactly how a scanner reports an encrypted
    bucket as compliant when it has no idea."""
    from arbiter.controls import NOT_ASSESSED, evaluate
    fw = _fw(tmp_path, [{"id": "SC-28", "automatable": "partial",
                         "satisfied_by": ["arbiter/resource.unencrypted-database"]}])
    na = Finding(rule_id="arbiter/resource.unencrypted-database.not-assessed",
                 title="cannot evaluate", severity="info", location=Location(path="a.tf"))
    res = evaluate(fw, [na], [_outcome("resource_policy")])
    assert res["controls"][0]["state"] == NOT_ASSESSED
    assert "undetermined" in res["controls"][0]["reason"]


def test_an_inapplicable_check_is_not_a_gap(tmp_path):
    """bandit not running against a Terraform-only repository is not a hole in
    the assessment of 'review human-readable code'. There is no Python."""
    from arbiter.controls import NO_COVERAGE, SATISFIED, evaluate
    fw = _fw(tmp_path, [
        {"id": "A", "automatable": "partial", "satisfied_by": ["bandit/B105"]},
        {"id": "B", "automatable": "partial",
         "satisfied_by": ["bandit/B105", "arbiter/resource.unencrypted-database"]},
    ])
    outcomes = [_outcome("bandit", "skipped", applicable=False, reason="no python detected"),
                _outcome("resource_policy")]
    states = {r["id"]: r["state"] for r in evaluate(fw, [], outcomes)["controls"]}
    # every covering check inapplicable -> no coverage for this target
    assert states["A"] == NO_COVERAGE
    # one inapplicable, one ran clean -> satisfied on the applicable one
    assert states["B"] == SATISFIED


def test_a_not_applicable_probe_leaves_the_coverage_denominator():
    """Seams on a single repository are not an unassessed check; there is
    nothing the probe would have assessed. The interface dimension read 0%
    on every single-repo scan and dragged the overall figure below the
    threshold for the wrong reason. A probe that was PREVENTED from running
    is a different fact and stays in the denominator."""
    from arbiter.core import ProbeOutcome
    from arbiter.policy import DEFAULTS, compute_scorecard
    ran = ProbeOutcome(name="secrets", status="ran", dimensions=["security"], checks=10)
    prevented = ProbeOutcome(name="bandit", status="skipped", reason="missing binary: bandit",
                             dimensions=["security"], checks=10)
    na = ProbeOutcome(name="interface", status="skipped", applicable=False,
                      reason="system has a single repo; no seams to check",
                      dimensions=["interface"], checks=6)
    with_na = compute_scorecard([], [ran, prevented, na], 1000, DEFAULTS)
    without = compute_scorecard([], [ran, prevented], 1000, DEFAULTS)
    assert with_na.coverage == without.coverage == 0.5
    assert "interface" not in with_na.dimensions


def test_a_missing_dependency_is_prevented_not_inapplicable(tmp_path):
    """The Windows job has no tree-sitter. Its AST probes reported themselves
    not applicable and left the coverage denominator, which is the exact
    laundering CI-12 exists to refuse: a check with a question to answer here
    that could not answer it. A missing package is a prevented probe."""
    from arbiter.probes import Probe
    ghost = Probe(name="ghost", dimensions=["quality"], checks=2, run=lambda ctx: [],
                  modules=["arbiter_no_such_module_xyz"])
    blocked, why = ghost.prevented()
    assert blocked and "missing python package" in why
    (tmp_path / "a.py").write_text("x = 1\n")
    import arbiter.probes as probes_mod
    probes_mod.REGISTRY.append(ghost)
    try:
        rep = run_scan([str(tmp_path)], load_config(None), only=["ghost"], use_adapters=False)
    finally:
        probes_mod.REGISTRY.remove(ghost)
    outcome = next(p for p in rep.probes if p.name == "ghost")
    assert outcome.status == "skipped" and outcome.applicable is True
    assert "missing python package" in outcome.reason
    from arbiter.claims import verify
    assert not [v for v in verify(rep, load_config(None)) if v.invariant == "CI-12"]


def test_not_applicable_cannot_launder_a_prevented_probe():
    """CI-12: a report may mark a probe not applicable only when it could
    never have applied. Pairing applicable=False with the engine's own
    "missing binary" wording is a coverage gap wearing a non-question's
    clothes, and the ledger refuses it."""
    from arbiter.claims import verify
    from arbiter.core import ProbeOutcome, Report
    from arbiter.policy import DEFAULTS, compute_scorecard
    honest = ProbeOutcome(name="interface", status="skipped", applicable=False,
                          reason="system has a single repo; no seams to check",
                          dimensions=["interface"], checks=6)
    laundered = ProbeOutcome(name="bandit", status="skipped", applicable=False,
                             reason="missing binary: bandit", dimensions=["security"], checks=4)
    for probes, expect in (([honest], 0), ([honest, laundered], 1)):
        rep = Report(system="t", findings=[], probes=probes)
        rep.scorecard = compute_scorecard([], probes, 1000, DEFAULTS)
        ci12 = [v for v in verify(rep, DEFAULTS) if v.invariant == "CI-12"]
        assert len(ci12) == expect


def test_procedural_controls_are_marked_not_automatable(tmp_path):
    """Personnel screening is not a failure and not a pass. Reporting it as
    either is dishonest; it belongs to a human assessor."""
    from arbiter.controls import NOT_AUTOMATABLE, evaluate
    fw = _fw(tmp_path, [{"id": "PS-3", "automatable": "none", "satisfied_by": []}])
    r = evaluate(fw, [], [])["controls"][0]
    assert r["state"] == NOT_AUTOMATABLE
    assert "no static analyzer" in r["reason"]


def test_coverage_is_measured_against_the_real_baseline_size(tmp_path):
    """A pack that enumerates the three controls it covers must not report
    100% coverage. The denominator is the framework's actual size."""
    from arbiter.controls import evaluate
    fw = _fw(tmp_path, [{"id": "SC-28", "automatable": "partial",
                         "satisfied_by": ["arbiter/resource.unencrypted-database"]}],
             declared=323)
    res = evaluate(fw, [], [_outcome("resource_policy")])
    assert res["declared_total"] == 323
    assert res["not_enumerated"] == 322
    assert res["assessed_fraction"] == round(1 / 323, 4)


def test_shipped_packs_all_declare_a_real_baseline_size(tmp_path):
    """Every pack must state how big its framework actually is, or its
    coverage figure is meaningless."""
    from arbiter.controls import load_frameworks
    fws = load_frameworks()
    assert len(fws) >= 5
    for fw in fws:
        assert fw.declared_controls > 0, f"{fw.id} declares no baseline size"
        assert fw.declared_source, f"{fw.id} cites no source for its baseline size"
        assert fw.enumerated <= fw.declared_controls, f"{fw.id} enumerates more than it declares"


def test_shipped_packs_say_what_a_person_must_still_check(tmp_path):
    """An automatable control that claims no residual is claiming a scan
    fully discharges it, which is never true."""
    from arbiter.controls import load_frameworks
    for fw in load_frameworks():
        for c in fw.controls:
            assert c.residual, f"{fw.id}:{c.id} does not say what remains for a person"
            if c.automatable != "none":
                assert c.satisfied_by, f"{fw.id}:{c.id} claims automatable with no checks"
                assert c.machine_scope, f"{fw.id}:{c.id} does not say what it can establish"


def test_shipped_packs_reference_rules_that_exist(tmp_path):
    """A mapping to a rule id that no longer exists silently becomes a control
    that can never be violated — a permanent false pass."""
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "src"))
    from arbiter.controls import load_frameworks
    from arbiter.probes import _load_resource_rules
    known = {f"arbiter/resource.{r['id']}" for r in _load_resource_rules()}
    # Several native rules are emitted directly from probes.py rather than
    # declared in the YAML pack, so the rule pack alone is not the full set.
    import re as _re
    src = (ROOT / "src" / "arbiter" / "probes.py").read_text()
    known |= set(_re.findall(r'rule_id=f?"(arbiter/[a-z_]+\.[a-z0-9.-]+)"', src))
    src_seams = (ROOT / "src" / "arbiter" / "probes.py").read_text()
    known |= set(_re.findall(r'"(arbiter/interface\.[a-z-]+)"', src_seams))
    unknown = []
    for fw in load_frameworks():
        for c in fw.controls:
            for ch in c.satisfied_by:
                if ch.startswith("arbiter/resource.") and ch not in known:
                    unknown.append(f"{fw.id}:{c.id} -> {ch}")
    assert not unknown, f"control packs reference rules that do not exist: {unknown}"


# ---------------------------------------------------------------------------
# The judgement pass.
#
# The whole reason this module exists as more than a single API call is the
# no-provider case. The easy implementation returns an empty list when there is
# no key, and an empty list is indistinguishable from "the model looked and
# found nothing" — so every unconfigured scan would silently report clean
# documentation drift forever.
# ---------------------------------------------------------------------------

def test_no_provider_is_not_assessed_never_a_pass(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "README.md").write_text("# App\nRuns on port 8080.\n")
    (tmp_path / "app.py").write_text("PORT = 9090\n")
    rep = run_scan([str(tmp_path)], dict(load_config(None), profile="connected"),
                   only=["judgement"], use_adapters=False)
    j = next(p for p in rep.probes if p.name == "judgement")
    assert j.status == "skipped", "must not report as having run"
    assert "ANTHROPIC_API_KEY" in j.reason, "must say why"
    assert not [f for f in rep.findings if f.probe == "judgement"]


def test_offline_profile_refuses_model_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-a-real-key")
    (tmp_path / "README.md").write_text("# App\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["judgement"],
                   use_adapters=False)
    j = next(p for p in rep.probes if p.name == "judgement")
    assert j.status == "skipped" and "forbids" in j.reason


def test_judgement_probe_always_counts_against_coverage(tmp_path, monkeypatch):
    """A probe that does not register never shows up as missing, which would
    quietly flatter every scan that has no model configured."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "README.md").write_text("# App\n")
    rep = run_scan([str(tmp_path)], dict(load_config(None), profile="connected"),
                   use_adapters=False)
    assert any(p.name == "judgement" for p in rep.probes)


def test_inferred_findings_do_not_gate_by_default():
    from arbiter.core import Report, Scorecard
    from arbiter.policy import evaluate_gate
    rep = Report()
    rep.findings = [Finding(rule_id="arbiter/judgement.claim-contradicts-code",
                            title="doc disagrees", severity="high",
                            provenance="inferred", location=Location(path="README.md"))]
    rep.scorecard = Scorecard(coverage=1.0, overall=90.0)
    cfg = dict(load_config(None))
    cfg["gate"] = dict(cfg.get("gate") or {}, max_severity="medium", min_coverage=0.0)
    assert evaluate_gate(rep, cfg)["passed"], \
        "a model's opinion must not turn a build red on its own"


def test_a_model_naming_a_file_it_was_not_shown_is_discarded():
    """The failure this guards is a model citing a path it half-remembers from
    training. Such a finding points at evidence that does not exist here."""
    from arbiter.judgement import _reconcile
    raw = json.dumps({"findings": [
        {"path": "README.md", "line": 3, "title": "port disagrees",
         "detail": "README says 8080, code says 9090", "confidence": "high"},
        {"path": "src/never/sent.py", "line": 1, "title": "invented",
         "detail": "not a file we showed", "confidence": "high"},
    ]})
    out = _reconcile(raw, {"README.md"}, "root")
    assert len(out) == 1 and out[0].location.path == "README.md"


def test_model_confidence_never_reaches_high():
    """Confidence stated by a model is not the same quantity as confidence
    measured from adjudicated outcomes. Sharing a scale would be a category
    error, so inferred findings are capped."""
    from arbiter.judgement import _reconcile
    raw = json.dumps({"findings": [{"path": "a.md", "title": "t", "detail": "d",
                                    "confidence": "high"}]})
    f = _reconcile(raw, {"a.md"}, "root")[0]
    assert f.confidence != "high" and f.severity != "critical"
    assert f.provenance == "inferred"


def test_secrets_are_masked_before_leaving_the_machine():
    from arbiter.judgement import _mask_secrets
    text = ("aws_key = AKIAIOSFODNN7EXAMPLE\n"
            "db_password = hunter2hunter2\n"
            "-----BEGIN RSA PRIVATE KEY-----\nMIIC\n-----END RSA PRIVATE KEY-----\n")
    out = _mask_secrets(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "hunter2hunter2" not in out
    assert "MIIC" not in out


def test_malformed_model_output_yields_nothing_rather_than_crashing():
    from arbiter.judgement import _reconcile
    for raw in ("not json at all", "", "{", '{"findings": "wrong type"}',
                '{"findings":[{"no_path":1}]}'):
        assert _reconcile(raw, {"a.md"}, "root") == []


def test_every_report_carries_control_coverage(tmp_path):
    """A compliance figure quoted without its denominator is the thing this
    tool exists to stop doing, so the denominator ships in the report."""
    (tmp_path / "main.tf").write_text(
        'resource "aws_db_instance" "d" {\n  identifier = "x"\n}\n')
    rep = run_scan([str(tmp_path)], load_config(None), only=["resource_policy"],
                   use_adapters=False)
    assert rep.controls, "no frameworks evaluated"
    by_id = {c["framework"]: c for c in rep.controls}
    fr = by_id["FedRAMP-Moderate-r5"]
    assert fr["declared_total"] == 323
    assert fr["not_enumerated"] > 250, "the unenumerated remainder must be visible"
    assert fr["assessed_fraction"] < 0.2, \
        "a handful of checks must never read as broad compliance"
    d = json.loads(json.dumps(rep.to_dict()))
    assert d["controls"][0]["counts"]["not_automatable"] >= 0


def test_scope_note_annotates_but_never_suppresses(tmp_path):
    """A repo's own docs can point a reviewer at a control that covers a
    finding, but a claim in prose is not a check that ran -- it must not
    change severity, status, or suppression on its own (controls.py)."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "security.md").write_text(
        "# Security\n\n"
        "## Known limitations\n\n"
        "- `deploy/` secrets are placeholders rotated by the deploy pipeline.\n"
    )
    (tmp_path / "deploy").mkdir()
    key = "-----BEGIN PRIVATE KEY-----\nMIIBVQIBADAN\n-----END PRIVATE KEY-----\n"
    (tmp_path / "deploy" / "server.key").write_text(key)

    found = run_scan([str(tmp_path)], load_config(None), only=["secrets"]).active()
    assert found and found[0].severity == "critical"
    assert not found[0].suppressed
    assert "docs/security.md" in found[0].scope_note
    assert "unverified" in found[0].scope_note


def test_scope_note_requires_a_backtick_path_match(tmp_path):
    """Free-text scope-note bullets with no quoted path match nothing --
    guessing at prose similarity would make this module wrong quietly."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "security.md").write_text(
        "## Known limitations\n\n"
        "- Secrets under the deploy directory are rotated automatically.\n"
    )
    (tmp_path / "deploy").mkdir()
    key = "-----BEGIN PRIVATE KEY-----\nMIIBVQIBADAN\n-----END PRIVATE KEY-----\n"
    (tmp_path / "deploy" / "server.key").write_text(key)

    found = run_scan([str(tmp_path)], load_config(None), only=["secrets"]).active()
    assert found and found[0].scope_note == ""
