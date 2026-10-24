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
to exactly one of five states, and a report says how many are in each (plus
NOT_APPLICABLE, for packs with per-language applicability; see below):

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

## Per-language applicability

A weakness list (the CWE Top 25) is not one question but one per language:
an out-of-bounds write cannot occur in Python, and a missing CSRF check in Go
is out of reach of a single-file check. A control may carry `languages:`, a
cell per language pack that applies, does not apply (with a reason) or is out
of reach (with a reason). An applying cell is assessed by that pack's rules
tagged with the control's `cwe` ids. Each cell resolves on its own, and the
control takes its worst cell; NOT_APPLICABLE only when every language in the
codebase is n/a. Language-pack rules that declare `controls:` get the same
per-language cells on the framework control they name.
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

# A sixth state, used only by packs with per-language applicability (the CWE
# Top 25): the weakness cannot occur in any language of this codebase
# (memory safety in a memory-safe runtime). Distinct from not_automatable,
# which says a person must assess it: here there is nothing to assess.
NOT_APPLICABLE = "not_applicable"

STATES = [VIOLATED, NOT_ASSESSED, NO_COVERAGE, SATISFIED, NOT_AUTOMATABLE, NOT_APPLICABLE]

STATE_MEANING = {
    SATISFIED: "a check ran, applied, and found nothing",
    VIOLATED: "a check fired",
    NOT_ASSESSED: "a check covers this but did not run — not a pass",
    NO_COVERAGE: "assessable in principle; Arbiter has no check for it",
    NOT_AUTOMATABLE: "no static analyzer can assess this; a person must",
    NOT_APPLICABLE: "cannot occur in any language of this codebase",
}

# Per-language applicability of a control (`languages:` in a pack).
APPLIES = "applies"
OUT_OF_REACH = "out_of_reach"   # applies, but beyond single-file checks
APPLICABILITY = (APPLIES, NOT_APPLICABLE, OUT_OF_REACH)

# The state of one language cell of a control. The control states, plus:
#   out_of_reach  the weakness applies in this language and no single-file
#                 check can establish it (an honest gap, not a pass)
#   absent        the language is not in this codebase
# A cell that applies and has no native check is `no_coverage`: the gap.
ABSENT = "absent"
CELL_STATES = [VIOLATED, NOT_ASSESSED, NO_COVERAGE, OUT_OF_REACH, SATISFIED,
               NOT_APPLICABLE, ABSENT]


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
    # The weakness ids whose `cwe:`-tagged language rules assess this control
    # (a CWE Top 25 entry and the children counted with it, CWE-94 <- CWE-95).
    cwe: list[str] = field(default_factory=list)
    # Per-language applicability, keyed by language pack (`python`, `go`...).
    # Each cell: status (applies | not_applicable | out_of_reach), reason,
    # measured (the docs/cwe-top25.md mark), checks (resolved at load time:
    # the pack's rules tagged with `cwe`, plus the control's satisfied_by,
    # plus any the cell names). Empty for a control without languages.
    languages: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "id": self.id, "title": self.title, "family": self.family,
            "automatable": self.automatable, "machine_scope": self.machine_scope,
            "residual": self.residual, "satisfied_by": list(self.satisfied_by),
        }
        if self.cwe:
            d["cwe"] = list(self.cwe)
        return d


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
        c = Control(
            id=str(raw.get("id", "")),
            title=raw.get("title", ""),
            family=raw.get("family", ""),
            automatable=raw.get("automatable", "none"),
            machine_scope=raw.get("machine_scope", ""),
            residual=raw.get("residual", ""),
            satisfied_by=list(raw.get("satisfied_by") or []),
            cwe=[str(x) for x in raw.get("cwe") or []],
        )
        if raw.get("languages"):
            _resolve_languages(c, raw["languages"], path.name)
        fw.controls.append(c)
    return fw


