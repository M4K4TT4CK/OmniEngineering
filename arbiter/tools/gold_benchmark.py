#!/usr/bin/env python3
"""Gold-standard benchmark: how often each rule fires on exemplary code (ARB-065).

    python tools/gold_benchmark.py --corpus ~/corpus-gold                 # precision
    python tools/gold_benchmark.py --corpus ~/corpus-gold --recall owasp-benchmark-java
    python tools/gold_benchmark.py --corpus ~/corpus-gold --only go-etcd,go-grpc --jobs 4

Precision mode scans every gold repository named in `tools/fetch_corpus.sh`
(the `get_pinned` lines: name, url, sha, language) and counts findings per
rule, per repository and per thousand lines of code. On code this carefully
run nearly every finding is the rule's fault, so the headline number is
findings per KLOC per rule, held against a per-rule budget in
`tools/gold/budgets.json`. Any rule over budget makes the exit status 1.

Recall mode scans a benchmark with labelled ground truth
(`tools/gold/benchmarks.json`: OWASP Benchmark for Java and Python) and reports,
per CWE, the share of real vulnerabilities caught and the share of safe test
cases flagged anyway.

The trees are untrusted. They are read and nothing else: no build, no install,
no adapters unless `--adapters` is given, and never their own arbiter.yaml.

Writes `<out>/gold-report.json` and `<out>/gold-summary.md` (or
`recall-<name>.json` / `.md`).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FETCH_SCRIPT = ROOT / "tools" / "fetch_corpus.sh"
DATA = ROOT / "tools" / "gold"
SCHEMA_VERSION = 1

# The five languages the rule packs cover. JavaScript is scored with
# TypeScript because one pack covers both.
LANG_GROUP = {"python": "python", "typescript": "typescript", "javascript": "typescript",
              "go": "go", "java": "java", "ruby": "ruby"}
PACKS = frozenset(LANG_GROUP.values())

_PIN = re.compile(r"^get_pinned\s+(\S+)\s+(\S+)\s+([0-9a-f]{40})\s+(\S+)\s*$")


@dataclass(frozen=True)
class Pin:
    name: str
    url: str
    sha: str
    language: str


def parse_pins(text: str) -> list[Pin]:
    """Every `get_pinned name url sha language` line of the fetch script."""
    return [Pin(*m.groups()) for m in map(_PIN.match, text.splitlines()) if m]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Counting lines
# ---------------------------------------------------------------------------

def count_lines(repo: Path) -> dict:
    """Non-blank lines per language group, split into production and test.

    Uses the scanner's own walk rules (skipped directories, language by
    extension, test-path hints) so the denominator is the code Arbiter read.
    """
    from arbiter.inventory import LANG_BY_EXT, SKIP_DIRS, TEST_HINTS
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"prod": 0, "test": 0})
    stack = [repo]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.is_symlink():
                continue
            if p.is_dir():
                if p.name not in SKIP_DIRS:
                    stack.append(p)
                continue
            group = LANG_GROUP.get(LANG_BY_EXT.get(p.suffix, ""))
            if not group:
                continue
            try:
                with p.open("rb") as fh:
                    n = sum(1 for line in fh if line.strip())
            except OSError:
                continue
            rel = p.relative_to(repo).as_posix()
            out[group]["test" if TEST_HINTS.search(rel) else "prod"] += n
    return {k: dict(v) for k, v in out.items()}


def language_of(path: str) -> str:
    from arbiter.inventory import LANG_BY_EXT
    return LANG_GROUP.get(LANG_BY_EXT.get(Path(path).suffix, ""), "other")


def is_test_path(path: str) -> bool:
    from arbiter.inventory import TEST_HINTS
    return bool(TEST_HINTS.search(path))


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def _head(repo: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=10, check=False)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def scan(repo: Path, use_adapters: bool = False) -> list[dict]:
    """Active findings for one directory, reduced to what the report needs."""
    from arbiter.engine import run_scan
    from arbiter.policy import load_config
    report = run_scan([str(repo)], load_config(None), use_adapters=use_adapters,
                      use_cache=False)
    return [{"id": f.id, "rule": f.rule_id, "path": f.location.path, "line": f.location.start_line,
             "cwe": list(f.cwe), "probe": f.probe, "severity": f.severity,
             "confidence": f.confidence, "title": f.title}
            for f in report.active()]


def _scan_repo(args: tuple[str, str, bool]) -> dict:
    path, name, use_adapters = args
    repo = Path(path)
    started = time.time()
    findings = scan(repo, use_adapters)
    return {"name": name, "head": _head(repo), "lines": count_lines(repo),
            "findings": findings, "seconds": round(time.time() - started, 1)}


# ---------------------------------------------------------------------------
# Aggregation and budgets -- pure functions, tested without a scan
# ---------------------------------------------------------------------------

def rule_pack(rule: str) -> str | None:
    """`arbiter/go.http-client-no-timeout` -> `go`; shared probes -> None."""
    head = rule.split("/", 1)[-1].split(".", 1)[0]
    return head if head in PACKS else None


def kloc(lines: dict, groups=None, include_tests: bool = True) -> float:
    total = 0
    for g, split in lines.items():
        if groups is None or g in groups:
            total += split.get("prod", 0) + (split.get("test", 0) if include_tests else 0)
    return total / 1000.0


def confidence_weight(finding: dict) -> float:
    """What one finding would cost if budgets were confidence-weighted.

    Reported as `weighted_per_kloc` beside the raw rate, for information
    only: budgets are held against the raw rate. A finding its rule marks
    below the packs' medium default is charged the scorecard's discount for
    it, `CONFIDENCE_FACTOR` low over medium (0.3 / 0.6 = 0.5); high and
    medium count 1; a finding recorded without a confidence counts in full.
    Weighting the budget itself was measured on 2026-10-21 and not adopted:
    it moved secrets.private-key, authored.security-check-disabled and
    authored.undeclared-import under budget through their existing
    low-confidence findings, while java.test-without-assertion stayed over
    (0.046 weighted against 0.03). That is a policy decision, not a default.
    """
    from arbiter.core import CONFIDENCE_FACTOR
    factor = CONFIDENCE_FACTOR.get(finding.get("confidence") or "high", 1.0)
    return min(1.0, factor / CONFIDENCE_FACTOR["medium"])


def aggregate(repos: list[dict], samples_per_repo: int = 3, max_samples: int = 15) -> dict:
    """Per-rule totals from per-repo scan results.

    A language pack rule is divided by the KLOC of its own language across the
    gold set; a shared probe by the KLOC of all five languages. Dividing a Go
    rule by Java lines would make it look quieter than it is. `per_kloc` is
    the raw rate the budget is held against; `weighted_per_kloc` charges each
    finding its confidence weight and is reported for information.
    """
    total_lines: dict[str, dict[str, int]] = defaultdict(lambda: {"prod": 0, "test": 0})
    for r in repos:
        for g, split in r["lines"].items():
            for k in ("prod", "test"):
                total_lines[g][k] += split.get(k, 0)
    rules: dict[str, dict] = {}
    for r in repos:
        for f in r["findings"]:
            s = rules.setdefault(f["rule"], {"rule": f["rule"], "count": 0, "in_tests": 0,
                                             "weighted": 0.0, "confidence": Counter(),
                                             "by_repo": Counter(), "by_language": Counter(),
                                             "cwe": set(), "samples": []})
            s["count"] += 1
            s["weighted"] += confidence_weight(f)
            s["confidence"][f.get("confidence") or "high"] += 1
            s["in_tests"] += is_test_path(f["path"])
            s["by_repo"][r["name"]] += 1
            s["by_language"][language_of(f["path"])] += 1
            s["cwe"].update(f.get("cwe") or [])
            # Spread samples across repositories: a few from each, so one
            # noisy repository cannot hide what the rule does elsewhere.
            if s["by_repo"][r["name"]] <= samples_per_repo and len(s["samples"]) < max_samples:
                s["samples"].append(f"{r['name']}:{f['path']}:{f['line']}")
    out = []
    for s in rules.values():
        pack = rule_pack(s["rule"])
        denom = kloc(total_lines, {pack} if pack else None)
        out.append({
            "rule": s["rule"], "pack": pack or "shared", "count": s["count"],
            "in_tests": s["in_tests"], "kloc_denominator": round(denom, 1),
            "per_kloc": round(s["count"] / denom, 4) if denom else None,
            "weighted_per_kloc": round(s["weighted"] / denom, 4) if denom else None,
            "confidence": dict(s["confidence"].most_common()),
            "repos_hit": len(s["by_repo"]),
            "by_repo": dict(s["by_repo"].most_common()),
            "by_language": dict(s["by_language"].most_common()),
            "cwe": sorted(s["cwe"]), "samples": s["samples"],
        })
    out.sort(key=lambda x: (-(x["per_kloc"] or 0), x["rule"]))
    return {"lines": {k: dict(v) for k, v in total_lines.items()}, "rules": out}


def budget_for(rule: str, budgets: dict) -> float:
    """Budget in findings per KLOC: an exact rule entry, else the longest
    matching prefix entry, else the default."""
    entries = budgets.get("rules", {})
    if rule in entries:
        return float(entries[rule]["per_kloc"])
    prefixes = sorted((p for p in budgets.get("prefixes", {}) if rule.startswith(p)),
                      key=len, reverse=True)
    if prefixes:
        return float(budgets["prefixes"][prefixes[0]]["per_kloc"])
    return float(budgets["default_per_kloc"])


def evaluate(rules: list[dict], budgets: dict) -> list[dict]:
    """Attach each rule's budget; return the rules over budget.

    The budget is held against the raw rate: every finding counts in full,
    whatever its confidence (see `confidence_weight` for why). A rule with
    fewer findings than `min_count` is never a breach: on a few hundred KLOC,
    two findings are an anecdote, not a rate.
    """
    min_count = int(budgets.get("min_count", 1))
    breaches = []
    for r in rules:
        b = budget_for(r["rule"], budgets)
        r["budget_per_kloc"] = b
        r["over_budget"] = (r["per_kloc"] is not None and r["per_kloc"] > b
                            and r["count"] >= min_count)
        if r["over_budget"]:
            breach = {"rule": r["rule"], "per_kloc": r["per_kloc"], "budget": b,
                      "count": r["count"]}
            accepted = budgets.get("accepted_breaches", {}).get(r["rule"])
            if accepted:
                ceiling = float(accepted["ceiling_per_kloc"])
                breach["accepted"] = r["per_kloc"] <= ceiling
                breach["ceiling"] = ceiling
                breach["reason"] = accepted["reason"]
            r["accepted_breach"] = bool(breach.get("accepted"))
            breaches.append(breach)
    return breaches


def failing_breaches(breaches: list[dict]) -> list[dict]:
    """Breaches that fail the run: every breach except an accepted one still
    at or under its recorded ceiling. An accepted breach stays in the report
    and in `rules_over_budget`; only a rise past its ceiling fails again."""
    return [b for b in breaches if not b.get("accepted")]


def build_report(repos: list[dict], pins: dict[str, Pin], budgets: dict,
                 missing: list[str], meta: dict) -> dict:
    agg = aggregate(repos)
    breaches = evaluate(agg["rules"], budgets)
    repo_rows = []
    for r in repos:
        pin = pins.get(r["name"])
        k = kloc(r["lines"])
        repo_rows.append({
            "name": r["name"], "language": pin.language if pin else "",
            "pinned_sha": pin.sha if pin else "", "head": r.get("head", ""),
            "at_pin": bool(pin and r.get("head") == pin.sha),
            "lines": r["lines"], "kloc": round(k, 1), "findings": len(r["findings"]),
            "per_kloc": round(len(r["findings"]) / k, 3) if k else None,
            "seconds": r.get("seconds"),
        })
    total = sum(len(r["findings"]) for r in repos)
    all_k = kloc(agg["lines"])
    return {
        "schema_version": SCHEMA_VERSION, "kind": "gold-precision", **meta,
        "totals": {"repos": len(repos), "kloc": round(all_k, 1), "findings": total,
                   "per_kloc": round(total / all_k, 3) if all_k else None,
                   "rules_fired": len(agg["rules"]), "rules_over_budget": len(breaches),
                   "rules_over_budget_accepted": len(breaches) - len(failing_breaches(breaches))},
        "lines": agg["lines"], "missing": missing, "repos": repo_rows,
        "rules": agg["rules"], "breaches": breaches,
    }


def render_markdown(report: dict, top: int = 15) -> str:
    t = report["totals"]
    out = ["# Gold-standard benchmark", "",
           (f"Arbiter `{report.get('arbiter_commit', '?')}`, {t['repos']} repositories, "
            f"{t['kloc']} KLOC, {t['findings']} findings ({t['per_kloc']}/KLOC), "
            f"{t['rules_fired']} rules fired, **{t['rules_over_budget']} over budget**"
            f" ({t.get('rules_over_budget_accepted', 0)} of them accepted breaches)."), ""]
    if report.get("missing"):
        out += [f"Not scanned (missing or not at the pinned commit): {', '.join(report['missing'])}", ""]
    out += ["## Rules by findings per KLOC", "",
            ("Budgets are held against the raw rate. The weighted rate, for information, "
             "counts a low-confidence finding 0.5 (the scorecard's low/medium factor)."), "",
            "| rule | findings | /KLOC | weighted /KLOC | budget | in tests | repos | over |",
            "|---|---:|---:|---:|---:|---:|---:|:-:|"]
    for r in report["rules"][:top]:
        out.append(f"| `{r['rule']}` | {r['count']} | {r['per_kloc']} "
                   f"| {r.get('weighted_per_kloc', r['per_kloc'])} | {r['budget_per_kloc']} "
                   f"| {r['in_tests']} | {r['repos_hit']} | "
                   f"{('accepted' if r.get('accepted_breach') else '**yes**') if r['over_budget'] else ''} |")
    accepted = [b for b in report.get("breaches", []) if "ceiling" in b]
    if accepted:
        out += ["", "## Accepted breaches", "",
                "Over budget by a recorded decision; the run fails again if the rate passes the ceiling.", "",
                "| rule | /KLOC | budget | ceiling | within ceiling | reason |", "|---|---:|---:|---:|:-:|---|"]
        for b in accepted:
            out.append(f"| `{b['rule']}` | {b['per_kloc']} | {b['budget']} | {b['ceiling']} "
                       f"| {'yes' if b['accepted'] else '**no**'} | {b['reason']} |")
    out += ["", "## Repositories", "", "| repo | lang | KLOC | findings | /KLOC | at pin |",
            "|---|---|---:|---:|---:|:-:|"]
    for r in sorted(report["repos"], key=lambda x: (x["language"], x["name"])):
        out.append(f"| {r['name']} | {r['language']} | {r['kloc']} | {r['findings']} "
                   f"| {r['per_kloc']} | {'yes' if r['at_pin'] else '**no**'} |")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Recall on labelled benchmarks
# ---------------------------------------------------------------------------

def read_expected(path: Path) -> dict[str, dict]:
    """OWASP Benchmark `expectedresults-*.csv`: test name, category, real, cwe."""
    out = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row or row[0].lstrip().startswith("#"):
                continue
            name, category, real, cwe = (c.strip() for c in row[:4])
            out[name] = {"category": category, "real": real.lower() == "true",
                         "cwe": f"CWE-{int(cwe)}"}
    return out


def claimed_cwes(language: str) -> set[str]:
    """CWEs Arbiter's own rules declare for a language: its pack plus the
    shared native table. A benchmark category outside this set is reported
    but kept out of the in-scope recall -- a miss there is a coverage gap,
    not a broken rule."""
    from arbiter.cwe import NATIVE
    from arbiter.lang_rules import load_packs
    out = {c for cwes in NATIVE.values() for c in cwes}
    pack = load_packs().get(LANG_GROUP.get(language, language), {})
    for rule in pack.get("rules", []):
        out.update(rule.get("cwe") or [])
    return out


def score_recall(expected: dict[str, dict], findings: list[dict], aliases: dict[str, list[str]],
                 test_dir: str, claimed: set[str] | None = None) -> dict:
    """Per CWE: true and false positives over the labelled test cases.

    A case counts as flagged when a finding in its own file carries the case's
    CWE or one of its aliases (`CWE-330` is also caught as `CWE-338`). OWASP's
    own score is TPR minus FPR, which rewards neither "flag everything" nor
    "flag nothing".
    """
    by_file: dict[str, set[str]] = defaultdict(set)
    other: Counter = Counter()
    prefix = test_dir.rstrip("/") + "/"
    for f in findings:
        path = f["path"]
        if not path.startswith(prefix):
            continue
        stem = Path(path).stem
        by_file[stem].update(f.get("cwe") or [])
    per: dict[str, Counter] = defaultdict(Counter)
    caught_examples: dict[str, list[str]] = defaultdict(list)
    for name, e in expected.items():
        wanted = {e["cwe"], *aliases.get(e["cwe"], [])}
        hit = bool(by_file.get(name, set()) & wanted)
        key = ("tp" if hit else "fn") if e["real"] else ("fp" if hit else "tn")
        per[e["cwe"]][key] += 1
        if hit and e["real"] and len(caught_examples[e["cwe"]]) < 3:
            caught_examples[e["cwe"]].append(name)
        for c in by_file.get(name, set()) - wanted:
            other[c] += 1
    rows = []
    tot, in_scope = Counter(), Counter()
    for cwe, c in sorted(per.items(), key=lambda x: int(x[0].split("-")[1])):
        tot.update(c)
        row = _rates(cwe, c, sorted({e["category"] for e in expected.values() if e["cwe"] == cwe}),
                     caught_examples.get(cwe, []))
        if claimed is not None:
            row["in_scope"] = bool({cwe, *aliases.get(cwe, [])} & claimed)
            if row["in_scope"]:
                in_scope.update(c)
        rows.append(row)
    rep = {"per_cwe": rows, "overall": _rates("all", tot, [], []),
           "other_cwes_in_test_files": dict(other.most_common())}
    if claimed is not None:
        rep["in_scope"] = _rates("in-scope", in_scope, [], [])
    return rep


def _rates(cwe: str, c: Counter, categories: list[str], examples: list[str]) -> dict:
    real = c["tp"] + c["fn"]
    safe = c["fp"] + c["tn"]
    tpr = c["tp"] / real if real else None
    fpr = c["fp"] / safe if safe else None
    return {"cwe": cwe, "categories": categories, "real": real, "safe": safe,
            "tp": c["tp"], "fn": c["fn"], "fp": c["fp"], "tn": c["tn"],
            "recall": round(tpr, 3) if tpr is not None else None,
            "false_positive_rate": round(fpr, 3) if fpr is not None else None,
            "score": round(tpr - fpr, 3) if tpr is not None and fpr is not None else None,
            "caught_examples": examples}


def render_recall_markdown(name: str, rep: dict) -> str:
    o = rep["overall"]
    out = [f"# Recall on {name}", "",
           (f"Arbiter `{rep.get('arbiter_commit', '?')}`, source {rep['source']['url']} @ "
            f"`{rep['source']['sha'][:12]}` ({rep['source']['license']}). "
            f"Overall recall {o['recall']}, false-positive rate {o['false_positive_rate']}, "
            f"score (TPR-FPR) {o['score']}.")]
    if "in_scope" in rep:
        i = rep["in_scope"]
        out.append(f"On the CWEs Arbiter's rules claim: recall {i['recall']}, "
                   f"false-positive rate {i['false_positive_rate']}, score {i['score']}.")
    out += ["", "| CWE | category | claimed | real | caught | recall | safe | flagged | FPR | score |",
            "|---|---|:-:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rep["per_cwe"]:
        claimed = {True: "yes", False: "no"}.get(r.get("in_scope"), "?")
        out.append(f"| {r['cwe']} | {', '.join(r['categories'])} | {claimed} | {r['real']} | {r['tp']} "
                   f"| {r['recall']} | {r['safe']} | {r['fp']} | {r['false_positive_rate']} "
                   f"| {r['score']} |")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------

def _arbiter_commit() -> str:
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10, check=False)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def run_precision(args, pins: list[Pin], recall_names: set[str]) -> int:
    budgets = load_json(Path(args.budgets))
    gold = [p for p in pins if p.name not in recall_names]
    if args.only:
        wanted = set(args.only.split(","))
        gold = [p for p in gold if p.name in wanted]
    corpus = Path(args.corpus).expanduser()
    present, missing = [], []
    for p in gold:
        d = corpus / p.name
        if not d.is_dir():
            missing.append(p.name)
        elif _head(d) != p.sha and not args.allow_unpinned:
            missing.append(f"{p.name} (HEAD {_head(d)[:12] or '?'} != pin {p.sha[:12]})")
        else:
            present.append(p)
    jobs = [(str(corpus / p.name), p.name, args.adapters) for p in present]
    results = []
    def _done(res: dict) -> None:
        print(f"  {res['name']:<24} {len(res['findings']):>6} findings  {res['seconds']}s",
              file=sys.stderr, flush=True)
        results.append(res)

    if args.jobs <= 1:
        for job in jobs:
            _done(_scan_repo(job))
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for res in pool.map(_scan_repo, jobs):
                _done(res)
    meta = {"arbiter_commit": _arbiter_commit(), "adapters": args.adapters,
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "budgets_file": str(Path(args.budgets).resolve().relative_to(ROOT))
            if Path(args.budgets).resolve().is_relative_to(ROOT) else args.budgets}
    report = build_report(results, {p.name: p for p in pins}, budgets, missing, meta)
    if args.raw:
        report["raw_findings"] = {r["name"]: r["findings"] for r in results}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "gold-report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (out / "gold-summary.md").write_text(render_markdown(report, args.top), encoding="utf-8")
    print(render_markdown(report, args.top))
    if missing and not args.allow_missing:
        print(f"missing repositories: {missing}", file=sys.stderr)
        return 2
    return 1 if failing_breaches(report["breaches"]) and not args.no_fail else 0


def run_recall(args, pins: list[Pin], benchmarks: dict) -> int:
    name = args.recall
    if name not in benchmarks:
        print(f"unknown benchmark {name}; known: {sorted(benchmarks)}", file=sys.stderr)
        return 2
    cfg = benchmarks[name]
    pin = {p.name: p for p in pins}.get(name)
    repo = Path(args.corpus).expanduser() / name
    if not repo.is_dir() or pin is None:
        print(f"{name} not fetched under {repo}", file=sys.stderr)
        return 2
    head = _head(repo)
    if head != pin.sha and not args.allow_unpinned:
        print(f"{name} HEAD {head[:12]} is not the pinned {pin.sha[:12]}", file=sys.stderr)
        return 2
    expected = read_expected(repo / cfg["expected_results"])
    started = time.time()
    findings = scan(repo / cfg["scan_root"], args.adapters) if cfg.get("scan_root") else scan(repo, args.adapters)
    test_dir = cfg["test_dir"]
    if cfg.get("scan_root"):
        test_dir = Path(test_dir).relative_to(cfg["scan_root"]).as_posix()
    rep = score_recall(expected, findings, cfg.get("cwe_aliases", {}), test_dir,
                       claimed_cwes(cfg["language"]))
    rep.update({"schema_version": SCHEMA_VERSION, "kind": "recall", "benchmark": name,
                "arbiter_commit": _arbiter_commit(), "adapters": args.adapters,
                "seconds": round(time.time() - started, 1),
                "source": {"url": pin.url, "sha": pin.sha, "head": head,
                           "license": cfg.get("license", "")},
                "cases": len(expected)})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"recall-{name}.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    md = render_recall_markdown(name, rep)
    (out / f"recall-{name}.md").write_text(md, encoding="utf-8")
    print(md)
    floor = cfg.get("min_recall")
    if floor is not None and (rep["in_scope"]["recall"] or 0) < floor and not args.no_fail:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", default="~/corpus-gold",
                    help="where `GOLD_ONLY=1 tools/fetch_corpus.sh DIR` put the gold repos")
    ap.add_argument("--out", default=str(ROOT / "arbiter-out" / "gold"))
    ap.add_argument("--budgets", default=str(DATA / "budgets.json"))
    ap.add_argument("--benchmarks", default=str(DATA / "benchmarks.json"))
    ap.add_argument("--fetch-script", default=str(FETCH_SCRIPT))
    ap.add_argument("--only", help="comma-separated repository names")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--adapters", action="store_true", help="also run semgrep, bandit, gitleaks")
    ap.add_argument("--recall", metavar="BENCHMARK", help="score recall on a labelled benchmark")
    ap.add_argument("--raw", action="store_true", help="keep every finding in the JSON report")
    ap.add_argument("--no-fail", action="store_true", help="exit 0 even when a budget is exceeded")
    ap.add_argument("--allow-missing", action="store_true")
    ap.add_argument("--allow-unpinned", action="store_true",
                    help="scan a checkout that is not at its pinned commit (numbers will not reproduce)")
    args = ap.parse_args(argv)
    if args.adapters:
        from arbiter.adapters import register_adapters
        register_adapters()
    pins = parse_pins(Path(args.fetch_script).read_text(encoding="utf-8"))
    benchmarks = load_json(Path(args.benchmarks))
    if args.recall:
        return run_recall(args, pins, benchmarks)
    return run_precision(args, pins, set(benchmarks))


if __name__ == "__main__":
    raise SystemExit(main())
