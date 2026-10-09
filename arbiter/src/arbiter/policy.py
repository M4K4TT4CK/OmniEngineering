"""Configuration, profiles, suppressions, scoring and the gate.

Scoring exists to drive behavior, not to be precise. Two properties matter
more than the formula: findings are normalized by codebase size, and the
grade is withheld when too little of the rubric could actually be evaluated.
"""
from __future__ import annotations

import datetime as _dt
import fnmatch
from pathlib import Path
from typing import Any

from .core import (
    DIMENSIONS,
    DimensionScore,
    Finding,
    ProbeOutcome,
    Report,
    Scorecard,
    sev_at_least,
)

PROFILES: dict[str, dict] = {
    "offline": {"network": False, "model": False, "description": "air-gapped; vendored rules only"},
    "ci": {"network": False, "model": False, "description": "fast, deterministic, gate-focused"},
    "connected": {"network": True, "model": True, "description": "full depth"},
    "audit": {"network": True, "model": True, "description": "everything, no time budget"},
}

DEFAULT_WEIGHTS = {
    "security": 0.30,
    "compliance": 0.10,
    "supply_chain": 0.15,
    "quality": 0.20,
    "drift": 0.10,
    "interface": 0.15,
    # Weight zero, deliberately. Assurance findings say how much checking was
    # switched off, not how bad the code is, and folding the two into one grade
    # would be the exact conflation this dimension exists to expose. They are
    # reported, counted, and never scored.
    "assurance": 0.0,
}

