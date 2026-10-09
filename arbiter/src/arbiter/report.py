"""Reporters.

JSON is canonical and is written first; every other format is a rendering of
the same object, so a stale HTML report next to a fresh JSON one is not a
thing that can happen.
"""
from __future__ import annotations

import html
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from .core import SARIF_LEVEL, Finding, Report, sev_at_least

SEV_ORDER = ["critical", "high", "medium", "low", "info"]

NO_REMEDIATION = "No fix recorded for this finding."

# Past this many locations, a grouped row lists the rest as a count instead
# of one more line each — a rule that fires in 40 files should not push the
# table off the screen.
GROUP_LOCATION_CAP = 8


def _remediation_text(f: Finding) -> str:
    return f.remediation or NO_REMEDIATION


def _grouped(findings: list[Finding], key) -> list[list[Finding]]:
    """Cluster findings that share `key(f)`, preserving first-seen order.

    Same rule firing across many files renders as one row/entry with all its
    locations, instead of one line per occurrence drowning the report.
    """
    groups: dict[object, list[Finding]] = defaultdict(list)
    order: list[object] = []
    for f in findings:
        k = key(f)
        if k not in groups:
            order.append(k)
        groups[k].append(f)
    return [groups[k] for k in order]

# One line each, kept short enough to sit in a legend or a table cell.
# Grounded in what each dimension's probes actually check (docs/probes.md),
# not a general description of the category name.
DIMENSION_DESC = {
    "security": "exploitable weaknesses — secrets, disabled TLS checks, open or public cloud resources",
    "quality": "maintainability — oversized files/functions, complexity, missing tests",
    "supply_chain": "dependency and pipeline hygiene — unpinned packages, mutable Action refs",
    "interface": "cross-repo seams — endpoints and contracts that don't match on both sides",
    "compliance": "provider-neutral infrastructure rules mapped to control frameworks (NIST, FedRAMP, CMMC)",
    "drift": "docs and contracts that no longer match the code they describe",
    "assurance": "suppressions, exclusions and no-op tests found while checking — weight 0, never moves the grade",
}

ANSI = {
    "critical": "\033[1;31m", "high": "\033[31m", "medium": "\033[33m",
    "low": "\033[36m", "info": "\033[2m", "reset": "\033[0m",
    "bold": "\033[1m", "dim": "\033[2m", "green": "\033[32m",
}


def _color(enabled: bool, key: str, text: str) -> str:
    if not enabled:
        return text
    return f"{ANSI.get(key, '')}{text}{ANSI['reset']}"


def counts_by_severity(findings: list[Finding]) -> dict[str, int]:
    out = {s: 0 for s in SEV_ORDER}
    for f in findings:
        if f.severity in out:
            out[f.severity] += 1
    return out


REQ_TAG = "req:"
UNATTRIBUTED = "unattributed"
# How the `req:` tags were chosen, from incremental.attribute_requirements
# (ARB-052): spelled out under the table so a reader never mistakes the
# round's open requirements for the one that introduced a finding.
REQ_SCOPE_COMMITS = "req-scope:commits"
REQ_SCOPE_OPEN = "req-scope:open"


def requirement_rows(findings: list[Finding]) -> list[tuple[str, dict[str, int], list[str]]]:
    """Findings grouped by the requirement their commits cited (REQ-038).

    One row per `req:<ID>` tag, counts by severity and the first three titles.
    Findings in the change that carry no requirement tag land in an
    `unattributed` row; findings tagged outside-this-change are not in the
    change and are left out. Empty unless some finding carries a tag, so a
    report with no attribution to make prints nothing. Titles only -- a
    summary table is read by people who may not be cleared for the evidence.
    """
    if not any(t.startswith(REQ_TAG) for f in findings for t in f.tags):
        return []
    groups: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        ids = [t[len(REQ_TAG):] for t in f.tags if t.startswith(REQ_TAG)]
        if ids:
            for rid in ids:
                groups[rid].append(f)
        elif "outside-this-change" not in f.tags:
            groups[UNATTRIBUTED].append(f)
    rows = []
    ordered = sorted(k for k in groups if k != UNATTRIBUTED)
    if UNATTRIBUTED in groups:
        ordered.append(UNATTRIBUTED)
    for key in ordered:
        members = groups[key]
        counts = counts_by_severity(members)
        titles = list(dict.fromkeys(m.title for m in members))[:3]
        rows.append((key, counts, titles))
    return rows


