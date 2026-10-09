#!/usr/bin/env python3
"""Find the findings where the tools disagree, and send those to be judged.

## Why disagreement is the highest-yield thing to adjudicate

A person will adjudicate perhaps twenty findings before the task becomes a
chore. Which twenty is therefore the whole question, and the obvious answers
are both poor:

  * A random twenty mostly lands on whatever rule fires most, and spends the
    budget confirming something already believed.
  * The twenty most severe mostly lands on findings everyone already agrees
    about, which teaches nothing either.

The informative ones are the findings where two independent analyzers looked
at the same line and reached different conclusions. Exactly one of them is
wrong, so a human verdict there resolves a real uncertainty rather than
confirming a settled one — and it says which tool to trust at that site, which
no amount of running either tool again can establish.

## Four buckets, and why the fourth one matters most

CONTESTED   Both sides cover this kind of defect and only one fired. Exactly
            one of them is wrong about this line, so a verdict here resolves a
            real uncertainty. This is the queue.

SEVERITY    Both fired and ranked it two or more levels apart. Less urgent and
            still real: severity is what decides whether a build goes red.

CORROBORATED
            Both fired and agreed. Reported and deliberately NOT queued — a
            finding two independent tools agree on is the least informative
            thing a person can spend a verdict on.

COVERAGE GAP
            An external tool checked something Arbiter has no rule for at all.
            This is NOT a disagreement and adjudicating it teaches nothing
            about either tool; it is a list of rules worth writing.

The first version of this conflated the last two and reported 238 "contested"
findings on a single repository, nearly all of them checkov checks for things
Arbiter simply does not cover. Separating them is what turns the output from a
pile into a queue.

## Matching, and its limits

Findings are matched by repository, file and line, then by rule family where
line numbers differ. Different tools describe the same defect in different
words, so a site where both fired is not proof they found the *same* thing.
That is stated rather than papered over: a contested pair is a candidate for
adjudication, not a proven contradiction.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arbiter.core import SEV_RANK, Finding  # noqa: E402
from arbiter.engine import run_scan  # noqa: E402
from arbiter.policy import load_config  # noqa: E402

EXTERNAL_TOOLS = ("checkov", "semgrep", "bandit", "gitleaks", "ruff")

# Rule families that plausibly describe the same defect across tools. Without
# this every finding on a busy line looks contested.
FAMILY = [
    ("encryption", ("unencrypted", "encrypt", "kms", "sse")),
    ("public-access", ("public", "acl", "0.0.0.0", "ingress", "anyone")),
    ("secret", ("secret", "credential", "password", "token", "key")),
    ("logging", ("logging", "audit-log", "access-log", "retention", "audit")),
    ("privilege", ("privilege", "root", "capabilit", "escalat", "security-context")),
    ("pinning", ("unpinned", "pin", "mutable", "latest", "version")),
    ("tls", ("tls", "ssl", "plaintext", "certificate", "https")),
]


def family_of(rule_id: str, title: str) -> str:
    blob = f"{rule_id} {title}".lower()
    for name, words in FAMILY:
        if any(w in blob for w in words):
            return name
    return ""


def _tool(f: Finding) -> str:
    return f.rule_id.split("/", 1)[0] if "/" in f.rule_id else "arbiter"


def compare(findings: list[Finding]) -> dict[str, list[dict]]:
    """Split findings into contested, severity-disagreement and corroborated."""
    by_site: dict[tuple, list[Finding]] = defaultdict(list)
    for f in findings:
        if f.suppressed or not f.location.path:
            continue
        by_site[(f.repo_id, f.location.path, f.location.start_line)].append(f)

    # Sites where an external tool fired, by family, so a native finding one
    # line away is still recognised as being about the same thing.
    external_family: dict[tuple, set[str]] = defaultdict(set)
    native_family: dict[tuple, set[str]] = defaultdict(set)
    for f in findings:
        if f.suppressed or not f.location.path:
            continue
        fam = family_of(f.rule_id, f.title)
        if not fam:
            continue
        key = (f.repo_id, f.location.path, fam)
        (external_family if _tool(f) in EXTERNAL_TOOLS else native_family)[key].add(f.rule_id)

    # Which families Arbiter has any rule for at all, anywhere in this run.
    # This is the distinction that makes the queue worth reading: an external
    # tool firing where Arbiter has NO rule is a coverage gap, not a
    # disagreement, and adjudicating it teaches nothing about either tool. The
    # first version of this conflated the two and produced 238 "contested"
    # findings on one repository, almost all of them checkov checks for things
    # Arbiter simply does not cover.
    covered_families = {family_of(f.rule_id, f.title) for f in findings
                        if _tool(f) == "arbiter"} - {""}

    contested: list[dict] = []
    severity: list[dict] = []
    corroborated: list[dict] = []
    gaps: list[dict] = []
    seen: set[str] = set()

    for f in findings:
        if f.suppressed or not f.location.path or f.id in seen:
            continue
        seen.add(f.id)
        fam = family_of(f.rule_id, f.title)
        if not fam:
            continue
        key = (f.repo_id, f.location.path, fam)
        mine = native_family.get(key, set())
        theirs = external_family.get(key, set()) - {f.rule_id}
        mine_others = mine - {f.rule_id}
        is_native = _tool(f) == "arbiter"
        row = {
            "id": f.id, "rule": f.rule_id, "title": f.title[:120],
            "severity": f.severity, "family": fam,
            "where": f.location.short(), "tool": _tool(f),
            # The OTHER side's rules, never the finding's own.
            "other_side": sorted(theirs if is_native else mine_others)[:4],
        }
        if mine and external_family.get(key):
            peers = [g for g in by_site.get(
                (f.repo_id, f.location.path, f.location.start_line), [])
                if family_of(g.rule_id, g.title) == fam and _tool(g) != _tool(f)]
            gap = max((abs(SEV_RANK.get(f.severity, 9) - SEV_RANK.get(g.severity, 9))
                       for g in peers), default=0)
            if gap >= 2:
                row["gap"] = gap
                severity.append(row)
            else:
                corroborated.append(row)
        elif is_native and not theirs:
            # Arbiter alone. Either it sees something the others do not, or it
            # is wrong. That is exactly the uncertainty a verdict resolves.
            row["only"] = "arbiter"
            contested.append(row)
        elif not is_native and not mine:
            if fam in covered_families:
                # Arbiter HAS rules for this family and did not fire here.
                # A likely miss, and the more valuable direction of the two.
                row["only"] = _tool(f)
                contested.append(row)
            else:
                row["only"] = _tool(f)
                gaps.append(row)
    return {"contested": contested, "severity_disagreement": severity,
            "corroborated": corroborated, "coverage_gap": gaps}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="*", default=["/tmp/corpus/terragoat"])
    ap.add_argument("--out", default="training/disagreements.json")
    ap.add_argument("--queue", default="",
                    help="also write a review file of the contested findings")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    cfg = dict(load_config(None))
    cfg["profile"] = "connected"   # external adapters need it
    findings: list[Finding] = []
    reports = []
    for t in args.targets:
        rep = run_scan([t], cfg, use_adapters=True)
        findings.extend(rep.active())
        reports.append(rep)
        print(f"  {Path(t).name:<20}{len(rep.active()):>5} findings")

    res = compare(findings)
    print(f"\n  {len(res['contested'])} contested — both tools cover this, only one fired")
    print(f"  {len(res['severity_disagreement'])} ranked very differently by the two")
    print(f"  {len(res['corroborated'])} corroborated — agreed, deliberately NOT queued")
    print(f"  {len(res['coverage_gap'])} coverage gaps — an external tool checks "
          "something Arbiter has no\n      rule for at all. Not a disagreement; a "
          "list of rules worth writing.")

    print("\n  CONTESTED, the highest-yield thing to adjudicate")
    for row in res["contested"][:args.limit]:
        others = ", ".join(row["other_side"]) or "nothing at this site"
        print(f"    only {row['only']:<9}{row['severity']:<9}{row['where'][:40]:<42}"
              f"{row['rule'][:60]}")
        print(f"      {row['family']}: the other side has {others}")

    if res["coverage_gap"]:
        fams = Counter(r["family"] for r in res["coverage_gap"])
        print("\n  COVERAGE GAPS BY FAMILY — candidate rules to write")
        for fam, n in fams.most_common(8):
            print(f"    {n:>5}  {fam}")

    if args.queue:
        by_id = {f.id: f for f in findings}
        picked = [by_id[r["id"]] for r in res["contested"][:args.limit] if r["id"] in by_id]
        if picked:
            from arbiter.learn import Knowledge
            from arbiter.review_ui import render_html
            paths = {r.id: r.path for rep in reports for r in rep.repos}
            Path(args.queue).parent.mkdir(parents=True, exist_ok=True)
            Path(args.queue).write_text(render_html(
                picked, Knowledge(), paths, "arbiter review report.json --apply review.md"))
            print(f"\n  wrote {args.queue} — {len(picked)} contested findings to judge")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "schema_version": "1.0",
        "targets": args.targets,
        "counts": {k: len(v) for k, v in res.items()},
        **res,
    }, indent=2))
    print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