DEFAULTS: dict[str, Any] = {
    "version": 1,
    "profile": "offline",
    "probes": {"enable": [], "disable": [], "severity_overrides": {}},
    "gate": {
        "fail_on": {"severity": "critical", "new": None, "coverage_below": None},
        "gate_on_inferred": False,
    },
    "score": {"weights": DEFAULT_WEIGHTS, "coverage_threshold": 0.6},
    "quality": {"max_file_lines": 800, "max_function_lines": 120, "max_complexity": 20},
    "suppress": [],
    "rules": [],
    "ignore": [],
    # The persistent per-file result cache (cache.py). `--no-cache` on the
    # command line bypasses it whatever this says; CI runs without it.
    "cache": {"enabled": True},
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | None, repo_root: str | None = None) -> dict:
    """Load arbiter.yaml. An explicit path wins; otherwise look in the repo root."""
    candidate: Path | None = None
    if path:
        candidate = Path(path)
    elif repo_root:
        for name in ("arbiter.yaml", "arbiter.yml", ".arbiter.yaml"):
            p = Path(repo_root) / name
            if p.is_file():
                candidate = p
                break
    if candidate is None or not candidate.is_file():
        return dict(DEFAULTS)
    try:
        import yaml
        data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise RuntimeError(f"could not read config {candidate}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"config {candidate} must be a mapping")
    return _merge(DEFAULTS, data)


def load_system(path: str) -> dict:
    import yaml
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or "repos" not in data:
        raise RuntimeError(f"{path} must be a mapping with a `repos` list")
    return data


# ---------------------------------------------------------------------------
# suppressions
# ---------------------------------------------------------------------------

def apply_suppressions(findings: list[Finding], config: dict) -> int:
    """Mark suppressed findings. Expiry is mandatory and enforced here."""
    rules = config.get("suppress") or []
    today = _dt.date.today()
    count = 0
    for f in findings:
        for rule in rules:
            rid = rule.get("rule")
            if rid and not (f.rule_id == rid or fnmatch.fnmatch(f.rule_id, rid)):
                continue
            pat = rule.get("path")
            if pat and not fnmatch.fnmatch(f.location.path, pat):
                continue
            fid = rule.get("id")
            if fid and f.id != fid:
                continue
            expires = rule.get("expires")
            if expires:
                try:
                    exp = expires if isinstance(expires, _dt.date) else _dt.date.fromisoformat(str(expires))
                except ValueError:
                    continue
                if exp < today:
                    continue  # expired suppressions stop suppressing, by design
            f.suppressed = True
            f.suppression_reason = rule.get("reason", "")
            count += 1
            break
    return count


def apply_severity_overrides(findings: list[Finding], config: dict) -> None:
    overrides = (config.get("probes") or {}).get("severity_overrides") or {}
    if not overrides:
        return
    for f in findings:
        for pat, sev in overrides.items():
            if f.rule_id == pat or fnmatch.fnmatch(f.rule_id, pat):
                f.severity = sev
                break


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def compute_scorecard(
    findings: list[Finding],
    outcomes: list[ProbeOutcome],
    total_loc: int,
    config: dict,
) -> Scorecard:
    weights = (config.get("score") or {}).get("weights") or DEFAULT_WEIGHTS
    threshold = float((config.get("score") or {}).get("coverage_threshold", 0.6))

    applicable: dict[str, int] = {}
    ran: dict[str, int] = {}
    for o in outcomes:
        # A probe that could never have applied here -- seams on a single
        # repository -- is not an unassessed check; there is nothing it would
        # have assessed. It leaves the denominator. A probe that was prevented
        # (missing binary, profile) stays in it: that gap is real.
        if not getattr(o, "applicable", True):
            continue
        for d in o.dimensions or []:
            if o.status in ("ran", "error", "skipped"):
                applicable[d] = applicable.get(d, 0) + _checks_of(o)
            if o.status == "ran":
                ran[d] = ran.get(d, 0) + _checks_of(o)

    size_factor = max(1.0, (max(total_loc, 1) / 2000.0) ** 0.5)

    card = Scorecard()
    active = [f for f in findings if not f.suppressed]
    for dim in DIMENSIONS:
        app = applicable.get(dim, 0)
        if app == 0:
            continue
        dim_findings = [f for f in active if f.dimension == dim]
        penalty = sum(f.weight() for f in dim_findings) / size_factor
        score = max(0.0, 100.0 - min(100.0, penalty))
        cov = min(1.0, ran.get(dim, 0) / app) if app else 0.0
        card.dimensions[dim] = DimensionScore(
            dimension=dim,
            score=round(score, 1),
            coverage=round(cov, 3),
            findings=len(dim_findings),
            checks_run=ran.get(dim, 0),
            checks_applicable=app,
        )

    total_app = sum(d.checks_applicable for d in card.dimensions.values())
    total_ran = sum(d.checks_run for d in card.dimensions.values())
    card.coverage = round(total_ran / total_app, 3) if total_app else 0.0

    present = {k: weights.get(k, 0.1) for k in card.dimensions}
    wsum = sum(present.values()) or 1.0
    if card.dimensions:
        card.overall = round(
            sum(card.dimensions[k].score * w for k, w in present.items()) / wsum, 1
        )
    if card.coverage < threshold:
        card.withheld = True
        card.overall = None
        card.withheld_reason = (
            f"assessment coverage {card.coverage:.0%} is below the {threshold:.0%} threshold; "
            "an overall grade would overstate what was actually checked"
        )
    return card


def _checks_of(o: ProbeOutcome) -> int:
    return max(1, o.checks)


# ---------------------------------------------------------------------------
# gate
# ---------------------------------------------------------------------------

def evaluate_gate(report: Report, config: dict) -> dict:
    gate_cfg = (config.get("gate") or {})
    fail_on = gate_cfg.get("fail_on") or {}
    gate_inferred = bool(gate_cfg.get("gate_on_inferred", False))

    considered = [
        f for f in report.findings
        if not f.suppressed and (gate_inferred or f.provenance != "inferred")
    ]

    reasons: list[str] = []
    # The findings behind the severity and new-finding reasons, by id, so a
    # renderer can single out what actually failed the build (the GitHub
    # annotations mark exactly these as errors) instead of every finding at
    # once. Coverage and probe-error reasons have no finding to name.
    failing: set[str] = set()

    sev_threshold = fail_on.get("severity")
    if sev_threshold:
        hits = [f for f in considered if sev_at_least(f.severity, sev_threshold)]
        if hits:
            reasons.append(f"{len(hits)} finding(s) at or above {sev_threshold}")
            failing.update(f.id for f in hits)

    new_threshold = fail_on.get("new")
    if new_threshold:
        hits = [f for f in considered if f.status == "new" and sev_at_least(f.severity, new_threshold)]
        if hits:
            reasons.append(f"{len(hits)} new finding(s) at or above {new_threshold}")
            failing.update(f.id for f in hits)

    cov_below = fail_on.get("coverage_below")
    if cov_below is not None and report.scorecard.coverage < float(cov_below):
        reasons.append(
            f"coverage {report.scorecard.coverage:.0%} below required {float(cov_below):.0%}"
        )

    errored = [p for p in report.probes if p.status == "error"]
    if errored and gate_cfg.get("fail_on_probe_error", False):
        reasons.append(f"{len(errored)} probe(s) errored")

    return {
        "passed": not reasons,
        "reasons": reasons,
        "considered": len(considered),
        "gate_on_inferred": gate_inferred,
        "failing_ids": sorted(failing),
    }