def _resolve_languages(c: Control, cells: dict, where: str) -> None:
    """Resolve a control's `languages:` cells to the checks that assess each.

    A language cell is assessed by that language pack's rules whose `cwe:`
    tags include one of the control's `cwe` ids, plus the control's own
    `satisfied_by` (language-independent checks, the secrets probe for
    CWE-798), plus any `checks:` the cell names. Deriving from the tags means
    a new rule tagged CWE-22 assesses the CWE-22 control the day it lands;
    tests/test_controls_cwe_top25.py holds the result to docs/cwe-top25.md.
    A not_applicable cell has no checks: there is nothing to look for.
    """
    if not isinstance(cells, dict):
        raise TypeError(f"{where} {c.id}: `languages` must be a mapping")
    wanted = set(c.cwe)
    rules = _lang_rules()
    shared = list(c.satisfied_by)
    union: list[str] = list(shared)
    for lang, raw in cells.items():
        raw = raw or {}
        status = raw.get("status", APPLIES)
        if status not in APPLICABILITY:
            raise ValueError(f"{where} {c.id} {lang}: status {status!r} is not one of "
                             f"{', '.join(APPLICABILITY)}")
        if status != APPLIES and not raw.get("reason"):
            raise ValueError(f"{where} {c.id} {lang}: a {status} cell must say why")
        checks: list[str] = []
        if status != NOT_APPLICABLE:
            checks = [f"arbiter/{lang}.{r['id']}" for r in rules.get(lang, [])
                      if wanted & set(r.get("cwe") or [])]
            checks += shared + [str(x) for x in raw.get("checks") or []]
            checks = list(dict.fromkeys(checks))
        c.languages[str(lang)] = {
            "status": status, "reason": raw.get("reason", ""),
            "measured": str(raw.get("measured", "")), "checks": checks,
        }
        union += checks
    c.satisfied_by = list(dict.fromkeys(union))


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

_NATIVE_PROBES = {
    "resource": "resource_policy", "secrets": "secrets",
    "supply": "supply_chain", "drift": "doc_drift",
    "quality": "quality", "ast": "ast_metrics",
    "interface": "interface",
}

# `house/<id>` is a house rule from the target's arbiter.yaml (`rules:`). Two
# probes emit them: `house_rules_ast` the `ast_query` rules, `house_rules`
# every other type. The prefix used to fall through to "house", which is not
# a probe, so a control mapped to a house rule always read as not assessed
# (FAIL-055).
HOUSE_PREFIX = "house/"
_HOUSE_PROBES = ("house_rules", "house_rules_ast")


def _lang_rules() -> dict[str, list[dict]]:
    """The shipped language packs' rules, keyed by pack name."""
    from . import lang_rules
    try:
        return {p: list(d["rules"]) for p, d in lang_rules.load_packs().items()}
    except Exception:  # noqa: BLE001 - a broken pack fails the scan and the suite elsewhere
        return {}


def _pack_languages() -> dict[str, set[str]]:
    """Language pack -> the inventory languages it reads (typescript reads
    javascript too)."""
    from . import lang_rules
    try:
        return {p: set(d["languages"]) for p, d in lang_rules.load_packs().items()}
    except Exception:  # noqa: BLE001 - mapping only
        return {}


def _language_packs() -> set[str]:
    """Names of the shipped language rule packs (`python`, `go`, ...).

    Read from the packs themselves so a new pack needs no edit here. If the
    packs cannot be loaded, fall back to their file names: a broken pack is
    reported by the scan, and this mapping must not hide which probe it is.
    """
    from . import lang_rules
    try:
        return set(lang_rules.load_packs())
    except Exception:  # noqa: BLE001 - mapping only; a broken pack fails the scan elsewhere
        return {p.stem for p in lang_rules.AST_PACKS.glob("*.yaml")}


def house_rule_types(config: dict | None) -> dict[str, str] | None:
    """House rule id -> its `type`, from a configuration. None when no
    configuration is known (a report read on its own)."""
    if config is None:
        return None
    return {str(r.get("id", "unnamed")): str(r.get("type", ""))
            for r in config.get("rules") or [] if isinstance(r, dict)}


def _probe_of(check: str, house: dict[str, str] | None = None) -> str:
    """Which probe or adapter would produce this check.

    `arbiter/resource.foo` comes from a native probe; `checkov/CKV_AWS_1` comes
    from the checkov adapter; `arbiter/python.sql-injection` comes from the
    `lang_python` pack probe; `house/no-bare-except` from `house_rules`, or
    from `house_rules_ast` when the configured rule is an `ast_query`.
    Knowing this is what separates "the check ran and found nothing" from
    "the check never ran". Returning the bare pack name (`python`, `house`)
    made every such check read as not assessed, because no probe has that
    name (ARB-062 criterion 5, FAIL-053, FAIL-055).
    """
    if check.startswith(HOUSE_PREFIX):
        rtype = (house or {}).get(check[len(HOUSE_PREFIX):], "")
        return "house_rules_ast" if rtype == "ast_query" else "house_rules"
    if check.startswith("arbiter/"):
        rest = check.split("/", 1)[1]
        head = rest.split(".", 1)[0]
        if head in _NATIVE_PROBES:
            return _NATIVE_PROBES[head]
        if head in _language_packs():
            return f"lang_{head}"
        return head
    return check.split("/", 1)[0]


