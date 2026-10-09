"""Tests for the code that judges the rules, not for the rules.

A wrong rule reports a wrong finding, and somebody notices. A wrong
*measurement* certifies the wrong rule, and nobody does -- the number it
prints is the thing you would have used to check. This project has already
been bitten there: a held-out comparison called Kubernetes findings "2.09x
worse" on held-out code, and all three causes were measurement bugs (it
matched a repository by its label rather than its content, counted findings
instead of weighting them by severity and confidence, and used one repository
per side as though that were a sample). Separately, three dependency-pinning
rules carried a hand-assigned severity until measurement showed two of them
fire *more* on good code than on bad.

So this file tests the arithmetic and the guarantees around it: the
confidence interval, the threshold that decides a rule is proven, and the
separation between what a person judged and what a generator produced.
"""
from __future__ import annotations

import json

import pytest

from arbiter.claims import wilson_lower_bound
from arbiter.core import Finding, Location
from arbiter.learn import (
    MIN_OBSERVATIONS,
    Knowledge,
    RuleStats,
    apply,
    calibrated_confidence,
    record_synthetic,
)

# --------------------------------------------------------------------------
# The confidence interval
# --------------------------------------------------------------------------

def test_a_perfect_run_is_not_certainty():
    """Ten for ten is evidence of a rate above roughly 0.72, not of six nines.

    This is the whole reason a lower bound is used instead of the ratio. A
    rule that has never been wrong in ten tries and one that has never been
    wrong in ten thousand are not the same claim.
    """
    assert wilson_lower_bound(10, 10) < 0.85
    assert wilson_lower_bound(10_000, 10_000) > 0.99
    assert wilson_lower_bound(10, 10) < wilson_lower_bound(10_000, 10_000)


def test_the_bound_never_leaves_the_unit_interval():
    for successes, n in [(0, 0), (0, 1), (1, 1), (0, 100), (50, 100), (100, 100)]:
        lb = wilson_lower_bound(successes, n)
        assert 0.0 <= lb <= 1.0, f"{successes}/{n} produced {lb}"


def test_the_bound_never_exceeds_what_was_observed():
    """A lower bound above the point estimate would be an invented claim."""
    for successes, n in [(1, 2), (3, 4), (9, 10), (99, 100), (1, 1000)]:
        assert wilson_lower_bound(successes, n) <= successes / n


def test_more_evidence_at_the_same_rate_tightens_the_bound():
    prev = -1.0
    for n in (10, 100, 1_000, 10_000):
        lb = wilson_lower_bound(int(n * 0.9), n)
        assert lb > prev, "the bound must rise as the same rate is observed more often"
        prev = lb


def test_no_observations_claims_nothing():
    assert wilson_lower_bound(0, 0) == 0.0


# --------------------------------------------------------------------------
# The threshold that decides a rule is proven
# --------------------------------------------------------------------------

def test_a_rule_is_unproven_one_observation_short():
    """The boundary itself, so a change to it has to be deliberate."""
    stats = RuleStats(rule_id="r", true_positives=MIN_OBSERVATIONS - 1, false_positives=0)
    assert stats.observations == MIN_OBSERVATIONS - 1
    assert not stats.proven
    assert calibrated_confidence(stats) is None


def test_a_rule_becomes_proven_exactly_at_the_threshold():
    stats = RuleStats(rule_id="r", true_positives=MIN_OBSERVATIONS, false_positives=0)
    assert stats.proven
    assert calibrated_confidence(stats) is not None


def test_being_proven_is_not_the_same_as_being_right():
    """Twenty verdicts that mostly went against the rule must not read high."""
    bad = RuleStats(rule_id="r", true_positives=4, false_positives=16)
    assert bad.proven
    assert calibrated_confidence(bad) == "low"


# --------------------------------------------------------------------------
# The separation the whole design rests on
# --------------------------------------------------------------------------

