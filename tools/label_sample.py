#!/usr/bin/env python3
"""Human labelling for measured precision: sample findings, then score the labels.

    # 1. From a gold run kept with --raw, draw a stratified sample.
    python tools/gold_benchmark.py --corpus ~/wt/corpus-gold --raw --out arbiter-out/gold
    python tools/label_sample.py sample --report arbiter-out/gold/gold-report.json \\
        --corpus ~/wt/corpus-gold --n 200 --out arbiter-out/labels/sheet.csv

    # 2. Two people label copies of the sheet independently (verdict TP or FP;
    #    leave it blank or write "unsure" to abstain).
    # 3. Score: precision per rule with 95% Wilson intervals, the population-
    #    weighted overall precision, and Cohen's kappa between the labellers.
    python tools/label_sample.py score alice.csv bob.csv --json out.json

The gold benchmark's per-KLOC budgets assume every finding on exemplary code
is a false positive. That is an assumption, not a measurement: some of those
findings are true. This turns it into a measurement. The sample is stratified
by (rule, language) so a rule that fires 3 times is not drowned by one that
fires 3,000 times; each row carries its stratum's population so the overall
figure is weighted back to what the scan actually reported.

Kappa says whether the labels mean anything. Two people who agree 90% of
the time on a rule that is 90% FP have agreed no better than chance; a
kappa under about 0.6 says the verdict is not decidable from the sheet
(add context, or write down the rule's intent) before any precision number
built on it is believed.

The corpus trees are untrusted: this reads their files for context lines and
nothing else.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FIELDS = ["sample_id", "rule", "language", "repo", "path", "line", "severity", "title",
          "stratum_size", "context", "verdict", "labeller", "notes"]
VERDICTS = {"tp": "TP", "fp": "FP", "true": "TP", "false": "FP", "y": "TP", "n": "FP"}
Z95 = 1.959964
DEFAULT_EXCLUDE = ("arbiter/ast.", "arbiter/quality.", "arbiter/assurance.suppression-census")


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def findings_from_report(report: dict) -> list[dict]:
    """Every raw finding of a gold run, tagged with its repository and language."""
    raw = report.get("raw_findings")
    if not raw:
        raise SystemExit("report has no raw_findings: rerun gold_benchmark.py with --raw")
    lang = {r["name"]: r.get("language", "") for r in report.get("repos", [])}
    out = []
    for repo, findings in sorted(raw.items()):
        for f in findings:
            out.append({**f, "repo": repo, "language": _language(f["path"]) or lang.get(repo, "")})
    return out


def _language(path: str) -> str:
    try:
        from arbiter.inventory import LANG_BY_EXT
    except ImportError:  # pragma: no cover
        return ""
    lang = LANG_BY_EXT.get(Path(path).suffix, "")
    return "typescript" if lang == "javascript" else lang


def allocate(sizes: dict, n: int, floor: int = 1) -> dict:
    """Sample size per stratum: at least `floor` each (capped by its size),
    the rest proportional to the square root of stratum size.

    Proportional allocation would spend almost the whole budget on the two or
    three loudest rules; equal allocation would label every finding of a rare
    rule and too few of a loud one. Square root sits between them and keeps
    every rule's interval informative.
    """
    alloc = {k: min(floor, v) for k, v in sizes.items()}
    left = n - sum(alloc.values())
    while left > 0:
        room = {k: math.sqrt(v) for k, v in sizes.items() if alloc[k] < v}
        if not room:
            break
        total = sum(room.values())
        given = 0
        for k, w in sorted(room.items(), key=lambda kv: -kv[1]):
            extra = min(sizes[k] - alloc[k], max(1, int(left * w / total)))
            extra = min(extra, left - given)
            if extra <= 0:
                break
            alloc[k] += extra
            given += extra
        if given == 0:
            break
        left -= given
    return alloc


def stratified_sample(findings: list[dict], n: int, seed: int = 20261022,
                      key=lambda f: (f["rule"], f["language"])) -> list[dict]:
    strata: dict[tuple, list[dict]] = defaultdict(list)
    for f in findings:
        strata[key(f)].append(f)
    sizes = {k: len(v) for k, v in strata.items()}
    alloc = allocate(sizes, n)
    rng = random.Random(seed)
    out = []
    for k in sorted(strata):
        pool = sorted(strata[k], key=lambda f: (f["repo"], f["path"], f["line"]))
        for f in rng.sample(pool, alloc[k]):
            out.append({**f, "stratum_size": sizes[k]})
    rng.shuffle(out)  # so a labeller does not see one rule's rows in a block
    return out


def context(corpus: Path | None, repo: str, path: str, line: int, radius: int = 4) -> str:
    if corpus is None:
        return ""
    p = corpus / repo / path
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(file not readable)"
    lo, hi = max(1, line - radius), min(len(lines), line + radius)
    return "\n".join(f"{'>' if i == line else ' '}{i:>5} {lines[i - 1][:200]}" for i in range(lo, hi + 1))


def write_sheet(rows: list[dict], corpus: Path | None, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    recs = []
    for i, f in enumerate(rows, 1):
        recs.append({"sample_id": f"S{i:04d}", "rule": f["rule"], "language": f["language"],
                     "repo": f["repo"], "path": f["path"], "line": f["line"],
                     "severity": f.get("severity", ""), "title": f.get("title", ""),
                     "stratum_size": f["stratum_size"],
                     "context": context(corpus, f["repo"], f["path"], f["line"]),
                     "verdict": "", "labeller": "", "notes": ""})
    if out.suffix == ".json":
        out.write_text(json.dumps(recs, indent=1) + "\n", encoding="utf-8")
        return
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(recs)


# ---------------------------------------------------------------------------
# Scoring -- pure
# ---------------------------------------------------------------------------

def read_sheet(path: Path) -> list[dict]:
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def verdict(row: dict) -> str | None:
    """TP, FP, or None for blank/unsure."""
    return VERDICTS.get(str(row.get("verdict", "")).strip().lower())


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3))


def cohens_kappa(a: list[str], b: list[str]) -> float | None:
    """Kappa over paired verdicts. None when undefined (no pairs, or both
    labellers used a single category, so chance agreement is 1)."""
    if not a or len(a) != len(b):
        return None
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[c] * cb[c] for c in set(ca) | set(cb)) / (n * n)
    if pe >= 1.0:
        return None
    return round((po - pe) / (1 - pe), 3)


def precision_table(rows: list[dict]) -> dict:
    """Per-rule precision from one labelled sheet, plus the stratum-weighted
    overall estimate."""
    per: dict[str, dict] = defaultdict(lambda: {"tp": 0, "fp": 0, "abstain": 0, "population": 0})
    strata: dict[tuple, dict] = defaultdict(lambda: {"tp": 0, "n": 0, "size": 0})
    for r in rows:
        v = verdict(r)
        e = per[r["rule"]]
        s = strata[(r["rule"], r.get("language", ""))]
        s["size"] = int(r.get("stratum_size") or 0)
        if v is None:
            e["abstain"] += 1
            continue
        e["tp" if v == "TP" else "fp"] += 1
        s["n"] += 1
        s["tp"] += v == "TP"
    for (rule, _lang), s in strata.items():
        per[rule]["population"] += s["size"]
    rules = {}
    for rule, e in sorted(per.items()):
        n = e["tp"] + e["fp"]
        rules[rule] = {**e, "labelled": n, "precision": round(e["tp"] / n, 3) if n else None,
                       "ci95": wilson(e["tp"], n)}
    labelled = [s for s in strata.values() if s["n"]]
    pop = sum(s["size"] for s in labelled)
    weighted = sum(s["size"] * s["tp"] / s["n"] for s in labelled) / pop if pop else None
    # Variance of a stratified proportion, with the finite-population correction.
    var = sum((s["size"] / pop) ** 2 * (s["tp"] / s["n"]) * (1 - s["tp"] / s["n"]) / s["n"]
              * ((s["size"] - s["n"]) / (s["size"] - 1) if s["size"] > 1 else 0)
              for s in labelled) if pop else 0.0
    half = Z95 * math.sqrt(var)
    overall = None if weighted is None else {
        "precision": round(weighted, 3),
        "ci95": (round(max(0.0, weighted - half), 3), round(min(1.0, weighted + half), 3)),
        "population": pop, "labelled": sum(s["n"] for s in labelled)}
    return {"rules": rules, "overall": overall}


def agreement(a_rows: list[dict], b_rows: list[dict]) -> dict:
    b_by = {r["sample_id"]: r for r in b_rows}
    pairs, per_rule = [], defaultdict(list)
    for r in a_rows:
        other = b_by.get(r["sample_id"])
        va, vb = verdict(r), verdict(other) if other else None
        if va and vb:
            pairs.append((va, vb))
            per_rule[r["rule"]].append((va, vb))
    disagreements = [r["sample_id"] for r in a_rows
                     if r["sample_id"] in b_by and verdict(r) and verdict(b_by[r["sample_id"]])
                     and verdict(r) != verdict(b_by[r["sample_id"]])]
    return {
        "pairs": len(pairs),
        "observed_agreement": round(sum(x == y for x, y in pairs) / len(pairs), 3) if pairs else None,
        "kappa": cohens_kappa([x for x, _ in pairs], [y for _, y in pairs]),
        "kappa_per_rule": {rule: {"pairs": len(p), "kappa": cohens_kappa([x for x, _ in p], [y for _, y in p])}
                           for rule, p in sorted(per_rule.items())},
        "disagreements": disagreements,
    }


def consensus(a_rows: list[dict], b_rows: list[dict]) -> list[dict]:
    """Rows both labellers agree on; a disagreement becomes an abstention
    until it is adjudicated (write the agreed verdict into both sheets)."""
    b_by = {r["sample_id"]: r for r in b_rows}
    out = []
    for r in a_rows:
        other = b_by.get(r["sample_id"])
        va, vb = verdict(r), verdict(other) if other else None
        out.append({**r, "verdict": va if va and va == vb else ""})
    return out


def render(result: dict) -> str:
    lines = []
    o = result["precision"]["overall"]
    if o:
        lines.append(f"overall precision {o['precision']} (95% CI {o['ci95'][0]}-{o['ci95'][1]}), "
                     f"{o['labelled']} labelled of {o['population']} findings")
    if result.get("agreement"):
        a = result["agreement"]
        lines.append(f"agreement {a['observed_agreement']} over {a['pairs']} pairs, Cohen's kappa {a['kappa']}"
                     f", {len(a['disagreements'])} to adjudicate")
    lines.append(f"{'rule':<52} {'TP':>4} {'FP':>4} {'prec':>6}  95% CI       pop")
    for rule, e in result["precision"]["rules"].items():
        ci = f"{e['ci95'][0]:.2f}-{e['ci95'][1]:.2f}" if e["ci95"] else "-"
        p = f"{e['precision']:.2f}" if e["precision"] is not None else "-"
        lines.append(f"{rule:<52} {e['tp']:>4} {e['fp']:>4} {p:>6}  {ci:<11} {e['population']:>5}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample", help="draw a stratified labelling sheet from a gold run")
    s.add_argument("--report", type=Path, required=True, help="gold-report.json written with --raw")
    s.add_argument("--corpus", type=Path, help="gold corpus root, for code context")
    s.add_argument("--n", type=int, default=200)
    s.add_argument("--seed", type=int, default=20261022)
    s.add_argument("--rule-prefix", action="append", default=[], help="only rules starting with this")
    s.add_argument("--exclude-prefix", action="append", default=None,
                   help="leave out rules starting with this (default: the maintainability metrics "
                        "and census rows, which make no defect claim to label)")
    s.add_argument("--out", type=Path, required=True, help=".csv or .json")
    c = sub.add_parser("score", help="precision per rule, and kappa when two sheets are given")
    c.add_argument("sheets", type=Path, nargs="+")
    c.add_argument("--json", type=Path)
    args = ap.parse_args(argv)

    if args.cmd == "sample":
        report = json.loads(args.report.read_text(encoding="utf-8"))
        findings = findings_from_report(report)
        if args.rule_prefix:
            findings = [f for f in findings if any(f["rule"].startswith(p) for p in args.rule_prefix)]
        exclude = DEFAULT_EXCLUDE if args.exclude_prefix is None else args.exclude_prefix
        findings = [f for f in findings if not any(f["rule"].startswith(p) for p in exclude)]
        rows = stratified_sample(findings, args.n, args.seed)
        if len(rows) > args.n:
            print(f"note: {len(rows)} rows for --n {args.n}: every (rule, language) stratum gets at "
                  "least one, so the sheet is larger than asked", file=sys.stderr)
        corpus = args.corpus.expanduser() if args.corpus else None
        write_sheet(rows, corpus, args.out)
        strata = len({(r["rule"], r["language"]) for r in rows})
        print(f"{len(rows)} findings from {strata} strata ({len(findings)} in the run) -> {args.out}")
        return 0

    sheets = [read_sheet(p) for p in args.sheets]
    result: dict = {}
    if len(sheets) >= 2:
        result["agreement"] = agreement(sheets[0], sheets[1])
        result["precision"] = precision_table(consensus(sheets[0], sheets[1]))
        result["precision_by_labeller"] = [precision_table(sh)["overall"] for sh in sheets]
    else:
        result["precision"] = precision_table(sheets[0])
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