def _probes_of(check: str, house: dict[str, str] | None = None) -> tuple[str, ...]:
    """Every probe that must have run for `check` to count as assessed.

    One, except a house rule whose type is unknown because the report is read
    without its configuration (`arbiter controls` on a saved report): then
    both house probes. An `ast_query` rule needs tree-sitter and the others do
    not, so reading it as assessed because `house_rules` ran would be a false
    pass when `house_rules_ast` could not.
    """
    if check.startswith(HOUSE_PREFIX) and check[len(HOUSE_PREFIX):] not in (house or {}):
        return _HOUSE_PROBES
    return (_probe_of(check, house),)


def declared_checks() -> dict[str, list[str]]:
    """`FRAMEWORK:CONTROL` -> the language-pack rules that declare it.

    A language rule may say which controls it supports (`controls:
    [NIST-800-218:PW.5]`). Each such rule is a check for that control, beside
    the pack's own `satisfied_by`, so the control is assessed by the language
    packs that ran and violated by the rule that fired.
    """
    out: dict[str, list[str]] = {}
    for pack, rules in _lang_rules().items():
        for r in rules:
            for ref in r.get("controls") or []:
                out.setdefault(str(ref), []).append(f"arbiter/{pack}.{r['id']}")
    return out


def unresolved_declarations(frameworks: list[Framework] | None = None) -> list[str]:
    """Declared `FRAMEWORK:CONTROL` refs no loaded pack enumerates. They assess
    nothing until a pack lists the control."""
    known = {f"{fw.id}:{c.id}" for fw in (frameworks or load_frameworks()) for c in fw.controls}
    return sorted(ref for ref in declared_checks() if ref not in known)


@dataclass
class _Run:
    ran: set[str]
    inapplicable: set[str]
    not_run: dict[str, str]
    fired: dict[str, list[Finding]]
    unknown: set[str]
    house: dict[str, str] | None

    def status(self, check: str) -> tuple[str, str]:
        """('ran' | 'inapplicable' | 'blocked', reason) for one check."""
        if check in self.unknown:
            return "blocked", f"{check}: value undetermined until apply"
        if (self.house is not None and check.startswith(HOUSE_PREFIX)
                and check[len(HOUSE_PREFIX):] not in self.house):
            return "blocked", f"{check}: no house rule with that id in the configuration"
        live = [p for p in _probes_of(check, self.house) if p not in self.inapplicable]
        if not live:
            return "inapplicable", ""
        for p in live:
            if p not in self.ran:
                return "blocked", f"{check}: {self.not_run.get(p, 'probe did not run')}"
        return "ran", ""


def _run_of(findings: list[Finding], outcomes: list[ProbeOutcome],
            house: dict[str, str] | None) -> _Run:
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
    return _Run(ran, inapplicable, not_run, fired, unknown, house)


def _belongs(f: Finding, lang: str, pack_langs: dict[str, set[str]]) -> bool:
    """Does this finding count against the `lang` cell of a control?

    A language-pack finding counts for its own pack only. Any other finding
    (the secrets probe, for CWE-798) counts for the language of the file it is
    in, and for every language when that file is in none of them (a
    credential in a `.env` file is the repository's, not one language's).
    """
    if f.rule_id.startswith("arbiter/"):
        head = f.rule_id.split("/", 1)[1].split(".", 1)[0]
        if head in pack_langs:
            return head == lang
    from .inventory import LANG_BY_EXT
    path = f.location.path or ""
    ext = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    flang = LANG_BY_EXT.get(ext, LANG_BY_EXT.get(ext.lower(), ""))
    owners = {p for p, langs in pack_langs.items() if flang in langs}
    return lang in owners or not owners


