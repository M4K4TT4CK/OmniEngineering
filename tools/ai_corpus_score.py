#!/usr/bin/env python3
"""Score Arbiter on AI-written apps against hand labels: recall, precision, misses.

    python tools/ai_corpus_score.py                     # every app, native probes
    python tools/ai_corpus_score.py --only go,python/notes-api
    python tools/ai_corpus_score.py --json out.json --markdown out.md
    python tools/ai_corpus_score.py --adapters          # with semgrep, bandit, ...

The corpus is fixtures/ai-corpus/<lang>/<task>/: `app/` is what a model
wrote when asked to build the task (see manifest.json for the prompt, model
and date), and `labels.json` beside it is the defects a reviewer found by
reading `app/`. The planted corpus asks "does the rule fire on the defect
its author wrote for it"; this asks "on code a model actually wrote for an
interview-style task, how much of what is wrong does Arbiter report, and how
much of what it reports is wrong".

labels.json:

    {"app": "go/job-queue",
     "defects": [{"id": "D1", "file": "main.go", "line": 40, "end_line": 42,
                  "cwe": ["CWE-400"], "category": "unbounded-request-body",
                  "severity": "medium", "justification": "...",
                  "source": "independent",          # or "reconcile"
                  "match_rules": ["arbiter/go.x"]}], # optional, see below
     "false_alarms": [{"rule": "arbiter/go.y", "file": "main.go", "line": 9,
                       "reason": "..."}]}

A finding detects a defect when it is in the same file, its line is within
`--tolerance` lines of the defect's [line, end_line] span, and either it
carries one of the defect's CWEs or its rule matches one of `match_rules`
(fnmatch globs). `match_rules` is written when reconciling, for a rule that
reports the defect under a neighbouring CWE; it records a judgement, so each
one should be explainable from the defect's justification.

`source: "independent"` labels were written before Arbiter was run on the
app; `"reconcile"` ones were added afterwards because Arbiter reported a real
defect the reader had missed. Recall is reported on both, and the
independent figure is the honest one.

Precision counts every in-scope finding: one that detects a label is a true
positive, one listed in `false_alarms` is a false positive, and one that is
neither is *unreviewed* -- counted as a false positive in the headline
figure and listed, so a rule change that starts firing somewhere new shows
up as work to do rather than as silent noise. Maintainability metrics
(`metric_rule_prefixes` in corpus.json) and info-severity findings are out of
scope: they are measurements, not defect claims.

The apps are data. Nothing here builds, installs or runs them.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CORPUS = ROOT / "fixtures" / "ai-corpus"
DEFAULT_TOLERANCE = 2
SEVERITIES = ("critical", "high", "medium", "low", "info")


@dataclass
class AppScore:
    app: str
    language: str
    defects: list[dict] = field(default_factory=list)
    detected: list[dict] = field(default_factory=list)       # defect + "by"
    missed: list[dict] = field(default_factory=list)
    true_positives: list[dict] = field(default_factory=list)  # findings
    false_alarms: list[dict] = field(default_factory=list)
    unreviewed: list[dict] = field(default_factory=list)
    out_of_scope: int = 0
    stale_false_alarms: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loading -- pure, tested without a scan
# ---------------------------------------------------------------------------

def load_config(corpus: Path) -> dict:
    p = corpus / "corpus.json"
    cfg = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    cfg.setdefault("metric_rule_prefixes", ["arbiter/ast.", "arbiter/quality."])
    return cfg


def apps(corpus: Path, only: list[str] | None = None) -> list[Path]:
    """Every <lang>/<task> directory holding an app/ and a labels.json."""
    out = sorted(p.parent for p in corpus.glob("*/*/labels.json") if (p.parent / "app").is_dir())
    if only:
        out = [a for a in out if any(_selected(a.relative_to(corpus).as_posix(), o) for o in only)]
    return out


def _selected(app: str, sel: str) -> bool:
    return app == sel or app.split("/")[0] == sel


def load_labels(app_dir: Path) -> dict:
    data = json.loads((app_dir / "labels.json").read_text(encoding="utf-8"))
    data.setdefault("defects", [])
    data.setdefault("false_alarms", [])
    return data


def validate_labels(app_dir: Path) -> list[str]:
    """Problems with one labels file: missing files, lines past EOF, bad fields."""
    problems: list[str] = []
    data = load_labels(app_dir)
    seen: set[str] = set()
    for d in data["defects"]:
        where = f"{app_dir.name}:{d.get('id')}"
        for key in ("id", "file", "line", "cwe", "category", "severity", "justification", "source"):
            if key not in d:
                problems.append(f"{where}: missing {key}")
        if d.get("id") in seen:
            problems.append(f"{where}: duplicate id")
        seen.add(d.get("id"))
        if d.get("severity") not in SEVERITIES:
            problems.append(f"{where}: severity {d.get('severity')!r}")
        if d.get("source") not in ("independent", "reconcile"):
            problems.append(f"{where}: source {d.get('source')!r}")
        if not isinstance(d.get("cwe"), list):
            problems.append(f"{where}: cwe must be a list")
        problems += _check_line(app_dir, where, d.get("file"), d.get("line"), d.get("end_line"))
    for fa in data["false_alarms"]:
        where = f"{app_dir.name}:false_alarm {fa.get('rule')}"
        for key in ("rule", "file", "line", "reason"):
            if key not in fa:
                problems.append(f"{where}: missing {key}")
        problems += _check_line(app_dir, where, fa.get("file"), fa.get("line"), None)
    return problems


def _check_line(app_dir: Path, where: str, file, line, end_line) -> list[str]:
    path = app_dir / "app" / str(file)
    if not path.is_file():
        return [f"{where}: no file app/{file}"]
    n = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    if not isinstance(line, int) or not 1 <= line <= max(n, 1):
        return [f"{where}: line {line} outside 1..{n}"]
    if end_line is not None and not (isinstance(end_line, int) and line <= end_line <= n):
        return [f"{where}: end_line {end_line} outside {line}..{n}"]
    return []


# ---------------------------------------------------------------------------
# Matching -- pure
# ---------------------------------------------------------------------------

def in_scope(finding: dict, cfg: dict) -> bool:
    if finding.get("severity") == "info":
        return False
    return not any(finding["rule"].startswith(p) for p in cfg["metric_rule_prefixes"])


def detects(finding: dict, defect: dict, tolerance: int) -> bool:
    if finding["path"] != defect["file"]:
        return False
    lo = defect["line"] - tolerance
    hi = defect.get("end_line", defect["line"]) + tolerance
    f_lo = finding["line"]
    f_hi = max(finding.get("end_line") or f_lo, f_lo)
    if f_hi < lo or f_lo > hi:
        return False
    if set(finding.get("cwe") or ()) & set(defect.get("cwe") or ()):
        return True
    return any(fnmatch.fnmatchcase(finding["rule"], g) for g in defect.get("match_rules", ()))


def _is_listed_false_alarm(finding: dict, fa: dict, tolerance: int) -> bool:
    return (finding["rule"] == fa["rule"] and finding["path"] == fa["file"]
            and abs(finding["line"] - fa["line"]) <= tolerance)


def score_app(app: str, language: str, labels: dict, findings: list[dict], cfg: dict,
              tolerance: int = DEFAULT_TOLERANCE) -> AppScore:
    s = AppScore(app=app, language=language, defects=list(labels["defects"]))
    scoped = []
    for f in findings:
        if in_scope(f, cfg):
            scoped.append(f)
        else:
            s.out_of_scope += 1
    for d in s.defects:
        by = sorted({f["rule"] for f in scoped if detects(f, d, tolerance)})
        (s.detected if by else s.missed).append({**d, "by": by})
    used_fa: set[int] = set()
    for f in scoped:
        if any(detects(f, d, tolerance) for d in s.defects):
            s.true_positives.append(f)
            continue
        hit = next((i for i, fa in enumerate(labels["false_alarms"])
                    if _is_listed_false_alarm(f, fa, tolerance)), None)
        if hit is None:
            s.unreviewed.append(f)
        else:
            used_fa.add(hit)
            s.false_alarms.append({**f, "reason": labels["false_alarms"][hit]["reason"]})
    s.stale_false_alarms = [fa for i, fa in enumerate(labels["false_alarms"]) if i not in used_fa]
    return s


def _ratio(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None


def summarize(scores: list[AppScore]) -> dict:
    """Recall and precision per language and overall."""
    groups: dict[str, list[AppScore]] = defaultdict(list)
    for s in scores:
        groups[s.language].append(s)
    groups["all"] = list(scores)
    out = {}
    for name, group in groups.items():
        defects = [d for s in group for d in s.defects]
        indep = [d for d in defects if d["source"] == "independent"]
        det = [d for s in group for d in s.detected]
        det_indep = [d for d in det if d["source"] == "independent"]
        tp = sum(len(s.true_positives) for s in group)
        fp = sum(len(s.false_alarms) for s in group)
        unrev = sum(len(s.unreviewed) for s in group)
        hi = [d for d in defects if d["severity"] in ("critical", "high")]
        hi_det = [d for d in det if d["severity"] in ("critical", "high")]
        out[name] = {
            "apps": len(group), "defects": len(defects), "independent_defects": len(indep),
            "detected": len(det), "detected_independent": len(det_indep),
            "recall": _ratio(len(det), len(defects)),
            "recall_independent": _ratio(len(det_indep), len(indep)),
            "recall_high_critical": _ratio(len(hi_det), len(hi)),
            "findings_in_scope": tp + fp + unrev, "true_positives": tp,
            "false_alarms": fp, "unreviewed": unrev,
            "precision": _ratio(tp, tp + fp + unrev),
            "precision_reviewed": _ratio(tp, tp + fp),
        }
    return out


def by_category(scores: list[AppScore]) -> list[dict]:
    """Recall per defect category: where the misses concentrate."""
    tally: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for s in scores:
        for d in s.defects:
            tally[d["category"]][1] += 1
        for d in s.detected:
            tally[d["category"]][0] += 1
    rows = [{"category": c, "detected": a, "defects": b, "recall": _ratio(a, b)}
            for c, (a, b) in tally.items()]
    return sorted(rows, key=lambda r: (-r["defects"], r["category"]))


def by_rule(scores: list[AppScore]) -> list[dict]:
    """Precision per rule over the corpus."""
    tally: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for s in scores:
        for f in s.true_positives:
            tally[f["rule"]][0] += 1
        for f in s.false_alarms:
            tally[f["rule"]][1] += 1
        for f in s.unreviewed:
            tally[f["rule"]][2] += 1
    return sorted(({"rule": r, "tp": a, "fp": b, "unreviewed": c, "precision": _ratio(a, a + b + c)}
                   for r, (a, b, c) in tally.items()), key=lambda r: (-(r["tp"] + r["fp"] + r["unreviewed"]), r["rule"]))


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def scan(app_dir: Path, use_adapters: bool = False) -> list[dict]:
    from arbiter.engine import run_scan
    from arbiter.policy import load_config as arbiter_config
    report = run_scan([str(app_dir / "app")], arbiter_config(None), use_adapters=use_adapters,
                      use_cache=False)
    return [{"rule": f.rule_id, "path": f.location.path, "line": f.location.start_line,
             "end_line": f.location.end_line, "cwe": list(f.cwe), "severity": f.severity,
             "title": f.title} for f in report.active()]


def render_markdown(summary: dict, scores: list[AppScore], cats: list[dict], rules: list[dict]) -> str:
    pct = lambda v: "—" if v is None else f"{v * 100:.0f}%"  # noqa: E731
    lines = ["# AI-written corpus score", "",
             "| language | apps | defects | recall | recall (independent) | recall high+crit | findings | TP | FP | unreviewed | precision |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in sorted(summary, key=lambda k: (k == "all", k)):
        r = summary[name]
        lines.append(f"| {name} | {r['apps']} | {r['defects']} | {pct(r['recall'])} | "
                     f"{pct(r['recall_independent'])} | {pct(r['recall_high_critical'])} | "
                     f"{r['findings_in_scope']} | {r['true_positives']} | {r['false_alarms']} | "
                     f"{r['unreviewed']} | {pct(r['precision'])} |")
    lines += ["", "## Recall by category", "", "| category | detected / defects | recall |", "|---|---|---|"]
    lines += [f"| {c['category']} | {c['detected']}/{c['defects']} | {pct(c['recall'])} |" for c in cats]
    lines += ["", "## Precision by rule", "", "| rule | TP | FP | unreviewed | precision |", "|---|---|---|---|---|"]
    lines += [f"| `{r['rule']}` | {r['tp']} | {r['fp']} | {r['unreviewed']} | {pct(r['precision'])} |" for r in rules]
    lines += ["", "## Misses", ""]
    for s in scores:
        for d in s.missed:
            lines.append(f"- {s.app} `{d['file']}:{d['line']}` [{d['severity']}] {d['category']} "
                         f"({', '.join(d['cwe']) or 'no CWE'}): {d['justification']}")
    lines += ["", "## False alarms", ""]
    for s in scores:
        for f in s.false_alarms:
            lines.append(f"- {s.app} `{f['path']}:{f['line']}` `{f['rule']}`: {f['reason']}")
    unrev = [(s.app, f) for s in scores for f in s.unreviewed]
    if unrev:
        lines += ["", "## Unreviewed findings (counted as false positives until labelled)", ""]
        lines += [f"- {a} `{f['path']}:{f['line']}` `{f['rule']}` {f['title']}" for a, f in unrev]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", type=Path, default=CORPUS)
    ap.add_argument("--only", help="comma-separated languages or lang/task")
    ap.add_argument("--tolerance", type=int, default=DEFAULT_TOLERANCE)
    ap.add_argument("--adapters", action="store_true")
    ap.add_argument("--json", type=Path, help="write the full result here")
    ap.add_argument("--markdown", type=Path, help="write the summary here")
    ap.add_argument("--validate", action="store_true", help="check the labels only; no scan")
    args = ap.parse_args(argv)
    if args.adapters:
        from arbiter.adapters import register_adapters
        register_adapters()
    only = args.only.split(",") if args.only else None
    app_dirs = apps(args.corpus, only)
    problems = [p for a in app_dirs for p in validate_labels(a)]
    for p in problems:
        print(f"label problem: {p}", file=sys.stderr)
    if args.validate:
        print(f"{len(app_dirs)} apps, {len(problems)} label problems")
        return 1 if problems else 0
    cfg = load_config(args.corpus)
    scores = []
    for a in app_dirs:
        rel = a.relative_to(args.corpus).as_posix()
        scores.append(score_app(rel, rel.split("/")[0], load_labels(a), scan(a, args.adapters),
                                cfg, args.tolerance))
    summary, cats, rules = summarize(scores), by_category(scores), by_rule(scores)
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "categories": cats, "rules": rules,
                                         "apps": [s.__dict__ for s in scores]}, indent=1) + "\n",
                             encoding="utf-8")
    md = render_markdown(summary, scores, cats, rules)
    if args.markdown:
        args.markdown.write_text(md, encoding="utf-8")
    else:
        print(md)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
