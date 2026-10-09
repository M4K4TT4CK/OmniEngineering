"""Learning, without breaking reproducibility.

An evaluator that adapts and an evaluator that is reproducible are in direct
tension: if a scan's behaviour depends on history, the same commit can pass on
Monday and fail on Tuesday, and a baseline, a CI gate and an accreditation
artefact all stop meaning anything.

The resolution is to separate the two:

  * LEARNING is offline. Feedback, measured precision and per-repository
    distributions accumulate into a knowledge file.
  * EXECUTION is deterministic. A scan reads one pinned knowledge version and
    records its hash in the report. Two runs against the same commit and the
    same knowledge version produce identical bytes.

So the tool adapts between runs, deliberately and visibly, rather than
drifting underneath you. Three further limits keep the adaptation honest:

  1. Learning adjusts CONFIDENCE, never SEVERITY. How sure we are is
     measurable from feedback; how much a defect matters is a judgement that
     belongs to the policy file.
  2. Learning never changes a gate outcome unless `gate.use_calibration` is
     explicitly set.
  3. A rule with too few observations reports as unproven rather than
     inheriting a flattering estimate from a handful of samples.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .claims import wilson_lower_bound
from .core import Finding

KNOWLEDGE_VERSION = 2
DEFAULT_PATH = ".arbiter/knowledge.json"

# Where a verdict came in. Recorded so a batch can be found again later; it
# says nothing about whether a person typed it.
ENTRY_POINTS = ("feedback", "feedback-batch", "review-interactive",
                "review-apply", "import")

# The entry points that reached the ledger without a terminal on the other end.
# Not a lesser class of verdict -- a batch import is a legitimate thing to do --
# but the one fact worth being able to filter on later, because it is the set
# that could have been produced by something that was not a person.
NON_INTERACTIVE_ENTRY_POINTS = ("feedback-batch", "review-apply", "import")

# What a pre-attribution verdict migrates to. Schema 1 stored a bare string,
# so these three facts are not recoverable for anything already in a ledger.
UNATTRIBUTED = "unattributed"

# Below this many adjudicated observations a rule's measured precision is not
# evidence, and the declared confidence stands.
MIN_OBSERVATIONS = 20


@dataclass
class RuleStats:
    """Evidence about one rule, kept in two separate ledgers.

    Adjudicated observations come from a human looking at a real finding.
    Synthetic observations come from generated defects and controls: cheap,
    plentiful, and drawn from a distribution we chose rather than one the
    world produced. Mixing them would let a million generated samples drown
    out ten real ones, so they are counted apart and reported apart.
    """

    rule_id: str
    true_positives: int = 0          # adjudicated by a person
    false_positives: int = 0
    synthetic_detected: int = 0      # planted defect, correctly found
    synthetic_missed: int = 0        # planted defect, not found  -> recall
    synthetic_clean_pass: int = 0    # control with no defect, correctly silent
    synthetic_false_alarm: int = 0   # control with no defect, fired anyway
    first_seen: str = ""
    last_seen: str = ""

    # -- adjudicated ------------------------------------------------------
    @property
    def observations(self) -> int:
        return self.true_positives + self.false_positives

    @property
    def precision(self) -> float | None:
        n = self.observations
        return self.true_positives / n if n else None

    @property
    def precision_lower_bound(self) -> float | None:
        n = self.observations
        return wilson_lower_bound(self.true_positives, n) if n else None

    @property
    def proven(self) -> bool:
        return self.observations >= MIN_OBSERVATIONS

    # -- synthetic --------------------------------------------------------
    @property
    def synthetic_positives(self) -> int:
        return self.synthetic_detected + self.synthetic_missed

    @property
    def synthetic_negatives(self) -> int:
        return self.synthetic_clean_pass + self.synthetic_false_alarm

    @property
    def recall(self) -> float | None:
        n = self.synthetic_positives
        return self.synthetic_detected / n if n else None

    @property
    def recall_lower_bound(self) -> float | None:
        n = self.synthetic_positives
        return wilson_lower_bound(self.synthetic_detected, n) if n else None

    @property
    def specificity(self) -> float | None:
        n = self.synthetic_negatives
        return self.synthetic_clean_pass / n if n else None

    @property
    def specificity_lower_bound(self) -> float | None:
        n = self.synthetic_negatives
        return wilson_lower_bound(self.synthetic_clean_pass, n) if n else None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update({
            "observations": self.observations,
            "precision": self.precision,
            "precision_lower_bound": self.precision_lower_bound,
            "proven": self.proven,
            "synthetic_positives": self.synthetic_positives,
            "synthetic_negatives": self.synthetic_negatives,
            "recall": self.recall,
            "recall_lower_bound": self.recall_lower_bound,
            "specificity": self.specificity,
            "specificity_lower_bound": self.specificity_lower_bound,
        })
        return d


@dataclass
class Verdict:
    """One adjudication, and who is answerable for it.

    Schema 1 stored `"true_positive"` or `"false_positive:note"` and nothing
    else, so a wrong mark was permanent AND anonymous — there was no way to
    find whose marks to distrust. This records who, when and through which
    door. It does not record whether a person typed it, because nothing here
    can establish that; `entry_point` narrows where to look, no further.
    """

    verdict: str
    reviewer: str
    at: str = ""
    entry_point: str = "feedback"
    note: str = ""

    def to_dict(self) -> dict:
        d = {"verdict": self.verdict, "reviewer": self.reviewer,
             "at": self.at, "entry_point": self.entry_point}
        if self.note:
            d["note"] = self.note
        return d

    @staticmethod
    def from_stored(raw: Any) -> "Verdict":
        """Read either schema. A schema 1 string carries no attribution, so it
        migrates to `unattributed` rather than guessing a name that would then
        look like evidence."""
        if isinstance(raw, dict):
            return Verdict(
                verdict=str(raw.get("verdict", "")),
                reviewer=str(raw.get("reviewer", UNATTRIBUTED)),
                at=str(raw.get("at", "")),
                entry_point=str(raw.get("entry_point", "import")),
                note=str(raw.get("note", "")),
            )
        verdict, _, note = str(raw).partition(":")
        return Verdict(verdict=verdict, reviewer=UNATTRIBUTED, at="",
                       entry_point="import", note=note)


@dataclass
class Knowledge:
    schema_version: int = KNOWLEDGE_VERSION
    updated: str = ""
    rules: dict[str, RuleStats] = field(default_factory=dict)
    # Per-repository metric distributions, used for adaptive thresholds.
    profiles: dict[str, dict] = field(default_factory=dict)
    # Fingerprints a human adjudicated, so the same finding is never re-asked.
    adjudicated: dict[str, Verdict] = field(default_factory=dict)
    # Severities for individual checks of EXTERNAL tools, measured against the
    # corpus by tools/calibrate_external.py.
    #
    # This does not breach the "learning never touches severity" rule above,
    # and the distinction is the whole point. A native rule's severity is a
    # deliberate policy statement and nothing here may move it. Checkov's open
    # build reports `"severity": null` on every finding, so Arbiter was
    # inventing `medium` for all 477 of them on a single repository. Replacing
    # an invented constant with a measured one is not drift.
    #
    # It is part of the version hash like everything else, so a scan still
    # records exactly which table it used and --pin-knowledge still fails a
    # run if the table moved underneath it.
    external_severity: dict[str, str] = field(default_factory=dict)

    # -- identity ---------------------------------------------------------
    def version_hash(self) -> str:
        """Content hash. A scan records this so a run is reproducible."""
        payload = json.dumps(self.to_dict(include_hash=False), sort_keys=True)
        return "k:" + hashlib.sha256(payload.encode()).hexdigest()[:12]

    def to_dict(self, include_hash: bool = True) -> dict:
        d = {
            "schema_version": self.schema_version,
            "updated": self.updated,
            "rules": {k: v.to_dict() for k, v in sorted(self.rules.items())},
            "profiles": self.profiles,
            "adjudicated": {k: v.to_dict() for k, v in sorted(self.adjudicated.items())},
            "external_severity": self.external_severity,
        }
        if include_hash:
            d["version"] = self.version_hash()
        return d

    @staticmethod
    def from_dict(d: dict) -> "Knowledge":
        # Always the current version: reading IS the migration, so the object
        # this returns is current-schema whatever the file said. Carrying the
        # old number forward would make a migrated ledger keep declaring a
        # layout it no longer has.
        k = Knowledge(
            schema_version=KNOWLEDGE_VERSION,
            updated=d.get("updated", ""),
            profiles=d.get("profiles") or {},
            adjudicated={k: Verdict.from_stored(v)
                         for k, v in (d.get("adjudicated") or {}).items()},
            external_severity=d.get("external_severity") or {},
        )
        for rule_id, raw in (d.get("rules") or {}).items():
            k.rules[rule_id] = RuleStats(
                rule_id=rule_id,
                true_positives=int(raw.get("true_positives", 0)),
                false_positives=int(raw.get("false_positives", 0)),
                synthetic_detected=int(raw.get("synthetic_detected", 0)),
                synthetic_missed=int(raw.get("synthetic_missed", 0)),
                synthetic_clean_pass=int(raw.get("synthetic_clean_pass", 0)),
                synthetic_false_alarm=int(raw.get("synthetic_false_alarm", 0)),
                first_seen=raw.get("first_seen", ""),
                last_seen=raw.get("last_seen", ""),
            )
        return k

    # -- persistence ------------------------------------------------------
    @staticmethod
    def load(path: str | None = None) -> "Knowledge":
        p = Path(path or DEFAULT_PATH)
        if not p.is_file():
            return Knowledge()
        try:
            return Knowledge.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 - a corrupt knowledge file starts fresh rather than blocking every scan
            return Knowledge()

    def save(self, path: str | None = None) -> str:
        p = Path(path or DEFAULT_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.updated = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        p.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return self.version_hash()

    def merge_external_severity(self, path: str) -> int:
        """Take measured severities from tools/calibrate_external.py's report.

        That tool writes a full measurement record per check -- hit counts,
        KLOC, ratio, basis -- and `apply()` needs only the one field. Without
        this step the two halves never meet: the nightly cycle wrote the
        report and copied it into .arbiter/, the scanner kept reading the map
        inside knowledge.json, and nothing moved a value from one to the
        other. The severities in the live ledger came from a one-off that no
        longer exists, so the measurement had been running for nothing.

        Checks the tool declined to grade carry `severity: null` (it needs
        MIN_OBSERVATIONS hits before it will claim anything). Those are
        skipped rather than stored as None, because absent means "no measured
        severity, keep the tool's own" to `apply()`, and a stored null would
        have to be special-cased everywhere it is read.
        """
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        merged = 0
        for rule_id, record in (report.get("checks") or {}).items():
            severity = record.get("severity")
            if severity:
                self.external_severity[rule_id] = severity
                merged += 1
        return merged


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def record(knowledge: Knowledge, finding: Finding, verdict: str, note: str = "",
           *, reviewer: str, entry_point: str = "feedback") -> bool:
    """Adjudicate one finding. Returns False if it was already adjudicated.

    Re-adjudicating the same fingerprint would let one disputed finding move
    the statistics as far as someone cares to click.

    `reviewer` is keyword-only and has no default, so a caller cannot record a
    verdict without saying who is answerable for it. That is attribution, not
    authentication: it makes a bad batch findable afterwards, and it is not
    evidence that a person rather than a script produced the mark.
    """
    if verdict not in ("true_positive", "false_positive"):
        raise ValueError("verdict must be true_positive or false_positive")
    if not (reviewer or "").strip():
        raise ValueError(
            "a verdict needs a reviewer: this ledger refuses re-adjudication, "
            "so an anonymous mark is permanent and untraceable")
    if finding.id in knowledge.adjudicated:
        return False
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    stats = knowledge.rules.setdefault(finding.rule_id, RuleStats(rule_id=finding.rule_id))
    if not stats.first_seen:
        stats.first_seen = now
    stats.last_seen = now
    if verdict == "true_positive":
        stats.true_positives += 1
    else:
        stats.false_positives += 1
    knowledge.adjudicated[finding.id] = Verdict(
        verdict=verdict, reviewer=reviewer.strip(), at=now,
        entry_point=entry_point, note=note)
    return True


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

CONFIDENCE_BANDS = ((0.90, "high"), (0.65, "medium"), (0.0, "low"))


def record_synthetic(knowledge: Knowledge, rule_id: str, kind: str, n: int = 1) -> None:
    """Record generated evidence. Kept out of the adjudicated ledger."""
    field_name = {
        "detected": "synthetic_detected",
        "missed": "synthetic_missed",
        "clean_pass": "synthetic_clean_pass",
        "false_alarm": "synthetic_false_alarm",
    }[kind]
    stats = knowledge.rules.setdefault(rule_id, RuleStats(rule_id=rule_id))
    setattr(stats, field_name, getattr(stats, field_name) + n)


def calibrated_confidence(stats: RuleStats) -> str | None:
    """Confidence derived from measured precision, or None when unproven.

    Deliberately reads the adjudicated ledger only. Synthetic evidence speaks
    to whether a rule can detect what it was written to detect; it says
    nothing about how often the things it flags in the wild are real.
    """
    if not stats.proven:
        return None
    lb = stats.precision_lower_bound or 0.0
    for floor, label in CONFIDENCE_BANDS:
        if lb >= floor:
            return label
    return "low"


def apply(findings: list[Finding], knowledge: Knowledge) -> dict:
    """Attach measured precision and recalibrate confidence.

    Severity is deliberately untouched: feedback measures how often a rule is
    right, not how much it matters when it is.
    """
    changed = 0
    annotated = 0
    graded = 0
    for f in findings:
        # External checks first. These arrive carrying a severity Arbiter's
        # adapter manifest invented, because the tool supplied none — every
        # Checkov finding is "medium" whether it is a public S3 bucket or a
        # missing transfer-acceleration setting. Where the corpus has measured
        # the check, that measurement replaces the invention.
        measured = knowledge.external_severity.get(f.rule_id)
        if measured and measured != f.severity:
            f.tags = list(f.tags) + [f"severity-was:{f.severity}", "severity:measured"]
            f.severity = measured
            graded += 1

        stats = knowledge.rules.get(f.rule_id)
        if stats is None or not stats.observations:
            continue
        annotated += 1
        f.tags = list(f.tags) + [
            f"precision:{stats.precision:.2f}",
            f"n={stats.observations}",
        ]
        new_conf = calibrated_confidence(stats)
        if new_conf and new_conf != f.confidence:
            f.tags.append(f"confidence-was:{f.confidence}")
            f.confidence = new_conf
            changed += 1
    return {"annotated": annotated, "recalibrated": changed,
            "externally_graded": graded}


# ---------------------------------------------------------------------------
# Adaptive thresholds
# ---------------------------------------------------------------------------

def build_profile(values: list[float]) -> dict:
    """Summarize a metric's distribution in this repository."""
    if not values:
        return {}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median": statistics.median(ordered),
        "p90": ordered[min(len(ordered) - 1, int(0.90 * len(ordered)))],
        "p95": ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))],
        "p99": ordered[min(len(ordered) - 1, int(0.99 * len(ordered)))],
        "max": ordered[-1],
    }


