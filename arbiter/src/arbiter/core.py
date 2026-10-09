"""Core data model for Arbiter.

Everything in the pipeline speaks Finding. Probes produce them, adapters
normalize into them, reporters render them, and the A/B harness matches on
their fingerprints.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = "1.0"

SEVERITIES = ["critical", "high", "medium", "low", "info"]
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}
SEV_WEIGHT = {"critical": 40.0, "high": 16.0, "medium": 5.0, "low": 1.5, "info": 0.0}
CONFIDENCE_FACTOR = {"high": 1.0, "medium": 0.6, "low": 0.3}

# "assurance" is its own dimension because it answers a different question from
# all the others. Every other dimension asks whether the CODE is sound.
# Assurance asks whether the CHECKING is: how much of this repository has been
# excluded from analysis, how many findings have been silenced, and whether the
# tests that are supposed to catch regressions actually assert anything.
#
# It has to be separate, because its findings are not defects and must not be
# scored as though they were. A repository with four hundred `# noqa` comments
# is not insecure; it is unmeasured, and a clean report from any other tool
# means correspondingly less. That is a fact about the evidence, in the same
# family as "not assessed" -- which is why it belongs to this tool.
DIMENSIONS = ["security", "compliance", "quality", "drift", "interface",
              "supply_chain", "assurance"]

# SARIF only has error/warning/note/none
SARIF_LEVEL = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}


def sev_at_least(sev: str, threshold: str) -> bool:
    """True when `sev` is as severe as `threshold` or worse."""
    if threshold not in SEV_RANK:
        return False
    return SEV_RANK.get(sev, 99) <= SEV_RANK[threshold]


_WS = re.compile(r"\s+")


def normalize_snippet(text: str) -> str:
    """Collapse whitespace and case so cosmetic edits don't churn fingerprints.

    Digits and punctuation are deliberately preserved: a changed port number or
    CIDR is a different finding, not the same one moved.
    """
    return _WS.sub(" ", (text or "").strip()).lower()


@dataclass
class Location:
    path: str = ""
    start_line: int = 0
    end_line: int = 0
    start_col: int = 0
    end_col: int = 0
    logical: str = ""  # resource address, symbol, or logical ID
    repo_id: str = ""  # set on cross-repo findings so related sites stay attributable

    def __post_init__(self) -> None:
        # Separator normalization belongs here rather than at each producer.
        # `fingerprint()` hashes this path, adjudications are keyed by that
        # fingerprint, and `record()` refuses to re-adjudicate -- so a path
        # that arrives as `a\b\c.tf` on Windows and `a/b/c.tf` elsewhere
        # silently splits one finding's identity in two, and a verdict
        # recorded on one platform can never match the same finding on the
        # other. There are ~54 Location construction sites and normalizing at
        # each of them is what was tried before: it was missed at one site in
        # REQ-014, at every probe in REQ-015, at the construction sites again
        # in REQ-016, and once more in the adapters in REQ-029. One boundary
        # every site passes through is the only version that stays fixed.
        if self.path:
            self.path = self.path.replace("\\", "/")

    def to_dict(self) -> dict:
        return asdict(self)

    def short(self) -> str:
        prefix = f"{self.repo_id}:" if self.repo_id else ""
        if not self.path:
            return prefix + (self.logical or "-")
        if self.start_line:
            return f"{prefix}{self.path}:{self.start_line}"
        return prefix + self.path


@dataclass
class Finding:
    rule_id: str
    title: str
    dimension: str = "quality"
    severity: str = "medium"
    confidence: str = "high"
    provenance: str = "deterministic"  # deterministic | inferred | hybrid
    repo_id: str = "root"
    location: Location = field(default_factory=Location)
    description: str = ""
    remediation: str = ""
    # Where `remediation` came from: "table" (curated, hand-written -- can go
    # stale), "field" (dug straight from the tool's own output this run --
    # self-refreshing), "link" (a URL wrapped as "see guidance"), "default"
    # (nothing tool- or curator-specific was available). Empty for findings
    # that didn't go through Adapter.to_findings() at all. Lets a report
    # surface curated-table coverage instead of it silently going stale.
    remediation_source: str = ""
    scope_note: str = ""  # a repo's own docs claim this location is covered elsewhere -- unverified, never changes severity/status
    evidence: str = ""
    probe: str = ""
    probe_version: str = ""
    controls: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    related: list[Location] = field(default_factory=list)  # interface findings cite both sides
    status: str = "new"  # new | existing | fixed
    suppressed: bool = False
    suppression_reason: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = self.fingerprint()

    def fingerprint(self) -> str:
        """Stable identity that survives line shifts.

        Deliberately excludes line numbers. Reformatting a file must not
        invalidate a baseline, or the 'new findings only' gate becomes noise
        and people switch it off.
        """
        parts = [
            self.rule_id,
            self.repo_id,
            self.location.path,
            self.location.logical,
            normalize_snippet(self.evidence)[:200],
        ]
        digest = hashlib.sha256("␟".join(parts).encode("utf-8")).hexdigest()
        return "f:" + digest[:12]

    def weight(self) -> float:
        return SEV_WEIGHT.get(self.severity, 1.0) * CONFIDENCE_FACTOR.get(self.confidence, 1.0)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["location"] = self.location.to_dict()
        d["related"] = [r.to_dict() if isinstance(r, Location) else r for r in self.related]
        return d

    @staticmethod
    def from_dict(d: dict) -> "Finding":
        loc = d.get("location") or {}
        related = [Location(**r) for r in (d.get("related") or [])]
        known = {
            k: v
            for k, v in d.items()
            if k not in ("location", "related") and k in Finding.__dataclass_fields__
        }
        known["location"] = Location(**{k: v for k, v in loc.items() if k in Location.__dataclass_fields__})
        known["related"] = related
        return Finding(**known)


@dataclass
class ProbeOutcome:
    """Why a probe did or did not contribute. Absence of findings is only a
    pass when status == 'ran'."""

    name: str
    status: str = "ran"  # ran | skipped | error
    reason: str = ""
    version: str = ""
    duration_s: float = 0.0
    finding_count: int = 0
    dimensions: list[str] = field(default_factory=list)
    checks: int = 1  # declared rule classes, used for coverage accounting
    # False when the probe could never have applied here — no Python in the
    # tree, a single repo so there are no seams. This is a different fact from
    # "the probe was prevented from running", and conflating them makes a
    # control look unassessed forever in a codebase the check has no business
    # touching. Coverage accounting is unaffected; the control matrix reads it.
    applicable: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RepoInfo:
    id: str = "root"
    path: str = ""
    source: str = ""
    role: str = ""
    commit: str = ""
    files: int = 0
    loc: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DimensionScore:
    dimension: str
    score: float = 100.0
    coverage: float = 0.0
    findings: int = 0
    checks_run: int = 0
    checks_applicable: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Scorecard:
    dimensions: dict[str, DimensionScore] = field(default_factory=dict)
    overall: float | None = None
    coverage: float = 0.0
    withheld: bool = False
    withheld_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "dimensions": {k: v.to_dict() for k, v in self.dimensions.items()},
            "overall": self.overall,
            "coverage": self.coverage,
            "withheld": self.withheld,
            "withheld_reason": self.withheld_reason,
        }


@dataclass
class Report:
    system: str = "default"
    schema_version: str = SCHEMA_VERSION
    arbiter_version: str = "0.1.0"
    profile: str = "offline"
    started_at: str = ""
    duration_s: float = 0.0
    repos: list[RepoInfo] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    probes: list[ProbeOutcome] = field(default_factory=list)
    scorecard: Scorecard = field(default_factory=Scorecard)
    stacks: list[str] = field(default_factory=list)
    # Lines of code broken out by language, and by file role. Without this, the
    # only available denominator is whole-repo size, which makes a rule that
    # applies to Kubernetes manifests look spotless in a 400,000-line Go
    # repository that happens to contain forty of them. A rate is only
    # meaningful against the code the rule could have fired on.
    loc_by_language: dict[str, int] = field(default_factory=dict)
    loc_by_role: dict[str, int] = field(default_factory=dict)
    # Whether this scan read the whole repository or only part of it, and on
    # what basis. {"mode": "full"} or {"mode": "partial", ...}. A partial scan
    # adds an abstention naming the files it did not read, so no claim built
    # on it can be scoped complete. See incremental.py.
    scan_scope: dict = field(default_factory=lambda: {"mode": "full"})
    # Per-framework control coverage: how many controls carry evidence from
    # this scan and how many do not. Summary only -- `arbiter controls` prints
    # the per-control detail. It lives in every report because a compliance
    # figure quoted without its denominator is the thing this tool exists to
    # stop doing.
    controls: list[dict] = field(default_factory=list)
    gate: dict = field(default_factory=dict)
    # Every assertion this report makes, with the basis it rests on, plus the
    # result of checking them. See claims.py.
    claims: list[dict] = field(default_factory=list)
    integrity: dict = field(default_factory=dict)
    # Which knowledge version this run used, and what it changed. Determinism
    # is (commit, config, knowledge_version) -> identical bytes.
    learning: dict = field(default_factory=dict)

    def active(self) -> list[Finding]:
        return [f for f in self.findings if not f.suppressed]

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "arbiter_version": self.arbiter_version,
            "system": self.system,
            "profile": self.profile,
            "started_at": self.started_at,
            "duration_s": round(self.duration_s, 3),
            "stacks": self.stacks,
            "scan_scope": self.scan_scope,
            "loc_by_language": self.loc_by_language,
            "loc_by_role": self.loc_by_role,
            "controls": self.controls,
            "repos": [r.to_dict() for r in self.repos],
            "probes": [p.to_dict() for p in self.probes],
            "scorecard": self.scorecard.to_dict(),
            "gate": self.gate,
            "claims": self.claims,
            "integrity": self.integrity,
            "learning": self.learning,
            "findings": [f.to_dict() for f in self.findings],
        }

    @staticmethod
    def from_dict(d: dict) -> "Report":
        r = Report()
        r.schema_version = d.get("schema_version", SCHEMA_VERSION)
        r.arbiter_version = d.get("arbiter_version", "")
        r.system = d.get("system", "default")
        r.profile = d.get("profile", "")
        r.started_at = d.get("started_at", "")
        r.duration_s = d.get("duration_s", 0.0)
        r.stacks = d.get("stacks", [])
        r.scan_scope = d.get("scan_scope", {"mode": "full"})
        r.repos = [RepoInfo(**x) for x in d.get("repos", [])]
        r.probes = [ProbeOutcome(**x) for x in d.get("probes", [])]
        r.findings = [Finding.from_dict(x) for x in d.get("findings", [])]
        r.gate = d.get("gate", {})
        r.claims = d.get("claims", [])
        r.integrity = d.get("integrity", {})
        r.learning = d.get("learning", {})
        sc = d.get("scorecard") or {}
        card = Scorecard(
            overall=sc.get("overall"),
            coverage=sc.get("coverage", 0.0),
            withheld=sc.get("withheld", False),
            withheld_reason=sc.get("withheld_reason", ""),
        )
        for k, v in (sc.get("dimensions") or {}).items():
            card.dimensions[k] = DimensionScore(**v)
        r.scorecard = card
        return r