def requirement_scope_note(findings: list[Finding], base: str | None = None) -> str:
    """Which scope the `req:` tags came from, in one line, or nothing.

    Counts the tagged findings by their `req-scope:` marker. `commits` means
    the ids came from the commits since the base that touched the file:
    attribution. `open` means no such commit cited one, so the ids are the
    requirements open when the scan ran: context. Both can appear in one
    report, and the reader is told which rows are which kind of claim.
    """
    tagged = [f for f in findings if any(t.startswith(REQ_TAG) for t in f.tags)]
    from_commits = sum(1 for f in tagged if REQ_SCOPE_COMMITS in f.tags)
    from_open = sum(1 for f in tagged if REQ_SCOPE_OPEN in f.tags)
    since = f"since `{base}`" if base else "since the base"
    parts = []
    if from_commits:
        parts.append(f"{from_commits} from the commits {since} that touched the file "
                     f"(`{REQ_SCOPE_COMMITS}`)")
    if from_open:
        parts.append(f"{from_open} from the requirements open when the scan ran, because no "
                     f"commit {since} touching the file cited one -- context, not attribution "
                     f"(`{REQ_SCOPE_OPEN}`)")
    if not parts:
        return ""
    return "Scope of the `req:` tags: " + "; ".join(parts) + "."


def requirement_block(findings: list[Finding], heading: str = "## By requirement",
                      base: str | None = None) -> list[str]:
    """The Markdown rendering of `requirement_rows`, or nothing.

    `base` is the ref the scan was changed-since, named in the scope note
    under the table so the reader knows which commits the tags were read
    from.
    """
    rows = requirement_rows(findings)
    if not rows:
        return []
    L = [heading, "", "| Requirement | " + " | ".join(s.capitalize() for s in SEV_ORDER) + " | First titles |",
         "|---|" + "---|" * len(SEV_ORDER) + "---|"]
    for key, counts, titles in rows:
        shown = "; ".join(t.replace("|", "\\|") for t in titles)
        L.append(f"| `{key}` | " + " | ".join(str(counts[s]) for s in SEV_ORDER) + f" | {shown} |")
    L.append("")
    note = requirement_scope_note(findings, base)
    if note:
        L.extend([note, ""])
    return L


def _bluf_lines(report: Report) -> list[str]:
    """What this report can and cannot claim, worst news first.

    Meant to sit above the findings table, not below it: a reader who never
    scrolls past the top should still learn the scan was partial, the grade
    is withheld, checks were skipped, or findings carry an unverified,
    doc-sourced scope note -- before seeing a clean-looking table that could
    otherwise read as more complete than it is.
    """
    lines: list[str] = []
    sc = report.scorecard
    ss = report.scan_scope or {}
    if ss.get("mode") == "partial":
        lines.append(
            f"Partial scan -- read {ss.get('fraction_read', 0):.0%} of lines "
            f"({ss.get('basis', 'changed files only')}); checks that read across files did not run."
        )
    if sc.withheld:
        lines.append(f"Grade withheld -- {sc.withheld_reason}")
    skipped = [p for p in report.probes if p.status != "ran"]
    if skipped:
        lines.append(f"{len(skipped)} probe(s) not assessed: {', '.join(p.name for p in skipped)}.")
    noted = sum(1 for f in report.active() if f.scope_note)
    if noted:
        lines.append(
            f"{noted} finding(s) below carry a scope note from this repo's own docs, "
            "marked unverified -- a doc's claim, not a check that ran."
        )
    no_fix = sum(1 for f in report.active() if f.remediation_source == "default")
    if no_fix:
        lines.append(
            f"{no_fix} finding(s) below have no rule-specific remediation -- "
            "neither a curated fix nor anything from the tool itself was available for that rule."
        )
    return lines


# ---------------------------------------------------------------------------