def _cell(lang: str, spec: dict, run: _Run, pack_langs: dict[str, set[str]]) -> dict:
    """Resolve one language cell of a control to one of CELL_STATES."""
    status, checks = spec.get("status", APPLIES), list(spec.get("checks") or [])
    out = {"status": status, "measured": spec.get("measured", ""),
           "checks": checks, "reason": spec.get("reason", "")}
    if status == NOT_APPLICABLE:
        return {**out, "state": NOT_APPLICABLE}
    if f"lang_{lang}" in run.inapplicable:
        return {**out, "state": ABSENT, "reason": f"no {lang} in this codebase"}
    hits = [f for ch in checks for f in run.fired.get(ch, []) if _belongs(f, lang, pack_langs)]
    if hits:
        return {**out, "state": VIOLATED, "evidence": [f.id for f in hits[:20]],
                "finding_count": len(hits),
                "reason": f"{len(hits)} finding(s) from {len({f.rule_id for f in hits})} check(s)"}
    if status == OUT_OF_REACH:
        # A partial signal (go.stub-on-security-path for CWE-863) can show the
        # weakness is present. Its silence shows nothing.
        return {**out, "state": OUT_OF_REACH}
    if not checks:
        mark = f" (docs/cwe-top25.md: {spec['measured']})" if spec.get("measured") else ""
        return {**out, "state": NO_COVERAGE,
                "reason": f"no native check covers this in {lang}{mark}"}
    statuses = [(ch, *run.status(ch)) for ch in checks]
    blocked = [why for _, st, why in statuses if st == "blocked"]
    applicable = [ch for ch, st, _ in statuses if st != "inapplicable"]
    if not applicable:
        return {**out, "state": NO_COVERAGE,
                "reason": "every check covering this is inapplicable to this codebase"}
    if blocked:
        return {**out, "state": NOT_ASSESSED, "reason": "; ".join(blocked[:3])}
    return {**out, "state": SATISFIED,
            "reason": f"{len(applicable)} applicable check(s) ran and found nothing"}


def _by_language(cells: dict[str, dict]) -> dict[str, list[str]]:
    by: dict[str, list[str]] = {}
    for lang, cell in cells.items():
        by.setdefault(cell["state"], []).append(lang)
    return by


def _language_row(c: Control, run: _Run, pack_langs: dict[str, set[str]]) -> dict:
    """A control with per-language applicability: each cell, then the control.

    The control takes its worst cell: violated, then not assessed, then a gap
    (no native check, or out of reach), then satisfied. Not applicable only
    when every language of this codebase is n/a for it.
    """
    cells = {lang: _cell(lang, spec, run, pack_langs) for lang, spec in c.languages.items()}
    by = _by_language(cells)
    row = {**c.to_dict(), "languages": cells, "evidence": []}

    def langs(state: str) -> str:
        return ", ".join(sorted(by.get(state, [])))

    if VIOLATED in by:
        hits = [e for cell in cells.values() for e in cell.get("evidence", [])]
        n = sum(cell.get("finding_count", 0) for cell in cells.values())
        return {**row, "state": VIOLATED, "evidence": hits[:20], "finding_count": n,
                "reason": f"{n} finding(s) in {langs(VIOLATED)}"}
    if NOT_ASSESSED in by:
        why = [f"{lang}: {cells[lang]['reason']}" for lang in sorted(by[NOT_ASSESSED])]
        return {**row, "state": NOT_ASSESSED, "reason": "; ".join(why[:3])}
    if NO_COVERAGE in by or OUT_OF_REACH in by:
        parts = []
        if NO_COVERAGE in by:
            parts.append(f"no native check in {langs(NO_COVERAGE)}")
        if OUT_OF_REACH in by:
            parts.append(f"out of reach of single-file checks in {langs(OUT_OF_REACH)}")
        if SATISFIED in by:
            parts.append(f"assessed in {langs(SATISFIED)}")
        return {**row, "state": NO_COVERAGE, "reason": "; ".join(parts)}
    if SATISFIED in by:
        extra = f"; does not apply in {langs(NOT_APPLICABLE)}" if NOT_APPLICABLE in by else ""
        return {**row, "state": SATISFIED,
                "reason": f"assessed in {langs(SATISFIED)}, nothing found{extra}"}
    if NOT_APPLICABLE in by:
        first = min(by[NOT_APPLICABLE])
        return {**row, "state": NOT_APPLICABLE,
                "reason": f"does not apply in {langs(NOT_APPLICABLE)}: {cells[first]['reason']}"}
    return {**row, "state": NO_COVERAGE,
            "reason": "no language this control covers is in this codebase"}


