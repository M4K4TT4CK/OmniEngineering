"""Control frameworks, and an honest account of what was actually assessed.

## The problem with every compliance report

A compliance scanner runs some checks, maps them to control identifiers, and
produces a page of green ticks. What it almost never tells you is the size of
what it did not look at. Two different silences get collapsed into the same
green:

  * the control was checked and passed, and
  * the control was never checked at all, by anything.

An accreditation package built on that is worse than no package, because a
reader cannot tell which controls carry evidence.

## The five states

Arbiter refuses to collapse them. Every control in a framework pack resolves
to exactly one of five states, and a report says how many are in each:

  SATISFIED      A check covering this control ran, applied to something, and
                 found nothing. Evidence exists.
  VIOLATED       A check covering this control fired. Evidence exists.
  NOT_ASSESSED   A check covers this control on paper, but it did not run
                 here -- the tool was not installed, the value was unknown
                 until apply, the stack was absent. NOT a pass.
  NO_COVERAGE    The control is technically assessable in principle, and
                 Arbiter has no check for it. This is the honest gap, and it
                 is the number that should be read first.
  NOT_AUTOMATABLE
                 No static analyzer can ever assess this control, because it
                 is about people, paperwork or physical space. Personnel
                 screening, incident-response exercises, visitor logs. Listing
                 these as failures is as dishonest as listing them as passes;
                 they belong to a human assessor and the pack says so.

The last two are the ones that make the report usable. A tool that reports
"87% compliant" without separating them is reporting its own coverage as if
it were your security posture.

## Independent packs, not crosswalks

Each framework pack maps its own controls directly to checks. Nothing is
routed through a hub framework. Crosswalking -- mapping 800-171 to 800-53 and
then 800-53 to checks -- is convenient and quietly lossy: the second hop
inherits the first hop's approximations, and a control ends up claiming
evidence two translations removed from anything that ran. The cost of
independence is duplicated mapping work. The benefit is that a claim about
CMMC is a claim about CMMC.

## Baseline totals

A pack states how many controls its framework actually has, separately from
how many the pack enumerates. Without that, a pack containing the twenty
controls Arbiter happens to cover would report 100% coverage. With it, the
report says twenty of three hundred and twenty-three, which is the truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .core import Finding, ProbeOutcome

PACKS = Path(__file__).parent / "packs" / "controls"

SATISFIED = "satisfied"
VIOLATED = "violated"
NOT_ASSESSED = "not_assessed"
NO_COVERAGE = "no_coverage"
NOT_AUTOMATABLE = "not_automatable"

STATES = [VIOLATED, NOT_ASSESSED, NO_COVERAGE, SATISFIED, NOT_AUTOMATABLE]

STATE_MEANING = {
    SATISFIED: "a check ran, applied, and found nothing",
    VIOLATED: "a check fired",
    NOT_ASSESSED: "a check covers this but did not run — not a pass",
    NO_COVERAGE: "assessable in principle; Arbiter has no check for it",
    NOT_AUTOMATABLE: "no static analyzer can assess this; a person must",
}


@dataclass
class Control:
    id: str
    title: str = ""
    family: str = ""
    # full | partial | none — how much of this control a machine can reach.
    # "partial" is the common and honest case: a scanner can see that storage
    # is encrypted, and cannot see whether the keys are managed properly.
    automatable: str = "none"
    machine_scope: str = ""     # what a check can actually establish
    residual: str = ""          # what a person must still assess, even at "full"
    satisfied_by: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "family": self.family,
            "automatable": self.automatable, "machine_scope": self.machine_scope,
            "residual": self.residual, "satisfied_by": list(self.satisfied_by),
        }


@dataclass
class Framework:
    id: str
    title: str = ""
    version: str = ""
    authority: str = ""
    baseline: str = ""
    # How many controls the framework has in the stated baseline, from the
    # published source. The denominator for every coverage claim.
    declared_controls: int = 0
    declared_source: str = ""
    notes: str = ""
    controls: list[Control] = field(default_factory=list)

    @property
    def enumerated(self) -> int:
        return len(self.controls)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "version": self.version,
            "authority": self.authority, "baseline": self.baseline,
            "declared_controls": self.declared_controls,
            "declared_source": self.declared_source,
            "enumerated_controls": self.enumerated, "notes": self.notes,
        }


def load_pack(path: Path) -> Framework:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    meta = doc.get("framework") or {}
    fw = Framework(
        id=meta.get("id", path.stem),
        title=meta.get("title", ""),
        version=str(meta.get("version", "")),
        authority=meta.get("authority", ""),
        baseline=meta.get("baseline", ""),
        declared_controls=int(meta.get("declared_controls", 0)),
        declared_source=meta.get("declared_source", ""),
        notes=meta.get("notes", ""),
    )
    for raw in doc.get("controls") or []:
        fw.controls.append(Control(
            id=str(raw.get("id", "")),
            title=raw.get("title", ""),
            family=raw.get("family", ""),
            automatable=raw.get("automatable", "none"),
            machine_scope=raw.get("machine_scope", ""),
            residual=raw.get("residual", ""),
            satisfied_by=list(raw.get("satisfied_by") or []),
        ))
    return fw


def load_frameworks(extra_dirs: list[str] | None = None) -> list[Framework]:
    out: list[Framework] = []
    for d in [PACKS] + [Path(x) for x in (extra_dirs or [])]:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.yaml")):
            try:
                out.append(load_pack(p))
            except Exception:  # noqa: BLE001 - one malformed pack must not take the other frameworks down
                continue
    return out


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _probe_of(check: str) -> str:
    """Which probe or adapter would produce this check.

    `arbiter/resource.foo` comes from a native probe; `checkov/CKV_AWS_1` comes
    from the checkov adapter. Knowing this is what separates "the check ran and
    found nothing" from "the check never ran".
    """
    if check.startswith("arbiter/"):
        rest = check.split("/", 1)[1]
        head = rest.split(".", 1)[0]
        return {
            "resource": "resource_policy", "secrets": "secrets",
            "supply": "supply_chain", "drift": "doc_drift",
            "quality": "quality", "ast": "ast_metrics",
            "interface": "interface", "house": "house_rules",
        }.get(head, head)
    return check.split("/", 1)[0]


def evaluate(
    framework: Framework,
    findings: list[Finding],
    outcomes: list[ProbeOutcome],
) -> dict[str, Any]:
    """Resolve every control in the pack to one of the five states."""
    ran = {o.name for o in outcomes if o.status == "ran"}
    # A probe that could never have applied here contributes nothing in either
    # direction. bandit not running against a Terraform-only repository is not
    # a gap in the assessment of "review human-readable code"; there is no
    # Python to review. It is only a gap when a probe that SHOULD have run was
    # prevented from running.
    inapplicable = {o.name for o in outcomes
                    if o.status != "ran" and not getattr(o, "applicable", True)}
    not_run = {o.name: (o.reason or o.status) for o in outcomes
               if o.status != "ran" and o.name not in inapplicable}

    fired: dict[str, list[Finding]] = {}
    unknown: set[str] = set()
    for f in findings:
        if f.suppressed:
            continue
        rid = f.rule_id
        # A `.not-assessed` finding is the engine saying a check could not
        # conclude — an unknown value in a plan, most often. It is evidence
        # that the control was NOT assessed, never that it passed.
        if rid.endswith(".not-assessed"):
            unknown.add(rid[: -len(".not-assessed")])
            continue
        fired.setdefault(rid, []).append(f)

    rows = []
    for c in framework.controls:
        checks = list(c.satisfied_by)
        if c.automatable == "none" or not checks:
            state = NOT_AUTOMATABLE if c.automatable == "none" else NO_COVERAGE
            rows.append({**c.to_dict(), "state": state, "evidence": [], "reason":
                         ("no static analyzer can establish this" if state == NOT_AUTOMATABLE
                          else "no check in this build covers it")})
            continue

        hits = [f for ch in checks for f in fired.get(ch, [])]
        if hits:
            rows.append({**c.to_dict(), "state": VIOLATED,
                         "evidence": [f.id for f in hits[:20]],
                         "finding_count": len(hits),
                         "reason": f"{len(hits)} finding(s) from "
                                   f"{len({f.rule_id for f in hits})} check(s)"})
            continue

        # Nothing fired. That is only a pass if the checks actually ran.
        blocked = []
        for ch in checks:
            if ch in unknown:
                blocked.append(f"{ch}: value undetermined until apply")
                continue
            probe = _probe_of(ch)
            if probe in inapplicable:
                continue
            if probe not in ran:
                blocked.append(f"{ch}: {not_run.get(probe, 'probe did not run')}")
        # Every covering check was inapplicable here: nothing was assessed, but
        # nothing was prevented either. That is no coverage for this target.
        applicable_checks = [c for c in checks if _probe_of(c) not in inapplicable]
        if not applicable_checks:
            rows.append({**c.to_dict(), "state": NO_COVERAGE, "evidence": [],
                         "reason": "every check covering this is inapplicable to "
                                   "this codebase"})
            continue
        if blocked:
            rows.append({**c.to_dict(), "state": NOT_ASSESSED, "evidence": [],
                         "reason": "; ".join(blocked[:3])})
        else:
            rows.append({**c.to_dict(), "state": SATISFIED, "evidence": [],
                         "reason": f"{len(applicable_checks)} applicable check(s) "
                                   "ran and found nothing"})

    counts = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}
    # The denominator is the framework's real size, not the pack's.
    total = framework.declared_controls or framework.enumerated
    not_enumerated = max(0, total - framework.enumerated)
    # Only controls with evidence count as assessed. This is the number a
    # reader should look at first, and it is deliberately unflattering.
    with_evidence = counts[SATISFIED] + counts[VIOLATED]
    return {
        "framework": framework.to_dict(),
        "counts": counts,
        "not_enumerated": not_enumerated,
        "declared_total": total,
        "assessed_fraction": round(with_evidence / total, 4) if total else 0.0,
        "controls": rows,
    }


def evaluate_all(
    findings: list[Finding],
    outcomes: list[ProbeOutcome],
    only: list[str] | None = None,
    extra_dirs: list[str] | None = None,
) -> list[dict[str, Any]]:
    wanted = {x.lower() for x in (only or [])}
    out = []
    for fw in load_frameworks(extra_dirs):
        if wanted and fw.id.lower() not in wanted:
            continue
        out.append(evaluate(fw, findings, outcomes))
    return out
