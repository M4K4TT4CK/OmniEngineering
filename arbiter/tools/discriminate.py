#!/usr/bin/env python3
"""Measure whether each rule actually tells good code apart from bad code.

## The question this answers

A rule is worth its severity only if it fires more on code that is broken than
on code that is not. Everything else -- how serious the underlying idea sounds,
how many findings it produces, how confident the regex is -- is beside the
point. A rule that fires equally on both is a description of a coding style,
not a defect detector, and it should not be allowed to move anybody's grade.

## Why the obvious way to measure it is wrong

The obvious way is findings divided by lines of code, comparing the
well-maintained repositories against the deliberately vulnerable ones. That
produced three separate wrong answers before this tool existed -- a wrong
denominator, a wrong numerator, and wrong labels on the code itself.

The first was the denominator. A Kubernetes rule can only fire on Kubernetes
manifests. Measured against whole-repository size, it looks immaculate in a
400,000-line Go project that happens to contain forty lines of YAML -- not
because the rule is good, but because 399,960 lines were never eligible. So
here the denominator for each rule is the number of lines written in the
languages that rule is capable of firing on, counted separately in each
population. A rule that only ever fires on YAML is judged against YAML.

That denominator is derived from where the rule was observed to fire anywhere
in the corpus. This is deliberately conservative: it can only make the
denominator smaller, which makes the good-code rate look worse, which makes it
harder for a rule to pass. A rule that clears this bar has cleared a bar set
against it.

The second problem was the numerator, and it is subtler. Arbiter already knows
that a manifest under `testdata/` is not a deployment: it reports the finding,
labels it "(example code)", and drops its severity and confidence so it barely
moves the grade. Counting that at full weight measures the rule against a claim
the tool never made. In Argo CD, 195 of 203 findings for one rule were exactly
this -- `test/e2e`, `testdata/`, and Lua health-check fixtures for other
projects' custom resources. So each finding is counted by what it is actually
worth: its severity weight times its confidence factor, the same arithmetic the
scorecard uses. A finding the tool already discounted counts as discounted here.

Both numbers are printed. The raw count says how often a rule speaks; the
weighted one says how hard it pushes. Severity is set from the weighted one,
because that is the one that decides whether somebody's build goes red.

The third problem was the population labels. Reference templates and
teaching samples were counted as well-maintained production code. They are
written to be short and readable, so they really do omit resource limits and
pinned versions, and they made six perfectly good rules look like the noisiest
in the tool. They are now a third population, reported and not scored.

## Reading the output

    ratio   what it means                        what to do
    ----    -----------------------------        ------------------
    > 3     fires on broken code and not on      let it carry severity
            working code
    1 - 3   weak or unproven separation          demote, or get more evidence
    < 1     fires MORE on working code           demote to info, or delete
    n/a     never fired on broken code, so       no claim either way
            there is nothing to compare

Ratios are reported with the raw counts beside them, because a ratio computed
from three findings is not evidence and should not be read as though it were.

And the ratio only carries a verdict for security and compliance rules. The
broken population is broken in the security sense; nobody publishes a
repository that is deliberately badly documented. Quality and drift rules get
their number printed with no verdict attached -- see JUDGEABLE below.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arbiter.core import CONFIDENCE_FACTOR, SEV_WEIGHT  # noqa: E402
from arbiter.engine import run_scan  # noqa: E402
from arbiter.inventory import LANG_BY_EXT  # noqa: E402
from arbiter.policy import load_config  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus import CORPUS, NATIVE, POPULATIONS  # noqa: E402

# A finding whose denominator cannot be established is excluded rather than
# guessed at. Findings with no path (cross-repo seam checks) are one such case.
MIN_FINDINGS_FOR_A_RATIO = 5

# The "deliberately vulnerable" repositories are vulnerable in the SECURITY
# sense. Nobody writes a repository that is deliberately badly documented or
# deliberately over-complex, so for those dimensions there is no broken
# population to compare against and the ratio measures nothing.
#
# This matters because it is the difference between a real finding and a
# category error. `ast.function-too-long` fires five times more per line on
# well-maintained code than on the goats -- but the goats are small teaching
# apps and the well-maintained repositories are mature production codebases,
# so that ratio is a statement about codebase age, not about the rule. Only
# security and compliance rules can be judged by this comparison. Everything
# else gets its number printed and no verdict attached.
JUDGEABLE = {"security", "compliance"}


def language_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if name in ("Dockerfile", "Containerfile") or name.startswith("Dockerfile."):
        return "dockerfile"
    if name in ("Makefile", "Jenkinsfile"):
        return name.lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    return LANG_BY_EXT.get(ext, LANG_BY_EXT.get(ext.lower(), "unknown"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/tmp/corpus")
    ap.add_argument("--out", default="/tmp/discriminate.json")
    args = ap.parse_args()

    root = Path(args.root)
    cfg = load_config(None)

    # population -> language -> lines
    loc: dict[str, Counter] = {p: Counter() for p in POPULATIONS}
    # population -> rule -> count, and rule -> languages it was seen firing in
    hits: dict[str, Counter] = {p: Counter() for p in POPULATIONS}
    # the same, counted by what each finding is actually worth to the score
    weight: dict[str, Counter] = {p: Counter() for p in POPULATIONS}
    discounted: Counter = Counter()   # rule -> findings the tool itself downgraded
    rule_langs: dict[str, set[str]] = defaultdict(set)
    severity_of: dict[str, str] = {}
    dimension_of: dict[str, str] = {}
    scanned: Counter = Counter()

    t0 = time.time()
    for name, (population, _stack) in CORPUS.items():
        path = root / name
        if not path.is_dir():
            print(f"  skip {name}: not cloned")
            continue
        rep = run_scan([str(path)], cfg, only=NATIVE, use_adapters=False)
        scanned[population] += 1
        for lang, n in rep.loc_by_language.items():
            loc[population][lang] += n
        for f in rep.active():
            hits[population][f.rule_id] += 1
            w = SEV_WEIGHT.get(f.severity, 0.0) * CONFIDENCE_FACTOR.get(f.confidence, 1.0)
            weight[population][f.rule_id] += w
            if f.confidence != "high":
                discounted[f.rule_id] += 1
            # the declared severity, not the per-finding downgraded one
            dimension_of[f.rule_id] = f.dimension
            severity_of.setdefault(f.rule_id, f.severity)
            if f.confidence == "high":
                severity_of[f.rule_id] = f.severity
            if f.location.path:
                rule_langs[f.rule_id].add(language_of(f.location.path))
        print(f"  {name:<20} {population:<11} {len(rep.active()):>5} findings"
              f"  {sum(rep.loc_by_language.values()):>8} lines")

    rows = []
    for rule in sorted(set(hits["clean"]) | set(hits["vulnerable"]) | set(hits["examples"])):
        langs = rule_langs.get(rule) or set()
        if not langs:
            continue  # no path, so no denominator can be established
        denom = {p: sum(loc[p][lang] for lang in langs) for p in POPULATIONS}
        rate = {
            p: (hits[p][rule] / (denom[p] / 1000)) if denom[p] else None
            for p in POPULATIONS
        }
        wrate = {
            p: (weight[p][rule] / (denom[p] / 1000)) if denom[p] else None
            for p in POPULATIONS
        }

        def _ratio(good, bad):
            if not bad:
                return None          # never fired on broken code: no claim either way
            if not good:
                return float("inf")  # fired on broken code only: perfect separation
            return bad / good

        good, bad = rate["clean"], rate["vulnerable"]
        ratio = _ratio(good, bad)
        wratio = _ratio(wrate["clean"], wrate["vulnerable"])
        rows.append({
            "rule": rule,
            "severity": severity_of.get(rule, "?"),
            "dimension": dimension_of.get(rule, "?"),
            "judgeable": dimension_of.get(rule) in JUDGEABLE,
            "languages": sorted(langs),
            "clean_hits": hits["clean"][rule], "clean_kloc": round(denom["clean"] / 1000, 1),
            "vuln_hits": hits["vulnerable"][rule], "vuln_kloc": round(denom["vulnerable"] / 1000, 1),
            "example_hits": hits["examples"][rule],
            "clean_rate": round(good, 3) if good is not None else None,
            "vuln_rate": round(bad, 3) if bad is not None else None,
            "ratio": ratio,
            "weighted_ratio": wratio,
            "clean_weight": round(weight["clean"][rule], 1),
            "vuln_weight": round(weight["vulnerable"][rule], 1),
            "discounted": discounted[rule],
            "thin": (hits["clean"][rule] + hits["vulnerable"][rule]) < MIN_FINDINGS_FOR_A_RATIO,
        })

    def sort_key(r):
        x = r["weighted_ratio"]
        return (0 if x is None else 1, -(x if x not in (None, float("inf")) else 1e9))

    rows.sort(key=sort_key)

    print(f"\n  scanned {sum(scanned.values())} repositories in {time.time() - t0:.0f}s: "
          + ", ".join(f"{scanned[p]} {p}" for p in POPULATIONS))
    print("\n  DISCRIMINATION — stack-matched, per rule")
    print("  rate = findings per 1000 lines of the languages that rule fires on")
    def fmt(x):
        if x is None:
            return "    n/a"
        if x == float("inf"):
            return "     inf"
        return f"{x:>7.1f}x"

    print(f"\n  {'WEIGHTED':>8}  {'RAW':>8}  {'SEV':<9}{'GOOD n':>8}{'BAD n':>7}"
          f"{'DISC':>6}   RULE")
    for r in rows:
        flag = " ?" if r["thin"] else "  "
        print(f"  {fmt(r['weighted_ratio'])}{flag}{fmt(r['ratio'])}  {r['severity']:<9}"
              f"{r['clean_hits']:>8}{r['vuln_hits']:>7}{r['discounted']:>6}   {r['rule']}")
    print("\n  WEIGHTED = ratio of score pressure, good code vs broken code. "
          "This is the one severity is set from.")
    print("  RAW      = ratio of finding counts, ignoring how much each one counts.")
    print("  DISC     = findings the tool itself labelled example or fixture "
          "material and discounted.")
    print("\n  ? = fewer than "
          f"{MIN_FINDINGS_FOR_A_RATIO} findings in total; the ratio is not evidence yet")

    print("\n  SECURITY AND COMPLIANCE RULES WHOSE SEVERITY IS NOT SUPPORTED")
    print("  (these are judgeable: the vulnerable corpus is deliberately insecure)")
    bad_rows = [
        r for r in rows
        if r["judgeable"] and r["severity"] != "info" and not r["thin"]
        and r["weighted_ratio"] is not None and r["weighted_ratio"] != float("inf")
        and r["weighted_ratio"] < 1.5
    ]
    for r in bad_rows:
        print(f"    {r['weighted_ratio']:>6.1f}x  {r['severity']:<8} {r['rule']}")
    if not bad_rows:
        print("    none — every scoring security rule with enough data separates "
              "the populations")

    print("\n  RULES THIS CORPUS CANNOT JUDGE")
    print("  (no deliberately-broken population exists for their dimension, so the")
    print("   ratio above is a statement about codebase size and age, not the rule)")
    for dim in sorted({r["dimension"] for r in rows if not r["judgeable"]}):
        names = [r for r in rows if r["dimension"] == dim and not r["judgeable"]]
        print(f"    {dim}:")
        for r in sorted(names, key=lambda r: r["rule"]):
            ratio = fmt(r["weighted_ratio"]).strip()
            print(f"      {ratio:>8}  {r['severity']:<8} {r['rule']}")

    Path(args.out).write_text(json.dumps({
        "rows": rows,
        "loc_by_population_language": {p: dict(c) for p, c in loc.items()},
        "repos_scanned": dict(scanned),
    }, indent=2, default=str))
    print(f"\n  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