@pytest.mark.parametrize("n", [1, 10, 1_000, 500_000])
def test_generated_evidence_never_makes_a_rule_proven(n):
    """Injection trials must not move calibration, at any volume.

    The live ledger holds 227,999 synthetic observations and no human
    verdicts, and reports exactly zero proven rules. If generated evidence
    counted, a night of trials would manufacture confidence for every rule in
    the pack, and the number a report prints would describe the generator
    rather than the code.
    """
    k = Knowledge()
    for _ in range(min(n, 1000)):
        record_synthetic(k, "arbiter/demo", "detected", n=max(1, n // 1000))
    stats = k.rules["arbiter/demo"]
    assert stats.observations == 0, "synthetic trials must not count as observations"
    assert not stats.proven
    assert calibrated_confidence(stats) is None


def test_synthetic_volume_does_not_shift_a_calibrated_confidence():
    """Hold the human verdicts fixed, vary the generated ones, expect no move."""
    def confidence_with(synthetic: int) -> str | None:
        k = Knowledge()
        stats = RuleStats(rule_id="arbiter/demo", true_positives=18, false_positives=2)
        k.rules["arbiter/demo"] = stats
        for _ in range(synthetic):
            record_synthetic(k, "arbiter/demo", "detected", n=100)
        return calibrated_confidence(k.rules["arbiter/demo"])

    baseline = confidence_with(0)
    assert baseline is not None
    for volume in (1, 50, 500):
        assert confidence_with(volume) == baseline


def test_learning_never_changes_a_native_rules_severity():
    """Severity is policy. Measurement may say how often a rule is right, not
    how much it matters when it is."""
    k = Knowledge()
    k.rules["arbiter/native"] = RuleStats(rule_id="arbiter/native",
                                          true_positives=40, false_positives=0)
    f = Finding(rule_id="arbiter/native", title="t", severity="high",
                confidence="medium", location=Location(path="a.tf"), evidence="e")
    apply([f], k)
    assert f.severity == "high", "calibration moved a native rule's severity"


# --------------------------------------------------------------------------
# Measured severity for external checks
# --------------------------------------------------------------------------

def test_a_measured_severity_reaches_a_scan(tmp_path):
    """The merge that was missing.

    `tools/calibrate_external.py` wrote its severities to a report file and
    `apply()` read a map inside knowledge.json. Nothing moved a value from one
    to the other, so every nightly measurement of the external tools changed
    nothing at all, and the severities in the live ledger came from a one-off
    step that no longer exists.
    """
    report = tmp_path / "external-severity.json"
    report.write_text(json.dumps({
        "checks": {
            "checkov/CKV_AWS_1": {"severity": "low"},
            "checkov/CKV_AWS_2": {"severity": None},   # too few observations to grade
        }
    }), encoding="utf-8")

    k = Knowledge()
    merged = k.merge_external_severity(str(report))
    assert merged == 1
    assert k.external_severity == {"checkov/CKV_AWS_1": "low"}, (
        "a check the tool declined to grade must not be stored")

    graded = Finding(rule_id="checkov/CKV_AWS_1", title="t", severity="medium",
                     location=Location(path="a.tf"), evidence="e")
    ungraded = Finding(rule_id="checkov/CKV_AWS_2", title="t", severity="medium",
                       location=Location(path="a.tf"), evidence="e")
    apply([graded, ungraded], k)

    assert graded.severity == "low"
    assert "severity:measured" in graded.tags
    assert "severity-was:medium" in graded.tags
    assert ungraded.severity == "medium", "an ungraded check keeps the tool's own severity"


def test_measurement_cannot_promote_a_check_to_high():
    """Discrimination measures signal; severity encodes consequence.

    A check requiring a description on every security-group rule fires 33
    times on deliberately broken Terraform and never on well-maintained
    repositories. The ratio is real and reproducible. It is also not a
    security finding, and grading it `high` would put a missing comment
    beside a public bucket.
    """
    import sys
    sys.path.insert(0, "tools")
    from calibrate_external import band

    for ratio in (3.0, 10.0, 50.0, 1_000.0, float("inf")):
        assert band(ratio) != "high", f"ratio {ratio} was promoted to high"
    assert band(10.0) == "medium"
    assert band(3.0) == "low"
    assert band(1.0) == "info"
