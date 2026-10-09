"""Claim integrity.

Arbiter's central promise is that it never asserts something it did not
verify. Stated in prose that is a design principle; stated here it is a
machine-checkable invariant with a test suite behind it.

The distinction that makes this tractable: verdict correctness on arbitrary
code is undecidable, but *claim integrity is a property of Arbiter itself*.
Whether a bucket is really public depends on code we cannot fully analyze;
whether the report claimed the bucket was fine without running the check is a
question about our own execution, and that is decidable, enumerable and
testable.

Every assertion a report makes becomes a Claim carrying its basis (checks that
ran and concluded) and its abstentions (checks that did not). An invariant is
violated when a claim's strength exceeds its basis.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .core import Report

# Claim kinds
GATE = "gate"
GRADE = "grade"
DIMENSION = "dimension"
COVERAGE = "coverage"
PROBE_CLEAN = "probe_clean"

# Scope — how strong the claim is allowed to be
COMPLETE = "complete"   # every applicable check ran and concluded
PARTIAL = "partial"     # some checks abstained; the claim is scoped to those that ran
WITHHELD = "withheld"   # too little ran to say anything


@dataclass
class Claim:
    id: str
    kind: str
    statement: str
    scope: str
    basis: list[str] = field(default_factory=list)
    abstained: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Violation:
    invariant: str
    claim_id: str
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Deriving the claims a report makes
# ---------------------------------------------------------------------------

def unread_files_abstention(report: Report) -> str:
    """The abstention a partial scan carries, or "" for a full scan.

    A partial scan did not read most of the repository. That is not a probe
    failure -- the probes that ran did their job -- but it is still something
    the report did not look at, and every claim built on it must say so. One
    entry is enough: it flows into the gate, the grade and every dimension.
    """
    sc = report.scan_scope or {}
    if sc.get("mode") != "partial":
        return ""
    read = sc.get("files_read", 0)
    total = sc.get("files_total", 0)
    return f"scan:{total - read} of {total} file(s) not read ({sc.get('basis', 'partial scan')})"


def abstentions(report: Report) -> list[str]:
    """Every check that did not run and conclude, named."""
    out: list[str] = []
    unread = unread_files_abstention(report)
    if unread:
        out.append(unread)
    for p in report.probes:
        if p.status != "ran" and getattr(p, "applicable", True):
            # A probe that could never have applied here is not an abstention:
            # it had no question to answer. It is still named in the coverage
            # claim, because "nothing here to assess" and "could not assess"
            # must never read the same, and CI-12 refuses the second dressed
            # as the first.
            out.append(f"probe:{p.name}")
    # A rule that matched a resource but could not be evaluated is an
    # abstention too, even though its probe ran.
    for f in report.findings:
        if f.rule_id.endswith(".not-assessed"):
            out.append(f"check:{f.rule_id[:-len('.not-assessed')]}@{f.location.logical or f.location.path}")
    return sorted(set(out))


def build_claims(report: Report) -> list[Claim]:
    absts = abstentions(report)
    ran = [f"probe:{p.name}" for p in report.probes if p.status == "ran"]
    claims: list[Claim] = []

    gate = report.gate or {}
    if gate:
        passed = bool(gate.get("passed"))
        claims.append(Claim(
            id="gate",
            kind=GATE,
            statement=("no configured threshold was violated by the checks that ran"
                       if passed else "a configured threshold was violated"),
            scope=(COMPLETE if (passed and not absts) else (PARTIAL if passed else COMPLETE)),
            basis=ran,
            abstained=absts if passed else [],
        ))

    sc = report.scorecard
    if sc.overall is not None:
        claims.append(Claim(
            id="grade",
            kind=GRADE,
            statement=f"overall score {sc.overall}",
            scope=COMPLETE if sc.coverage >= 1.0 else PARTIAL,
            basis=ran,
            abstained=absts,
        ))
    else:
        claims.append(Claim(
            id="grade", kind=GRADE,
            statement="no overall score asserted",
            scope=WITHHELD, basis=[], abstained=absts,
        ))

    for name, dim in sc.dimensions.items():
        dim_abst = [
            f"probe:{p.name}" for p in report.probes
            if p.status != "ran" and name in (p.dimensions or [])
            and getattr(p, "applicable", True)
        ]
        # A dimension can reach full probe coverage in a partial scan -- if
        # every probe carrying that dimension is file-scoped, they all ran.
        # They ran on half the files. The score is a fact about those files
        # and not about the repository, so the unread files abstain here too.
        if unread_files_abstention(report):
            dim_abst.append(unread_files_abstention(report))
        claims.append(Claim(
            id=f"dimension:{name}",
            kind=DIMENSION,
            statement=f"{name} scores {dim.score} with {dim.findings} finding(s)",
            scope=COMPLETE if (dim.coverage >= 1.0 and not dim_abst) else PARTIAL,
            basis=[f"probe:{p.name}" for p in report.probes
                   if p.status == "ran" and name in (p.dimensions or [])],
            abstained=sorted(set(dim_abst)),
        ))

    unread = unread_files_abstention(report)
    for p in report.probes:
        if p.status == "ran" and p.finding_count == 0:
            # In a partial scan this probe read a subset. "Found nothing" is
            # true of what it was given and false of the repository, so the
            # claim is narrowed rather than deleted.
            claims.append(Claim(
                id=f"probe_clean:{p.name}",
                kind=PROBE_CLEAN,
                statement=(f"{p.name} ran and found nothing" if not unread else
                           f"{p.name} found nothing in the files it was given"),
                scope=COMPLETE if not unread else PARTIAL,
                basis=[f"probe:{p.name}"],
                abstained=[] if not unread else [unread],
            ))

    # The coverage figure is a measurement *about* the abstentions, not a
    # claim weakened by them: "57% of applicable checks ran" is exactly true
    # however many abstained. Listing them here would mean the report cannot
    # state its own incompleteness without violating an invariant.
    not_applicable = [p.name for p in report.probes if not getattr(p, "applicable", True)]
    claims.append(Claim(
        id="coverage",
        kind=COVERAGE,
        statement=(f"{sc.coverage:.3f} of applicable checks ran; {len(absts)} abstention(s)"
                   + (f"; not applicable here: {', '.join(not_applicable)}" if not_applicable else "")),
        scope=COMPLETE,
        basis=ran,
        abstained=[],
    ))
    return claims


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------
#
# Each is stated so that a violation means Arbiter asserted more than it
# verified. These are the properties the six-nines target attaches to.

INVARIANTS = {
    "CI-1": "a complete-scope claim may not have abstentions",
    "CI-2": "an overall grade requires coverage at or above the configured threshold",
    "CI-3": "a withheld grade must not also report an overall score",
    "CI-4": "every probe that did not run must give a reason",
    "CI-5": "a dimension claiming full coverage must have run every applicable check",
    "CI-6": "suppression may hide findings but must never raise coverage",
    "CI-7": "a probe that errored must not be counted as a clean result",
    "CI-8": "checks recorded as not-assessed must appear in the abstention list",
    "CI-9": "coverage may never exceed 1.0 or fall below 0.0",
    "CI-10": "a passing gate with abstentions must be scoped partial, not complete",
    "CI-11": "a partial scan may make no complete-scope claim about the repository",
    "CI-12": "a probe recorded as not applicable must not be one that was merely prevented from running",
}

# The engine's own wording for a probe it was PREVENTED from running. A report
# that pairs one of these with applicable=False is laundering a coverage gap
# as a non-question, which is exactly the move CI-12 exists to refuse.
_PREVENTED_REASONS = ("missing binary", "missing python package", "not installed", "forbids",
                      "not selected", "disabled in configuration", "partial scan")


def verify(report: Report, config: dict | None = None) -> list[Violation]:
    """Check every invariant. An empty list is the only acceptable result."""
    config = config or {}
    threshold = float((config.get("score") or {}).get("coverage_threshold", 0.6))
    claims = build_claims(report)
    by_id = {c.id: c for c in claims}
    out: list[Violation] = []
    absts = abstentions(report)
    sc = report.scorecard

    for c in claims:
        if c.scope == COMPLETE and c.abstained:
            out.append(Violation("CI-1", c.id,
                                 f"scope=complete but {len(c.abstained)} abstention(s): "
                                 f"{', '.join(c.abstained[:4])}"))

    if sc.overall is not None and sc.coverage < threshold:
        out.append(Violation("CI-2", "grade",
                             f"overall={sc.overall} at coverage {sc.coverage:.3f} "
                             f"below threshold {threshold}"))

    if sc.withheld and sc.overall is not None:
        out.append(Violation("CI-3", "grade", "withheld is set yet an overall score is present"))

    for p in report.probes:
        if p.status != "ran" and not (p.reason or "").strip():
            out.append(Violation("CI-4", f"probe:{p.name}", "no reason recorded"))
        if p.status == "error" and p.finding_count:
            out.append(Violation("CI-7", f"probe:{p.name}",
                                 "errored probe reported findings"))

    for p in report.probes:
        if getattr(p, "applicable", True):
            continue
        reason = (p.reason or "").lower()
        if p.status == "ran" or p.status == "error":
            out.append(Violation("CI-12", f"probe:{p.name}",
                                 f"not applicable yet status is {p.status}"))
        elif any(marker in reason for marker in _PREVENTED_REASONS):
            out.append(Violation("CI-12", f"probe:{p.name}",
                                 f"not applicable yet the reason says it was prevented: {p.reason}"))

    for name, dim in sc.dimensions.items():
        if dim.coverage >= 1.0 and dim.checks_run < dim.checks_applicable:
            out.append(Violation("CI-5", f"dimension:{name}",
                                 f"coverage 1.0 with {dim.checks_run}/{dim.checks_applicable} checks"))
        if dim.coverage > 1.0 or dim.coverage < 0.0:
            out.append(Violation("CI-9", f"dimension:{name}", f"coverage {dim.coverage}"))
        claim = by_id.get(f"dimension:{name}")
        if claim and claim.scope == COMPLETE and dim.checks_run < dim.checks_applicable:
            out.append(Violation("CI-1", claim.id, "complete scope with unrun checks"))

    if sc.coverage > 1.0 or sc.coverage < 0.0:
        out.append(Violation("CI-9", "coverage", f"coverage {sc.coverage}"))

    suppressed = [f for f in report.findings if f.suppressed]
    if suppressed:
        total_run = sum(d.checks_run for d in sc.dimensions.values())
        total_app = sum(d.checks_applicable for d in sc.dimensions.values())
        if total_app and total_run > total_app:
            out.append(Violation("CI-6", "coverage",
                                 "checks_run exceeds checks_applicable in the presence of suppressions"))

    recorded = {a for a in absts if a.startswith("check:")}
    for f in report.findings:
        if f.rule_id.endswith(".not-assessed"):
            key = f"check:{f.rule_id[:-len('.not-assessed')]}@{f.location.logical or f.location.path}"
            if key not in recorded:
                out.append(Violation("CI-8", f.id, f"not-assessed check missing from abstentions: {key}"))

    gate_claim = by_id.get("gate")
    if gate_claim and (report.gate or {}).get("passed") and absts and gate_claim.scope == COMPLETE:
        out.append(Violation("CI-10", "gate", "passing gate scoped complete despite abstentions"))

    # CI-11. The one that makes incremental scanning safe to ship: a scan that
    # read four files of a repository must not produce a single sentence a
    # reader could quote as being about the repository.
    if (report.scan_scope or {}).get("mode") == "partial":
        for c in claims:
            if c.kind == COVERAGE:
                continue  # a measurement ABOUT the incompleteness; see above
            if c.kind == GATE and not (report.gate or {}).get("passed"):
                # "a threshold was violated" is an existence claim. Finding one
                # defect proves it whatever else went unread, so completeness
                # is not part of what is being asserted.
                continue
            if c.scope == COMPLETE:
                out.append(Violation("CI-11", c.id,
                                     "partial scan asserted a complete-scope claim"))

    return out


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------

def wilson_lower_bound(successes: int, n: int, z: float = 1.96) -> float:
    """Lower bound of the Wilson score interval.

    Used so a rule with a handful of observations reports as unproven instead
    of as perfect. Ten for ten is not evidence of six nines; it is evidence of
    a rate above roughly 0.72.
    """
    if n <= 0:
        return 0.0
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def observations_needed(target_rate: float, confidence: float = 0.95) -> int:
    """Rule of three: observations required to claim an error rate below
    `target_rate` after seeing zero errors."""
    if target_rate <= 0:
        return 0
    return math.ceil(-math.log(1 - confidence) / target_rate)


def nines(lower_bound: float) -> str:
    """Report a bound as nines, never rounding up to a claim not supported."""
    if lower_bound >= 1.0:
        return "exhaustive"
    if lower_bound <= 0:
        return "unproven"
    failures = 1.0 - lower_bound
    if failures <= 0:
        return "exhaustive"
    n = -math.log10(failures)
    if n < 1:
        return f"{lower_bound * 100:.1f}%"
    return f"{lower_bound * 100:.{min(6, max(1, int(n) + 1))}f}%"