def evaluate(
    framework: Framework,
    findings: list[Finding],
    outcomes: list[ProbeOutcome],
    house: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve every control in the pack to one of the states.

    `house` maps the target's house rule ids to their types
    (`house_rule_types(config)`); without it a `house/` check needs both house
    probes to have run.
    """
    run = _run_of(findings, outcomes, house)
    declared = declared_checks()
    pack_langs = _pack_languages()

    rows = []
    for c in framework.controls:
        if c.languages:
            rows.append(_language_row(c, run, pack_langs))
            continue

        decl = [ch for ch in declared.get(f"{framework.id}:{c.id}", [])
                if ch not in c.satisfied_by]
        checks = list(c.satisfied_by) + decl
        extra: dict[str, Any] = {"declared_by": decl} if decl else {}
        if c.automatable == "none" or not checks:
            state = NOT_AUTOMATABLE if c.automatable == "none" else NO_COVERAGE
            reason = ("no static analyzer can establish this" if state == NOT_AUTOMATABLE
                      else "no check in this build covers it")
            if decl:
                # Kept visible rather than silently dropped: the pack's
                # judgement stands, and a reader can see the rules disagree.
                reason += (f"; {len(decl)} language rule(s) declare this control, "
                           "which this pack marks not automatable")
            rows.append({**c.to_dict(), **extra, "state": state, "evidence": [],
                         "reason": reason})
            continue
        if decl:
            # Which language packs assess this control, and how each fared.
            per: dict[str, list[str]] = {}
            for ch in decl:
                per.setdefault(ch.split("/", 1)[1].split(".", 1)[0], []).append(ch)
            extra["languages"] = {lang: _cell(lang, {"status": APPLIES, "checks": chs},
                                              run, pack_langs)
                                  for lang, chs in sorted(per.items())}

        hits = [f for ch in checks for f in run.fired.get(ch, [])]
        if hits:
            rows.append({**c.to_dict(), **extra, "state": VIOLATED,
                         "evidence": [f.id for f in hits[:20]],
                         "finding_count": len(hits),
                         "reason": f"{len(hits)} finding(s) from "
                                   f"{len({f.rule_id for f in hits})} check(s)"})
            continue

        # Nothing fired. That is only a pass if the checks actually ran.
        statuses = [(ch, *run.status(ch)) for ch in checks]
        blocked = [why for _, st, why in statuses if st == "blocked"]
        # Every covering check was inapplicable here: nothing was assessed, but
        # nothing was prevented either. That is no coverage for this target.
        applicable_checks = [ch for ch, st, _ in statuses if st != "inapplicable"]
        if not applicable_checks:
            rows.append({**c.to_dict(), **extra, "state": NO_COVERAGE, "evidence": [],
                         "reason": "every check covering this is inapplicable to "
                                   "this codebase"})
            continue
        if blocked:
            rows.append({**c.to_dict(), **extra, "state": NOT_ASSESSED, "evidence": [],
                         "reason": "; ".join(blocked[:3])})
        else:
            rows.append({**c.to_dict(), **extra, "state": SATISFIED, "evidence": [],
                         "reason": f"{len(applicable_checks)} applicable check(s) "
                                   "ran and found nothing"})

    counts = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}
    # The denominator is the framework's real size, not the pack's.
    total = framework.declared_controls or framework.enumerated
    not_enumerated = max(0, total - framework.enumerated)
    # Only controls with evidence count as assessed. This is the number a
    # reader should look at first, and it is deliberately unflattering.
    with_evidence = counts[SATISFIED] + counts[VIOLATED]
    out = {
        "framework": framework.to_dict(),
        "counts": counts,
        "not_enumerated": not_enumerated,
        "declared_total": total,
        "assessed_fraction": round(with_evidence / total, 4) if total else 0.0,
        "controls": rows,
    }
    per_language = {c.id for c in framework.controls if c.languages}
    if per_language:
        out["languages"] = language_summary([r for r in rows if r["id"] in per_language])
    return out


def language_summary(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Per language, how many controls are in each cell state."""
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        for lang, cell in (r.get("languages") or {}).items():
            counts = out.setdefault(lang, {s: 0 for s in CELL_STATES})
            counts[cell["state"]] += 1
    return dict(sorted(out.items()))


def evaluate_all(
    findings: list[Finding],
    outcomes: list[ProbeOutcome],
    only: list[str] | None = None,
    extra_dirs: list[str] | None = None,
    house: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    wanted = {x.lower() for x in (only or [])}
    out = []
    for fw in load_frameworks(extra_dirs):
        if wanted and fw.id.lower() not in wanted:
            continue
        out.append(evaluate(fw, findings, outcomes, house=house))
    return out