def adaptive_threshold(profile: dict, fixed: float, percentile: str = "p95",
                       floor: float | None = None) -> float:
    """A threshold that fits the repository it is judging.

    A fixed 120-line function limit is wrong in both directions: noise in a
    codebase whose median function is 80 lines, and useless in one whose median
    is 12. Where a profile exists, flag the tail of the repository's own
    distribution instead — but never below a floor, so a codebase that is
    uniformly bad cannot normalize its way to a clean report.
    """
    if not profile or profile.get("n", 0) < 30:
        return fixed
    value = float(profile.get(percentile, fixed))
    return max(value, floor if floor is not None else fixed * 0.5)


def metrics_from_inventory(inventory, graph=None) -> dict:
    """Collect the distributions adaptive thresholds are drawn from."""
    file_lines = [f.lines for f in inventory.text_files()
                  if f.role in ("source", "iac") and f.lines]
    out = {"file_lines": build_profile([float(x) for x in file_lines])}

    try:
        from . import ast as ts
        if ts.available():
            lengths: list[float] = []
            complexities: list[float] = []
            for f in inventory.text_files():
                if f.role not in ("source",) or not ts.ts_name(f.language):
                    continue
                for fn in ts.functions(f.abspath, f.language):
                    lengths.append(float(fn.lines))
                    complexities.append(float(fn.complexity))
            out["function_lines"] = build_profile(lengths)
            out["complexity"] = build_profile(complexities)
    except Exception:  # noqa: BLE001 - profiles are advisory; a parser failure leaves them absent
        pass
    return out


def resolve_quality_config(config: dict, profiles: dict) -> dict:
    """Return the quality thresholds a run should use, fixed or adaptive."""
    quality = dict(config.get("quality") or {})
    if not quality.get("adaptive"):
        return quality
    quality["max_file_lines"] = int(adaptive_threshold(
        profiles.get("file_lines") or {}, float(quality.get("max_file_lines", 800)), "p95", 200))
    quality["max_function_lines"] = int(adaptive_threshold(
        profiles.get("function_lines") or {}, float(quality.get("max_function_lines", 120)), "p95", 40))
    quality["max_complexity"] = int(adaptive_threshold(
        profiles.get("complexity") or {}, float(quality.get("max_complexity", 20)), "p95", 10))
    quality["_adaptive_source"] = "repository distribution (p95)"
    return quality