def write_json(report: Report, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")


def write_sarif(report: Report, path: str, min_severity: str | None = None) -> None:
    """SARIF 2.1.0 of the active findings. `min_severity` drops everything below it: a code-scanning
    upload of every low and info finding buries the ones a reviewer should see."""
    rules: dict[str, dict] = {}
    results = []
    for f in report.active():
        if min_severity and not sev_at_least(f.severity, min_severity):
            continue
        if f.rule_id not in rules:
            rules[f.rule_id] = {
                "id": f.rule_id,
                "name": f.rule_id.replace("/", "."),
                "shortDescription": {"text": f.title[:200]},
                "fullDescription": {"text": (f.description or f.title)[:1000]},
                "properties": {
                    "dimension": f.dimension,
                    "provenance": f.provenance,
                    "tags": f.tags + f.controls,
                },
            }
        loc = {
            "physicalLocation": {
                "artifactLocation": {"uri": f.location.path or "."},
                "region": {"startLine": max(1, f.location.start_line or 1)},
            }
        }
        if f.location.logical:
            loc["logicalLocations"] = [{"fullyQualifiedName": f.location.logical}]
        results.append({
            "ruleId": f.rule_id,
            "level": SARIF_LEVEL.get(f.severity, "warning"),
            "message": {"text": f.title},
            "locations": [loc],
            "fingerprints": {"arbiter/v1": f.id},
            "properties": {
                "severity": f.severity,
                "confidence": f.confidence,
                "repo": f.repo_id,
                "status": f.status,
                "remediation": _remediation_text(f),
                "remediation_source": f.remediation_source,
                "scope_note": f.scope_note,
            },
        })
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "arbiter",
                "version": report.arbiter_version,
                "informationUri": "https://example.invalid/arbiter",
                "rules": list(rules.values()),
            }},
            "results": results,
            "invocations": [{
                "executionSuccessful": True,
                "properties": {
                    "profile": report.profile,
                    "coverage": report.scorecard.coverage,
                    "bluf": _bluf_lines(report),
                    "skipped_probes": [
                        {"name": p.name, "reason": p.reason}
                        for p in report.probes if p.status != "ran"
                    ],
                },
            }],
        }],
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(doc, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# GitHub workflow commands
# ---------------------------------------------------------------------------

def _wc_message(text: str) -> str:
    """Escape a workflow-command message the way the runner expects."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _wc_property(text: str) -> str:
    """Property values additionally escape the separators of the syntax."""
    return _wc_message(text).replace(":", "%3A").replace(",", "%2C")


def render_annotations(report: Report) -> str:
    """One GitHub workflow command per active finding.

    `::error` for the findings that failed the gate (`gate.failing_ids`),
    `::notice` for findings in files the change did not touch (tagged
    `outside-this-change`), `::warning` for everything else. Only the rule id,
    the title and the location are printed: never evidence or a snippet, so
    the build log is not where a credential gets reprinted.
    """
    failing = set((report.gate or {}).get("failing_ids") or [])
    lines: list[str] = []
    for f in report.active():
        if f.id in failing:
            level = "error"
        elif "outside-this-change" in f.tags:
            level = "notice"
        else:
            level = "warning"
        props = []
        if f.location.path:
            props.append(f"file={_wc_property(f.location.path)}")
            if f.location.start_line:
                props.append(f"line={f.location.start_line}")
        props.append(f"title={_wc_property(f.rule_id)}")
        lines.append(f"::{level} {','.join(props)}::{_wc_message(f.title)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------

def render_console(report: Report, color: bool | None = None, limit: int = 40) -> str:
    if color is None:
        color = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    L: list[str] = []
    sc = report.scorecard
    active = report.active()
    counts = counts_by_severity(active)

    L.append("")
    L.append(_color(color, "bold", f"  arbiter {report.arbiter_version}  ·  {report.system}  ·  profile={report.profile}"))
    repo_line = ", ".join(f"{r.id}({r.loc} loc)" for r in report.repos)
    L.append(_color(color, "dim", f"  {len(report.repos)} repo(s): {repo_line}"))
    if report.stacks:
        L.append(_color(color, "dim", f"  stacks: {', '.join(report.stacks)}"))
    ss = report.scan_scope or {}
    if ss.get("mode") == "partial":
        # Loud, and above the findings. Somebody scrolling to the counts must
        # not be able to reach them without passing this line.
        L.append("")
        L.append(_color(color, "high", "  PARTIAL SCAN — this is not a report about the repository"))
        L.append(_color(color, "dim",
                        f"  read {ss.get('files_read', 0)} of {ss.get('files_total', 0)} file(s) "
                        f"({ss.get('fraction_read', 0):.0%} of lines) — {ss.get('basis', '')}"))
        L.append(_color(color, "dim",
                        "  checks that read across files were not run; see NOT ASSESSED"))
        outside = sum(1 for f in active if "outside-this-change" in f.tags)
        if outside:
            L.append(_color(color, "dim",
                            f"  {outside} finding(s) are in context files this change did "
                            "not touch (tagged outside-this-change)"))
    L.append("")

    head = "  " + "  ".join(
        _color(color, s, f"{counts[s]} {s}") for s in SEV_ORDER if counts[s]
    )
    L.append(head.rstrip() or _color(color, "green", "  no findings"))

    if sc.withheld:
        L.append("  " + _color(color, "medium", f"grade withheld — {sc.withheld_reason}"))
    elif sc.overall is not None:
        L.append(f"  overall {sc.overall}/100   coverage {sc.coverage:.0%}")
    L.append("")

    if sc.dimensions:
        # In a partial scan a dimension can reach 100% check coverage -- every
        # probe carrying it ran -- while having read a third of the files.
        # The claim ledger already scopes those claims partial; the console
        # must not read more confidently than the ledger does.
        mark = "*" if (report.scan_scope or {}).get("mode") == "partial" else " "
        L.append(_color(color, "bold", "  DIMENSION        SCORE   COVERAGE   FINDINGS"))
        for name, d in sorted(sc.dimensions.items()):
            L.append(f"  {name:<15}  {d.score:>5.1f}   {d.coverage:>6.0%}{mark}   {d.findings:>8}")
        if mark == "*":
            L.append(_color(color, "dim",
                            "  * share of CHECKS that ran, not of the repository: "
                            "these checks read only the changed files"))
        for name in sorted(sc.dimensions):
            desc = DIMENSION_DESC.get(name)
            if desc:
                L.append(_color(color, "dim", f"    {name}: {desc}"))
        L.append("")

    shown = [f for f in active][:limit]
    if shown:
        L.append(_color(color, "bold", "  FINDINGS"))
        for f in shown:
            tag = _color(color, f.severity, f"{f.severity:<8}")
            conf = "" if f.confidence == "high" else _color(color, "dim", f" [{f.confidence} confidence]")
            # short() already carries the repo on cross-repo findings
            prefix = "" if (f.location.repo_id or len(report.repos) == 1) else f"{f.repo_id}:"
            where = prefix + f.location.short()
            L.append(f"  {tag} {where}  {f.title}{conf}")
            L.append(_color(color, "dim", f"           {f.rule_id}  {f.id}"))
        if len(active) > limit:
            L.append(_color(color, "dim", f"  … {len(active) - limit} more (see the JSON report)"))
        L.append("")

    skipped = [p for p in report.probes if p.status != "ran"]
    if skipped:
        L.append(_color(color, "bold", "  NOT ASSESSED"))
        for p in skipped:
            mark = ("error" if p.status == "error"
                    else "n/a  " if not getattr(p, "applicable", True) else "skip ")
            L.append(f"  {mark}  {p.name:<18} {p.reason}")
        L.append("")

    suppressed = [f for f in report.findings if f.suppressed]
    if suppressed:
        L.append(_color(color, "dim", f"  {len(suppressed)} finding(s) suppressed by policy"))

    gate = report.gate or {}
    if gate:
        if gate.get("passed"):
            L.append("  " + _color(color, "green", "gate PASS"))
        else:
            L.append("  " + _color(color, "critical", "gate FAIL") + " — " + "; ".join(gate.get("reasons", [])))
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------

def render_markdown(report: Report) -> str:
    sc = report.scorecard
    active = report.active()
    counts = counts_by_severity(active)
    L: list[str] = []
    L.append(f"# Arbiter report — {report.system}")
    L.append("")
    verdict = "**PASS**" if (report.gate or {}).get("passed") else "**FAIL**"
    L.append(f"{verdict} · profile `{report.profile}` · {len(report.repos)} repo(s) · "
             f"{sum(r.loc for r in report.repos):,} lines · {report.duration_s:.1f}s")
    L.append("")

    bluf = _bluf_lines(report)
    if bluf:
        L.append("> **Read first**")
        for line in bluf:
            L.append(f"> - {line}")
        L.append("")

    if not sc.withheld:
        L.append(f"**Overall {sc.overall}/100** at {sc.coverage:.0%} coverage")
        L.append("")
    L.append("| " + " | ".join(s.capitalize() for s in SEV_ORDER) + " |")
    L.append("|" + "---|" * len(SEV_ORDER))
    L.append("| " + " | ".join(str(counts[s]) for s in SEV_ORDER) + " |")
    L.append("")
    L.extend(requirement_block(active, base=(report.scan_scope or {}).get("changed_since")))

    if sc.dimensions:
        gaps: dict[str, list[str]] = defaultdict(list)
        for p in report.probes:
            if p.status == "ran":
                continue
            for d_name in p.dimensions:
                gaps[d_name].append(f"{p.name}: {p.reason}" if p.reason else p.name)

        L.append("## Dimensions")
        L.append("")
        L.append("| Dimension | Score | Coverage | Checks | Findings |")
        L.append("|---|---|---|---|---|")
        for name, d in sorted(sc.dimensions.items()):
            L.append(f"| {name} | {d.score} | {d.coverage:.0%} | {d.checks_run}/{d.checks_applicable} | {d.findings} |")
        L.append("")
        why_lines = [f"**{name}**: {'; '.join(gaps[name])}" for name in sorted(sc.dimensions) if gaps.get(name)]
        if why_lines:
            L.append("Why checks didn't run:  \n" + "  \n".join(why_lines))
            L.append("")
        legend = [f"**{name}** — {DIMENSION_DESC[name]}"
                  for name in sorted(sc.dimensions) if name in DIMENSION_DESC]
        if legend:
            L.append(" · ".join(legend))
            L.append("")

    if active:
        L.append("## Findings")
        L.append("")
        for sev in SEV_ORDER:
            group = [f for f in active if f.severity == sev]
            if not group:
                continue
            L.append(f"### {sev.capitalize()} ({len(group)})")
            L.append("")
            for members in _grouped(group, lambda f: f.rule_id):
                f = members[0]
                if len(members) == 1:
                    repo = f"`{f.repo_id}` " if len(report.repos) > 1 else ""
                    L.append(f"- {repo}**{f.title}**  \n  `{f.location.short()}` · `{f.rule_id}` · {f.id}")
                    if f.description:
                        L.append(f"  \n  {f.description}")
                    L.append(f"  \n  Fix: {_remediation_text(f)}")
                    if f.scope_note:
                        L.append(f"  \n  ⚠ {f.scope_note}")
                    continue

                files = {m.location.path for m in members}
                locs = [f"`{m.location.short()}`" for m in members]
                loc_text = ", ".join(locs[:GROUP_LOCATION_CAP])
                if len(locs) > GROUP_LOCATION_CAP:
                    loc_text += f", … {len(locs) - GROUP_LOCATION_CAP} more"
                remediations = dict.fromkeys(_remediation_text(m) for m in members)
                L.append(
                    f"- **{f.title}** (×{len(members)} across {len(files)} file(s))  \n"
                    f"  `{f.rule_id}`  \n  {loc_text}"
                )
                L.append(f"  \n  Fix: {' / '.join(remediations)}")
                scope_notes = dict.fromkeys(m.scope_note for m in members if m.scope_note)
                if scope_notes:
                    L.append(f"  \n  ⚠ {' / '.join(scope_notes)}")
            L.append("")

    skipped = [p for p in report.probes if p.status != "ran"]
    if skipped:
        L.append("## Not assessed")
        L.append("")
        for p in skipped:
            L.append(f"- `{p.name}` — {p.reason}")
        L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------

_HTML_CSS = """
:root{--bg:#f3f5f8;--sf:#fff;--ink:#161a21;--ink2:#3d4654;--mut:#66717f;--rule:#d7dde5;
--acc:#1f5b6e;--crit:#9c2118;--high:#a45a05;--med:#6f6410;--low:#24614a;--info:#4c5a6b;--code:#eef1f6}
@media(prefers-color-scheme:dark){:root{--bg:#101318;--sf:#171b22;--ink:#e6eaf0;--ink2:#c2cad6;
--mut:#8d97a6;--rule:#2a313b;--acc:#62a8bd;--crit:#e8776b;--high:#dc9b45;--med:#c2b04a;--low:#6fbd9b;
--info:#93a2b5;--code:#1b2029}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1060px;margin:0 auto;padding:32px 20px 72px}
h1{font-size:26px;letter-spacing:-.02em;margin:0 0 4px}
h2{font-size:16px;letter-spacing:.02em;margin:36px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--rule)}
.sub{color:var(--mut);font-size:13px;margin:0 0 24px;font-variant-numeric:tabular-nums}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:8px}
.card{background:var(--sf);border:1px solid var(--rule);border-radius:5px;padding:12px 14px}
.card .n{font-size:24px;font-weight:600;font-variant-numeric:tabular-nums;line-height:1.1}
.card .l{font-size:11px;letter-spacing:.09em;text-transform:uppercase;color:var(--mut);margin-top:2px}
.banner{border-left:3px solid var(--acc);background:var(--sf);padding:12px 14px;border-radius:0 5px 5px 0;margin:16px 0}
.pass{border-left-color:var(--low)}.fail{border-left-color:var(--crit)}
table{width:100%;border-collapse:collapse;font-size:13.5px;background:var(--sf);
border:1px solid var(--rule);border-radius:5px;overflow:hidden}
th{text-align:left;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--mut);
padding:9px 12px;border-bottom:1px solid var(--rule);white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid var(--rule);vertical-align:top;color:var(--ink2)}
tr:last-child td{border-bottom:none}
.tw{overflow-x:auto}
.pill{display:inline-block;font-size:10px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;
padding:2px 6px;border-radius:3px;border:1px solid currentColor;white-space:nowrap}
.s-critical{color:var(--crit)}.s-high{color:var(--high)}.s-medium{color:var(--med)}
.s-low{color:var(--low)}.s-info{color:var(--info)}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
code{background:var(--code);padding:1px 5px;border-radius:3px}
.bar{height:6px;background:var(--code);border-radius:3px;overflow:hidden;min-width:70px}
.bar i{display:block;height:100%;background:var(--acc)}
.muted{color:var(--mut)}
a{color:var(--acc);text-decoration:none}
a:hover{text-decoration:underline}
a.muted{color:var(--mut)}
"""


def _html_rows_findings(active: list[Finding], loc_cell) -> str:
    """One row per finding, or per (severity, rule) group; loc_cell renders a location."""
    e = html.escape
    out = []
    for members in _grouped(active, lambda f: (f.severity, f.rule_id)):
        f = members[0]
        if len(members) == 1:
            related = ""
            if f.related:
                related = "<br><span class='muted mono'>also: " + e(
                    ", ".join(r.short() for r in f.related)
                ) + "</span>"
            fix = f"<br><span class='muted'>Fix: {e(_remediation_text(f))}</span>"
            scope = f"<br><span class='muted'>&#9888; {e(f.scope_note)}</span>" if f.scope_note else ""
            out.append(
                f"<tr><td><span class='pill s-{f.severity}'>{f.severity}</span></td>"
                f"<td>{e(f.title)}<br><span class='muted mono'>{e(f.rule_id)} · {e(f.id)}"
                f"{'' if f.confidence == 'high' else ' · ' + f.confidence + ' confidence'}</span>{fix}{scope}</td>"
                f"<td class='mono'>{e(f.repo_id)}</td>"
                f"<td class='mono'>{loc_cell(f)}{related}</td>"
                f"<td class='mono'>{e(f.dimension)}</td></tr>"
            )
            continue

        files = {m.location.path for m in members}
        repo_ids = dict.fromkeys(m.repo_id for m in members)
        remediations = dict.fromkeys(_remediation_text(m) for m in members)
        fix = f"<br><span class='muted'>Fix: {e(' / '.join(remediations))}</span>"
        scope_notes = dict.fromkeys(m.scope_note for m in members if m.scope_note)
        scope = f"<br><span class='muted'>&#9888; {e(' / '.join(scope_notes))}</span>" if scope_notes else ""
        locs = [f"{loc_cell(m)} <span class='muted'>({e(m.repo_id)})</span>" for m in members]
        loc_html = "<br>".join(locs[:GROUP_LOCATION_CAP])
        if len(locs) > GROUP_LOCATION_CAP:
            loc_html += f"<br><span class='muted'>… {len(locs) - GROUP_LOCATION_CAP} more</span>"
        out.append(
            f"<tr><td><span class='pill s-{f.severity}'>{f.severity}</span></td>"
            f"<td>{e(f.title)} <span class='muted'>(×{len(members)} across {len(files)} file(s))</span>"
            f"<br><span class='muted mono'>{e(f.rule_id)}</span>{fix}{scope}</td>"
            f"<td class='mono'>{e(', '.join(repo_ids))}</td>"
            f"<td class='mono'>{loc_html}</td>"
            f"<td class='mono'>{e(f.dimension)}</td></tr>"
        )
    return "\n".join(out) or "<tr><td colspan='5' class='muted'>No active findings.</td></tr>"


def _html_rows_dims(report: Report) -> str:
    e = html.escape
    sc = report.scorecard
    # Checks_run/checks_applicable is a gap with no reason attached to
    # it in the row itself -- "compliance 28/148" doesn't say why the
    # other 120 didn't run. The probes that declared this dimension and
    # didn't run (skipped or errored) are why; show them right here
    # instead of making the reader hunt for the Probe outcomes table.
    gaps: dict[str, list[str]] = defaultdict(list)
    for p in report.probes:
        if p.status == "ran":
            continue
        for d_name in p.dimensions:
            gaps[d_name].append(f"{p.name}: {p.reason}" if p.reason else p.name)

    out = []
    for name, d in sorted(sc.dimensions.items()):
        desc = DIMENSION_DESC.get(name, "")
        why = gaps.get(name) or []
        why_html = (
            "<br><span class='muted' style='font-size:11px'>" + e("; ".join(why)) + "</span>"
            if why else ""
        )
        out.append(
            f"<tr><td title='{e(desc)}'>{e(name)}</td><td class='mono'>{d.score}</td>"
            f"<td><div class='bar'><i style='width:{d.coverage*100:.0f}%'></i></div>"
            f"<span class='mono muted'>{d.coverage:.0%}</span></td>"
            f"<td class='mono'>{d.checks_run}/{d.checks_applicable}{why_html}</td>"
            f"<td class='mono'>{d.findings}</td></tr>"
        )
    return "\n".join(out)


def _html_rows_probes(report: Report) -> str:
    e = html.escape
    out = []
    for p in report.probes:
        mark = {"ran": "ran", "skipped": "skipped", "error": "error"}[p.status]
        cls = "s-low" if p.status == "ran" else ("s-high" if p.status == "error" else "s-info")
        out.append(
            f"<tr><td><span class='pill {cls}'>{mark}</span></td>"
            f"<td class='mono'>{e(p.name)}</td>"
            f"<td class='mono'>{p.finding_count if p.status == 'ran' else '—'}</td>"
            f"<td class='mono'>{p.duration_s:.2f}s</td>"
            f"<td class='muted'>{e(p.reason)}</td></tr>"
        )
    return "\n".join(out)


def render_html(report: Report) -> str:
    sc = report.scorecard
    active = report.active()
    counts = counts_by_severity(active)
    e = html.escape

    repo_roots = {r.id: r.path for r in report.repos}

    def loc_cell(f: Finding) -> str:
        text = e(f.location.short())
        root = repo_roots.get(f.repo_id)
        if not (root and f.location.path):
            return text
        try:
            abs_path = (Path(root) / f.location.path).resolve()
        except OSError:
            return text
        line = f.location.start_line or 1
        col = f.location.start_col or 1
        vscode_href = e(f"vscode://file/{abs_path.as_posix()}:{line}:{col}")
        file_href = e(abs_path.as_uri())
        return (
            f"<a href='{vscode_href}' title='Open in an editor at this line (vscode://)'>{text}</a>"
            f" <a class='muted' href='{file_href}' title='Open the file'>&#8599;</a>"
        )

    def dims_legend() -> str:
        parts = [
            f"<b>{e(name)}</b> {e(DIMENSION_DESC[name])}"
            for name in sorted(sc.dimensions) if name in DIMENSION_DESC
        ]
        return " · ".join(parts)

    gate = report.gate or {}
    gate_cls = "pass" if gate.get("passed") else "fail"
    gate_txt = "Gate passed" if gate.get("passed") else "Gate failed — " + e("; ".join(gate.get("reasons", [])))
    # The withheld message already has its own prominent banner below, so it
    # is not repeated here.
    bluf = [line for line in _bluf_lines(report) if not line.startswith("Grade withheld")]
    bluf_html = (
        "<div class='banner'><b>Read first</b><ul style='margin:6px 0 0 18px'>"
        + "".join(f"<li>{e(line)}</li>" for line in bluf) + "</ul></div>"
    ) if bluf else ""
    grade = (
        f"<div class='banner'><b>Grade withheld.</b> {e(sc.withheld_reason)}</div>"
        if sc.withheld else
        f"<div class='banner'><b>Overall {sc.overall}/100</b> at {sc.coverage:.0%} assessment coverage</div>"
    )
    cards = "".join(
        f"<div class='card'><div class='n s-{s}'>{counts[s]}</div><div class='l'>{s}</div></div>"
        for s in SEV_ORDER
    )
    repos = ", ".join(f"{e(r.id)} ({r.loc:,} loc{', ' + e(r.commit) if r.commit else ''})" for r in report.repos)

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Arbiter — {e(report.system)}</title><style>{_HTML_CSS}</style></head><body><div class="wrap">
<h1>Arbiter — {e(report.system)}</h1>
<p class="sub">profile <code>{e(report.profile)}</code> · {len(report.repos)} repo(s): {repos}
 · stacks: {e(', '.join(report.stacks) or 'none detected')} · {report.duration_s:.1f}s · {e(report.started_at)}</p>
<div class="banner {gate_cls}"><b>{gate_txt}</b></div>
{grade}
{bluf_html}
<div class="cards">{cards}</div>
<h2>Dimensions</h2><div class="tw"><table>
<thead><tr><th>Dimension</th><th>Score</th><th>Coverage</th><th>Checks</th><th>Findings</th></tr></thead>
<tbody>{_html_rows_dims(report)}</tbody></table></div>
<p class="sub" style="margin-top:6px">{dims_legend()}</p>
<h2>Findings ({len(active)})</h2><div class="tw"><table>
<thead><tr><th>Severity</th><th>Finding</th><th>Repo</th><th>Location</th><th>Dimension</th></tr></thead>
<tbody>{_html_rows_findings(active, loc_cell)}</tbody></table></div>
<h2>Probe outcomes</h2><div class="tw"><table>
<thead><tr><th>Status</th><th>Probe</th><th>Findings</th><th>Time</th><th>Reason</th></tr></thead>
<tbody>{_html_rows_probes(report)}</tbody></table></div>
<p class="sub" style="margin-top:28px">Absence of findings from a skipped probe is not a pass.
Coverage above counts only checks that actually ran.</p>
</div></body></html>"""


def write_all(report: Report, outdir: str, formats: list[str], sarif_min_severity: str | None = None) -> dict[str, str]:
    Path(outdir).mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    if "json" in formats:
        p = str(Path(outdir) / "report.json"); write_json(report, p); written["json"] = p
    if "sarif" in formats:
        p = str(Path(outdir) / "report.sarif"); write_sarif(report, p, sarif_min_severity); written["sarif"] = p
    if "html" in formats:
        p = str(Path(outdir) / "report.html")
        Path(p).write_text(render_html(report), encoding="utf-8"); written["html"] = p
    if "markdown" in formats or "md" in formats:
        p = str(Path(outdir) / "REPORT.md")
        Path(p).write_text(render_markdown(report), encoding="utf-8"); written["markdown"] = p
    if "pr-comment" in formats:
        from .diff import render_pr_comment
        p = str(Path(outdir) / "pr-comment.md")
        Path(p).write_text(render_pr_comment(report), encoding="utf-8"); written["pr-comment"] = p
    if "annotations" in formats:
        # The CLI also prints these to stdout, which is where the runner reads
        # them; the file is the same text kept with the other outputs.
        p = str(Path(outdir) / "annotations.txt")
        Path(p).write_text(render_annotations(report) + "\n", encoding="utf-8")
        written["annotations"] = p
    return written
