"""A/B harness.

An arm is any named way of producing findings for the same target. That one
abstraction covers all three comparisons worth making:

  * two Arbiter configurations   (kind: arbiter, different probes/profile)
  * Arbiter versus another tool  (kind: tool, the raw analyzer via its adapter)
  * two builds of Arbiter        (kind: command, each writing its own report.json)

Matching is the hard part. Two tools describing the same defect produce
different rule IDs, so equality is established in three passes: exact
fingerprint, then same file and line, then same file with overlapping title
wording. Every match records which pass found it, because a comparison that
hides how it matched is not evidence.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .core import Finding, Report
from .report import _HTML_CSS, SEV_ORDER

STOPWORDS = {
    "the", "a", "an", "is", "are", "not", "no", "in", "on", "for", "of", "to",
    "with", "and", "or", "be", "this", "that", "it", "has", "have", "should",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t and t not in STOPWORDS and len(t) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------

@dataclass
class Arm:
    name: str
    kind: str = "arbiter"          # arbiter | tool | command
    profile: str | None = None
    config: str | None = None
    only: list[str] = field(default_factory=list)
    skip: list[str] = field(default_factory=list)
    tool: str | None = None        # kind == tool
    command: str | None = None     # kind == command; {target} and {out} are substituted
    label: str = ""

    def describe(self) -> str:
        if self.label:
            return self.label
        if self.kind == "tool":
            return f"raw tool: {self.tool}"
        if self.kind == "command":
            return f"command: {self.command}"
        bits = []
        if self.profile:
            bits.append(f"profile={self.profile}")
        if self.only:
            bits.append("only=" + ",".join(self.only))
        if self.skip:
            bits.append("skip=" + ",".join(self.skip))
        return "arbiter " + (" ".join(bits) if bits else "(defaults)")


@dataclass
class ArmResult:
    name: str
    description: str
    findings: list[Finding] = field(default_factory=list)
    duration_s: float = 0.0
    coverage: float = 0.0
    probes_ran: int = 0
    probes_skipped: int = 0
    error: str = ""

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in SEV_ORDER}
        for f in self.findings:
            if f.severity in out:
                out[f.severity] += 1
        return out


@dataclass
class Match:
    how: str            # fingerprint | location | wording
    a: Finding
    b: Finding

    def severity_disagrees(self) -> bool:
        return self.a.severity != self.b.severity


@dataclass
class GroundTruthResult:
    expected: int = 0
    found: int = 0
    missed: list[str] = field(default_factory=list)
    unexpected: int = 0
    exhaustive: bool = False

    @property
    def recall(self) -> float:
        return self.found / self.expected if self.expected else 0.0

    @property
    def precision(self) -> float | None:
        if not self.exhaustive:
            return None
        denom = self.found + self.unexpected
        return self.found / denom if denom else 0.0

    @property
    def f1(self) -> float | None:
        p = self.precision
        if p is None:
            return None
        r = self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class ABResult:
    name: str
    targets: list[str]
    a: ArmResult
    b: ArmResult
    matches: list[Match] = field(default_factory=list)
    only_a: list[Finding] = field(default_factory=list)
    only_b: list[Finding] = field(default_factory=list)
    truth_a: GroundTruthResult | None = None
    truth_b: GroundTruthResult | None = None

    def agreement(self) -> float:
        total = len(self.matches) + len(self.only_a) + len(self.only_b)
        return len(self.matches) / total if total else 1.0

    def to_dict(self) -> dict:
        def arm(r: ArmResult) -> dict:
            return {
                "name": r.name, "description": r.description,
                "findings": len(r.findings), "duration_s": round(r.duration_s, 3),
                "coverage": r.coverage, "probes_ran": r.probes_ran,
                "probes_skipped": r.probes_skipped, "counts": r.counts(),
                "error": r.error,
            }
        return {
            "name": self.name,
            "targets": self.targets,
            "arms": {"a": arm(self.a), "b": arm(self.b)},
            "agreement": round(self.agreement(), 4),
            "matched": len(self.matches),
            "matched_by": {
                k: sum(1 for m in self.matches if m.how == k)
                for k in ("fingerprint", "location", "wording")
            },
            "severity_disagreements": [
                {"title": m.a.title, "path": m.a.location.short(),
                 "a": m.a.severity, "b": m.b.severity}
                for m in self.matches if m.severity_disagrees()
            ],
            "only_a": [f.to_dict() for f in self.only_a],
            "only_b": [f.to_dict() for f in self.only_b],
            "ground_truth": {
                "a": asdict(self.truth_a) | {"recall": round(self.truth_a.recall, 3),
                                             "precision": self.truth_a.precision,
                                             "f1": self.truth_a.f1} if self.truth_a else None,
                "b": asdict(self.truth_b) | {"recall": round(self.truth_b.recall, 3),
                                             "precision": self.truth_b.precision,
                                             "f1": self.truth_b.f1} if self.truth_b else None,
            },
        }


# ---------------------------------------------------------------------------
# running an arm
# ---------------------------------------------------------------------------

def run_arm(arm: Arm, targets: list[str], system_path: str | None, base_config: dict) -> ArmResult:
    from .engine import run_scan
    from .policy import load_config

    res = ArmResult(name=arm.name, description=arm.describe())
    t0 = time.time()
    try:
        if arm.kind == "command":
            with tempfile.TemporaryDirectory(prefix="arbiter-ab-") as td:
                cmd = (arm.command or "").replace("{target}", targets[0] if targets else "").replace("{out}", td)
                proc = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=1800)
                rp = Path(td) / "report.json"
                if not rp.is_file():
                    res.error = (
                        f"command produced no report.json (exit {proc.returncode}): "
                        f"{(proc.stderr or proc.stdout)[-300:]}"
                    )
                else:
                    rep = Report.from_dict(json.loads(rp.read_text(encoding="utf-8")))
                    res.findings = rep.active()
                    res.coverage = rep.scorecard.coverage
                    res.probes_ran = sum(1 for p in rep.probes if p.status == "ran")
                    res.probes_skipped = sum(1 for p in rep.probes if p.status != "ran")

        elif arm.kind == "tool":
            import shutil as _sh

            from .adapters import load_all
            from .engine import resolve_targets
            from .probes import ProbeContext
            adapters = {a.name: a for a in load_all()}
            ad = adapters.get(arm.tool or "")
            if ad is None:
                res.error = f"no adapter named '{arm.tool}'"
            elif ad.missing_binaries():
                res.error = f"missing binary: {', '.join(ad.missing_binaries())}"
            else:
                name, repos, tmps, manifest = resolve_targets(targets, system_path)
                try:
                    ctx = ProbeContext(repos=repos, config=base_config, system=manifest)
                    res.findings = ad.run(ctx)
                    res.probes_ran = 1
                    res.coverage = 1.0
                finally:
                    for d in tmps:
                        _sh.rmtree(d, ignore_errors=True)

        else:  # arbiter
            cfg = load_config(arm.config) if arm.config else dict(base_config)
            rep = run_scan(
                targets, cfg, system_path=system_path, profile=arm.profile,
                only=arm.only or None, skip=arm.skip or None,
            )
            res.findings = rep.active()
            res.coverage = rep.scorecard.coverage
            res.probes_ran = sum(1 for p in rep.probes if p.status == "ran")
            res.probes_skipped = sum(1 for p in rep.probes if p.status != "ran")
    except Exception as exc:  # noqa: BLE001 - an arm that crashes is a result with an error, so the other arm still reports
        res.error = f"{type(exc).__name__}: {exc}"[:300]
    res.duration_s = time.time() - t0
    return res


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------

def compare(a: list[Finding], b: list[Finding], line_tolerance: int = 2, wording_threshold: float = 0.5):
    """Three-pass match. Returns (matches, only_a, only_b)."""
    unmatched_a = list(a)
    unmatched_b = list(b)
    matches: list[Match] = []

    # pass 1 — identical fingerprints
    b_by_id: dict[str, list[Finding]] = {}
    for f in unmatched_b:
        b_by_id.setdefault(f.id, []).append(f)
    still_a: list[Finding] = []
    for f in unmatched_a:
        pool = b_by_id.get(f.id)
        if pool:
            matches.append(Match("fingerprint", f, pool.pop(0)))
        else:
            still_a.append(f)
    unmatched_a = still_a
    unmatched_b = [f for pool in b_by_id.values() for f in pool]

    # pass 2 — same repo, file and line (within tolerance)
    still_a = []
    for f in unmatched_a:
        hit = None
        if f.location.path and f.location.start_line:
            for g in unmatched_b:
                if (g.repo_id == f.repo_id and g.location.path == f.location.path
                        and g.location.start_line
                        and abs(g.location.start_line - f.location.start_line) <= line_tolerance):
                    hit = g
                    break
        if hit is not None:
            unmatched_b.remove(hit)
            matches.append(Match("location", f, hit))
        else:
            still_a.append(f)
    unmatched_a = still_a

    # pass 3 — same file, overlapping wording
    still_a = []
    for f in unmatched_a:
        ft = _tokens(f.title + " " + f.rule_id.split("/")[-1])
        best, best_score = None, 0.0
        for g in unmatched_b:
            if g.repo_id != f.repo_id or g.location.path != f.location.path:
                continue
            score = _jaccard(ft, _tokens(g.title + " " + g.rule_id.split("/")[-1]))
            if score > best_score:
                best, best_score = g, score
        if best is not None and best_score >= wording_threshold:
            unmatched_b.remove(best)
            matches.append(Match("wording", f, best))
        else:
            still_a.append(f)
    unmatched_a = still_a

    return matches, unmatched_a, unmatched_b


# ---------------------------------------------------------------------------
# ground truth
# ---------------------------------------------------------------------------

def load_ground_truth(target: str) -> dict | None:
    for name in (".arbiter-expected.yaml", ".arbiter-expected.yml"):
        p = Path(target) / name
        if p.is_file():
            import yaml
            try:
                return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                return None
    return None


def _expected_matches(item: dict, f: Finding) -> bool:
    path = item.get("path")
    if path and not (f.location.path == path or f.location.path.endswith("/" + path)):
        return False
    needle = str(item.get("match", "")).lower()
    if needle:
        hay = f"{f.rule_id} {f.title} {f.description} {f.location.logical}".lower()
        if not all(part.strip() in hay for part in needle.split("&")):
            return False
    dim = item.get("dimension")
    if dim and f.dimension != dim:
        return False
    return True


def score_ground_truth(truth: dict, findings: list[Finding]) -> GroundTruthResult:
    items = truth.get("expected") or []
    res = GroundTruthResult(expected=len(items), exhaustive=bool(truth.get("exhaustive", False)))
    consumed: set[int] = set()
    for item in items:
        hit = None
        for i, f in enumerate(findings):
            if i in consumed:
                continue
            if _expected_matches(item, f):
                hit = i
                break
        if hit is None:
            res.missed.append(item.get("id") or f"{item.get('path', '?')}::{item.get('match', '?')}")
        else:
            consumed.add(hit)
            res.found += 1
    res.unexpected = len(findings) - len(consumed)
    return res


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def load_ab_spec(path: str) -> dict:
    import yaml
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if "arms" not in data:
        raise RuntimeError(f"{path} must define `arms`")
    return data


def arm_from_dict(d: dict) -> Arm:
    return Arm(
        name=d.get("name", "arm"),
        kind=d.get("kind", "arbiter"),
        profile=d.get("profile"),
        config=d.get("config"),
        only=d.get("only") or [],
        skip=d.get("skip") or [],
        tool=d.get("tool"),
        command=d.get("command"),
        label=d.get("label", ""),
    )


def run_ab(
    name: str,
    targets: list[str],
    arm_a: Arm,
    arm_b: Arm,
    base_config: dict,
    system_path: str | None = None,
) -> ABResult:
    ra = run_arm(arm_a, targets, system_path, base_config)
    rb = run_arm(arm_b, targets, system_path, base_config)
    matches, only_a, only_b = compare(ra.findings, rb.findings)

    truth_a = truth_b = None
    if targets:
        truth = load_ground_truth(targets[0])
        if truth:
            truth_a = score_ground_truth(truth, ra.findings)
            truth_b = score_ground_truth(truth, rb.findings)

    return ABResult(
        name=name, targets=targets, a=ra, b=rb,
        matches=matches, only_a=only_a, only_b=only_b,
        truth_a=truth_a, truth_b=truth_b,
    )


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render_ab_console(r: ABResult) -> str:
    L: list[str] = []
    L.append("")
    L.append(f"  A/B — {r.name}")
    L.append(f"  target(s): {', '.join(r.targets)}")
    L.append("")
    for tag, arm, truth in (("A", r.a, r.truth_a), ("B", r.b, r.truth_b)):
        L.append(f"  {tag}  {arm.name:<16} {arm.description}")
        if arm.error:
            L.append(f"       ERROR: {arm.error}")
            continue
        c = arm.counts()
        sev = " ".join(f"{c[s]} {s}" for s in SEV_ORDER if c[s]) or "no findings"
        L.append(f"       {len(arm.findings)} findings ({sev})")
        L.append(f"       {arm.duration_s:.2f}s · coverage {arm.coverage:.0%} · "
                 f"{arm.probes_ran} ran / {arm.probes_skipped} skipped")
        if truth:
            line = f"       recall {truth.recall:.0%} ({truth.found}/{truth.expected} planted defects)"
            if truth.precision is not None:
                line += f" · precision {truth.precision:.0%} · F1 {truth.f1:.2f}"
            L.append(line)
            if truth.missed:
                L.append(f"       missed: {', '.join(truth.missed[:6])}"
                         + (" …" if len(truth.missed) > 6 else ""))
    L.append("")
    by = {k: sum(1 for m in r.matches if m.how == k) for k in ("fingerprint", "location", "wording")}
    L.append(f"  agreement {r.agreement():.0%} — {len(r.matches)} matched "
             f"(fingerprint {by['fingerprint']}, location {by['location']}, wording {by['wording']}), "
             f"{len(r.only_a)} only in A, {len(r.only_b)} only in B")
    dis = [m for m in r.matches if m.severity_disagrees()]
    if dis:
        L.append(f"  {len(dis)} severity disagreement(s) on matched findings")
        for m in dis[:5]:
            L.append(f"     {m.a.location.short():<38} A={m.a.severity:<8} B={m.b.severity:<8} {m.a.title[:48]}")
    L.append("")
    for label, group in (("ONLY IN A", r.only_a), ("ONLY IN B", r.only_b)):
        if not group:
            continue
        L.append(f"  {label} ({len(group)})")
        for f in group[:12]:
            L.append(f"     {f.severity:<8} {f.location.short():<38} {f.title[:60]}")
        if len(group) > 12:
            L.append(f"     … {len(group) - 12} more")
        L.append("")
    return "\n".join(L)


def render_ab_html(r: ABResult) -> str:
    import html as _h
    e = _h.escape

    def arm_card(tag: str, arm, truth) -> str:
        c = arm.counts()
        pills = "".join(
            f"<span class='pill s-{s}' style='margin-right:6px'>{c[s]} {s}</span>"
            for s in SEV_ORDER if c[s]
        ) or "<span class='muted'>no findings</span>"
        if arm.error:
            body = f"<div class='pill s-high'>error</div><p class='muted'>{e(arm.error)}</p>"
        else:
            t = ""
            if truth:
                prec = f" · precision <b>{truth.precision:.0%}</b> · F1 <b>{truth.f1:.2f}</b>" if truth.precision is not None else ""
                missed = ("<br><span class='muted mono'>missed: " + e(", ".join(truth.missed)) + "</span>") if truth.missed else ""
                t = (f"<p style='margin:8px 0 0'>recall <b>{truth.recall:.0%}</b> "
                     f"({truth.found}/{truth.expected} planted){prec}{missed}</p>")
            body = (f"<div style='margin:8px 0'>{pills}</div>"
                    f"<p class='muted mono' style='margin:0'>{arm.duration_s:.2f}s · coverage {arm.coverage:.0%}"
                    f" · {arm.probes_ran} ran / {arm.probes_skipped} skipped</p>{t}")
        return (f"<div class='card'><div class='l'>arm {tag}</div>"
                f"<div style='font-size:17px;font-weight:600'>{e(arm.name)}</div>"
                f"<p class='muted mono' style='margin:2px 0 0'>{e(arm.description)}</p>{body}</div>")

    def table(findings, empty: str) -> str:
        if not findings:
            return f"<p class='muted'>{empty}</p>"
        rows = "".join(
            f"<tr><td><span class='pill s-{f.severity}'>{f.severity}</span></td>"
            f"<td>{e(f.title)}<br><span class='muted mono'>{e(f.rule_id)}</span></td>"
            f"<td class='mono'>{e(f.location.short())}</td></tr>"
            for f in findings
        )
        return ("<div class='tw'><table><thead><tr><th>Severity</th><th>Finding</th>"
                f"<th>Location</th></tr></thead><tbody>{rows}</tbody></table></div>")

    dis = [m for m in r.matches if m.severity_disagrees()]
    dis_html = ""
    if dis:
        rows = "".join(
            f"<tr><td>{e(m.a.title)}</td><td class='mono'>{e(m.a.location.short())}</td>"
            f"<td><span class='pill s-{m.a.severity}'>{m.a.severity}</span></td>"
            f"<td><span class='pill s-{m.b.severity}'>{m.b.severity}</span></td>"
            f"<td class='mono muted'>{m.how}</td></tr>"
            for m in dis
        )
        dis_html = ("<h2>Severity disagreements</h2><div class='tw'><table><thead><tr>"
                    "<th>Finding</th><th>Location</th><th>A</th><th>B</th><th>Matched by</th>"
                    f"</tr></thead><tbody>{rows}</tbody></table></div>")

    by = {k: sum(1 for m in r.matches if m.how == k) for k in ("fingerprint", "location", "wording")}

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Arbiter A/B — {e(r.name)}</title><style>{_HTML_CSS}
.split{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:720px){{.split{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<h1>A/B — {e(r.name)}</h1>
<p class="sub">target(s): <code>{e(', '.join(r.targets))}</code></p>
<div class="cards" style="grid-template-columns:1fr 1fr">
{arm_card('A', r.a, r.truth_a)}
{arm_card('B', r.b, r.truth_b)}
</div>
<div class="banner"><b>Agreement {r.agreement():.0%}</b> — {len(r.matches)} matched
(fingerprint {by['fingerprint']}, location {by['location']}, wording {by['wording']}),
{len(r.only_a)} only in A, {len(r.only_b)} only in B.</div>
{dis_html}
<div class="split">
<div><h2>Only in A ({len(r.only_a)})</h2>{table(r.only_a, 'Nothing unique to A.')}</div>
<div><h2>Only in B ({len(r.only_b)})</h2>{table(r.only_b, 'Nothing unique to B.')}</div>
</div>
<p class="sub" style="margin-top:28px">Matching runs in three passes — identical fingerprint, then same
file and line, then overlapping wording in the same file. The pass that produced each match is shown,
because a comparison that hides how it matched is not evidence.</p>
</div></body></html>"""
