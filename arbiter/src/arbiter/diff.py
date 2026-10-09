"""Report-to-report comparison.

The number that changes behavior is the delta, not the absolute score. This
is what the CI gate reads when `fail_on.new` is set and what the pull-request
comment leads with.

Comparison is by fingerprint, which is why fingerprints exclude line numbers:
otherwise every reformat shows up here as a wall of new findings and a wall of
fixed ones, and nobody reads the comment twice.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .core import SEV_RANK, Finding, Report


@dataclass
class ReportDiff:
    before: Report
    after: Report
    new: list[Finding] = field(default_factory=list)
    fixed: list[Finding] = field(default_factory=list)
    persisting: list[Finding] = field(default_factory=list)
    severity_changed: list[tuple[Finding, Finding]] = field(default_factory=list)
    moved: list[tuple[Finding, Finding]] = field(default_factory=list)

    @property
    def coverage_delta(self) -> float:
        return round(self.after.scorecard.coverage - self.before.scorecard.coverage, 3)

    def score_deltas(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for dim, after in self.after.scorecard.dimensions.items():
            before = self.before.scorecard.dimensions.get(dim)
            if before is not None:
                out[dim] = round(after.score - before.score, 1)
        return out

    def worst_new(self) -> str | None:
        if not self.new:
            return None
        return min(self.new, key=lambda f: SEV_RANK.get(f.severity, 99)).severity

    def to_dict(self) -> dict:
        return {
            "before": {"system": self.before.system, "started_at": self.before.started_at,
                       "findings": len(self.before.active())},
            "after": {"system": self.after.system, "started_at": self.after.started_at,
                      "findings": len(self.after.active())},
            "new": [f.to_dict() for f in self.new],
            "fixed": [f.to_dict() for f in self.fixed],
            "persisting": len(self.persisting),
            "moved": [
                {"id": a.id, "title": a.title,
                 "from": a.location.short(), "to": b.location.short()}
                for a, b in self.moved
            ],
            "severity_changed": [
                {"id": a.id, "title": a.title, "before": a.severity, "after": b.severity}
                for a, b in self.severity_changed
            ],
            "coverage_delta": self.coverage_delta,
            "score_deltas": self.score_deltas(),
        }


def diff_reports(before: Report, after: Report) -> ReportDiff:
    d = ReportDiff(before=before, after=after)
    a_by_id = {f.id: f for f in before.active()}
    b_by_id = {f.id: f for f in after.active()}

    for fid, f in b_by_id.items():
        prev = a_by_id.get(fid)
        if prev is None:
            d.new.append(f)
        else:
            d.persisting.append(f)
            if prev.severity != f.severity:
                d.severity_changed.append((prev, f))
            if prev.location.start_line != f.location.start_line:
                d.moved.append((prev, f))

    for fid, f in a_by_id.items():
        if fid not in b_by_id:
            d.fixed.append(f)

    order = {s: i for i, s in enumerate(["critical", "high", "medium", "low", "info"])}
    d.new.sort(key=lambda f: (order.get(f.severity, 9), f.location.path))
    d.fixed.sort(key=lambda f: (order.get(f.severity, 9), f.location.path))
    return d


def render_diff_console(d: ReportDiff) -> str:
    L: list[str] = ["", f"  diff — {d.before.system}"]
    L.append(f"  {d.before.started_at or 'before'}  →  {d.after.started_at or 'after'}")
    L.append("")
    L.append(f"  {len(d.new)} new · {len(d.fixed)} fixed · {len(d.persisting)} unchanged")
    if d.moved:
        L.append(f"  {len(d.moved)} moved to a different line but kept identity")
    cd = d.coverage_delta
    if cd:
        L.append(f"  coverage {d.before.scorecard.coverage:.0%} → {d.after.scorecard.coverage:.0%} "
                 f"({cd:+.0%})")
    deltas = {k: v for k, v in d.score_deltas().items() if v}
    if deltas:
        L.append("  " + " · ".join(f"{k} {v:+.1f}" for k, v in sorted(deltas.items())))
    L.append("")
    for label, group in (("NEW", d.new), ("FIXED", d.fixed)):
        if not group:
            continue
        L.append(f"  {label} ({len(group)})")
        for f in group[:15]:
            L.append(f"     {f.severity:<8} {f.location.short():<40} {f.title[:58]}")
        if len(group) > 15:
            L.append(f"     … {len(group) - 15} more")
        L.append("")
    if d.severity_changed:
        L.append(f"  SEVERITY CHANGED ({len(d.severity_changed)})")
        for a, b in d.severity_changed[:10]:
            L.append(f"     {a.location.short():<40} {a.severity} → {b.severity}  {a.title[:40]}")
        L.append("")
    return "\n".join(L)


def render_pr_comment(after: Report, d: ReportDiff | None = None) -> str:
    """Compact enough to be read in a pull request without scrolling."""
    gate = after.gate or {}
    verdict = "✅ **Arbiter: pass**" if gate.get("passed") else "❌ **Arbiter: fail**"
    L = [verdict]
    if not gate.get("passed") and gate.get("reasons"):
        L.append("")
        L.append("> " + "; ".join(gate["reasons"]))
    L.append("")

    sc = after.scorecard
    if sc.withheld:
        L.append(f"Grade withheld — coverage {sc.coverage:.0%}. {sc.withheld_reason}")
    else:
        L.append(f"**{sc.overall}/100** at {sc.coverage:.0%} coverage")
    L.append("")

    if d is not None:
        L.append(f"`{len(d.new)}` new · `{len(d.fixed)}` fixed · `{len(d.persisting)}` unchanged")
        L.append("")
        if d.new:
            L.append("| Severity | Finding | Location |")
            L.append("|---|---|---|")
            for f in d.new[:10]:
                L.append(f"| {f.severity} | {f.title} | `{f.location.short()}` |")
            if len(d.new) > 10:
                L.append(f"| | _… {len(d.new) - 10} more_ | |")
            L.append("")
    else:
        counts: dict[str, int] = {}
        for f in after.active():
            counts[f.severity] = counts.get(f.severity, 0) + 1
        L.append(" · ".join(f"`{n}` {s}" for s, n in counts.items()) or "No findings.")
        L.append("")

    from .report import requirement_block
    L.extend(requirement_block(after.active(), heading="**By requirement**",
                               base=(after.scan_scope or {}).get("changed_since")))

    skipped = [p for p in after.probes if p.status != "ran"]
    if skipped:
        names = ", ".join(f"`{p.name}`" for p in skipped[:6])
        more = f" and {len(skipped) - 6} more" if len(skipped) > 6 else ""
        L.append(f"<sub>Not assessed: {names}{more}. Absence of findings from a skipped "
                 f"probe is not a pass.</sub>")
    return "\n".join(L)
