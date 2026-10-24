#!/usr/bin/env python3
"""Measure the model verification pass against labels: agreement and confirmed-set precision.

    # the AI-written corpus (fixtures/ai-corpus/**/labels.json), every severity
    python tools/verify_eval.py --ai-corpus --max-findings 150 --json out.json
    # a human label sheet from tools/label_sample.py (verdict column filled in)
    python tools/verify_eval.py --sheet alice.csv --corpus ~/wt/corpus-gold

The verification pass (src/arbiter/verify.py) asks a model whether each
finding is real. Before its verdict is allowed to promote anything to the
gate-blocking tier, it has to be measured: of the findings the model
CONFIRMS, how many are true by a human label? That number, with its Wilson
95% interval, is the one the 99% target is about. Also reported, per rule
and overall:

  * agreement and Cohen's kappa between the model's decided verdicts
    (confirmed = TP, rejected = FP) and the labels;
  * the TP-rejection rate: real defects the model rejected, which is what
    using the verdict to demote costs in recall;
  * unsure and not-verified counts, which are neither agreement nor error;
  * tokens and an approximate cost.

Labels: on the AI corpus a finding is TP when it detects a labelled defect
and FP when it is a listed false alarm, matched exactly as
tools/ai_corpus_score.py matches them; unreviewed findings are verified but
left out of every ratio. On a sheet, the row's `verdict` column (TP/FP) is the
label; blank or unsure rows are skipped.

Without ANTHROPIC_API_KEY every finding comes back not_verified and the
report says "not measured". Corpus files are untrusted: they are read for
excerpts and nothing else; nothing is built or run.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# claude-opus-5-5 list prices, USD per million tokens (dated 2026-10-06).
PRICE = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0),
         "claude-haiku-5-5": (0.10, 0.50)}
DEFAULT_CACHE = ROOT / "arbiter-out" / "verify-eval" / "verify-cache.json"


def _tool(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Scoring -- pure
# ---------------------------------------------------------------------------

def _empty() -> dict:
    return {"n": 0, "confirmed_tp": 0, "confirmed_fp": 0, "rejected_tp": 0, "rejected_fp": 0,
            "unsure_tp": 0, "unsure_fp": 0, "not_verified": 0}


def _finish(e: dict) -> dict:
    ls = _tool("label_sample")
    conf = e["confirmed_tp"] + e["confirmed_fp"]
    decided = conf + e["rejected_tp"] + e["rejected_fp"]
    agree = e["confirmed_tp"] + e["rejected_fp"]
    tps = e["confirmed_tp"] + e["rejected_tp"] + e["unsure_tp"]
    return {
        **e,
        "decided": decided,
        "agreement": round(agree / decided, 3) if decided else None,
        "confirmed_precision": round(e["confirmed_tp"] / conf, 3) if conf else None,
        "confirmed_precision_ci95": ls.wilson(e["confirmed_tp"], conf),
        "tp_rejected_rate": round(e["rejected_tp"] / tps, 3) if tps else None,
        "tp_rejected_ci95": ls.wilson(e["rejected_tp"], tps),
    }


def score(pairs: list[dict]) -> dict:
    """pairs: [{"rule", "label": "TP"|"FP", "verdict"}]. Per-rule and overall
    agreement, kappa over decided pairs, confirmed-set precision with Wilson
    95%, and the TP-rejection rate."""
    ls = _tool("label_sample")
    per: dict[str, dict] = defaultdict(_empty)
    total = _empty()
    model_side, label_side = [], []
    by_rule_pairs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for p in pairs:
        label, v = p["label"], p.get("verdict") or "not_verified"
        if label not in ("TP", "FP"):
            continue
        for e in (per[p["rule"]], total):
            e["n"] += 1
            if v in ("confirmed", "rejected", "unsure"):
                e[f"{v}_{label.lower()}"] += 1
            else:
                e["not_verified"] += 1
        if v in ("confirmed", "rejected"):
            m = "TP" if v == "confirmed" else "FP"
            model_side.append(m)
            label_side.append(label)
            by_rule_pairs[p["rule"]].append((m, label))
    rules = {}
    for rule, e in sorted(per.items()):
        rp = by_rule_pairs.get(rule, [])
        rules[rule] = {**_finish(e), "kappa": ls.cohens_kappa([a for a, _ in rp], [b for _, b in rp])}
    overall = {**_finish(total), "kappa": ls.cohens_kappa(model_side, label_side)}
    return {"overall": overall, "rules": rules}


def cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = PRICE.get(model)
    if price is None:
        return None
    return round(input_tokens / 1e6 * price[0] + output_tokens / 1e6 * price[1], 4)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def _config(args) -> dict:
    from arbiter.policy import load_config
    cfg = load_config(None)
    cfg["verify"] = {**cfg.get("verify", {}), "enabled": True,
                     "min_severity": args.min_severity, "cache_path": str(args.cache)}
    if args.model:
        cfg["verify"]["model"] = args.model
    if args.effort:
        cfg["verify"]["effort"] = args.effort
    return cfg


def _add_usage(totals: dict, s: dict) -> None:
    for k in ("calls", "cache_hits", "input_tokens", "output_tokens", "candidates"):
        totals[k] = totals.get(k, 0) + int(s.get(k) or 0)
    if s.get("reason"):
        totals.setdefault("reasons", set()).add(s["reason"])
    totals["model"] = s.get("model", totals.get("model"))


def ai_corpus_pairs(args) -> tuple[list[dict], dict]:
    """Scan each labelled app with verification on; label each verified
    finding TP / FP / unreviewed as ai_corpus_score.py does."""
    acs = _tool("ai_corpus_score")
    from arbiter.engine import run_scan
    corpus_cfg = acs.load_config(args.corpus)
    hygiene = set(corpus_cfg.get("hygiene_categories", []))
    cfg = _config(args)
    remaining = args.max_findings
    pairs: list[dict] = []
    totals: dict = {"unreviewed": 0}
    for app_dir in acs.apps(args.corpus, args.only.split(",") if args.only else None):
        if remaining <= 0:
            break
        cfg["verify"]["max_findings"] = remaining
        rep = run_scan([str(app_dir / "app")], cfg, profile="assisted", use_adapters=False,
                       use_cache=False, model_verify=True)
        s = rep.scan_scope.get("verification") or {}
        remaining -= int(s.get("calls") or 0)
        _add_usage(totals, s)
        labels = acs.load_labels(app_dir)
        defects = [d for d in labels["defects"]
                   if not (args.code_only and d["category"] in hygiene)]
        app = app_dir.relative_to(args.corpus).as_posix()
        for f in rep.active():
            if not f.verification:
                continue
            fd = {"rule": f.rule_id, "path": f.location.path, "line": f.location.start_line,
                  "end_line": f.location.end_line, "cwe": list(f.cwe)}
            if not acs.in_scope(fd, corpus_cfg):
                continue
            if any(acs.detects(fd, d, args.tolerance, corpus_cfg) for d in defects):
                label = "TP"
            elif any(acs._is_listed_false_alarm(fd, fa, args.tolerance) for fa in labels["false_alarms"]):
                label = "FP"
            else:
                totals["unreviewed"] += 1
                continue
            pairs.append({"app": app, "rule": f.rule_id, "path": f.location.path,
                          "line": f.location.start_line, "label": label, **_verdict_fields(f)})
    return pairs, totals


def _verdict_fields(f) -> dict:
    v = f.verification or {}
    return {"verdict": v.get("verdict", "not_verified"), "reason": v.get("reason", ""),
            "cached": bool(v.get("cached"))}


def sheet_pairs(args) -> tuple[list[dict], dict]:
    """Verify each labelled row of a label_sample.py sheet against its file in
    --corpus. The sheet carries the rule id and title, not the rule's
    description, so the prompt is thinner than in a scan; agreement on sheet
    rows can understate what the scan-time pass achieves."""
    ls = _tool("label_sample")
    from arbiter import verify
    from arbiter.core import Finding, Location
    from arbiter.inventory import LANG_BY_EXT, FileInfo, Inventory
    cfg = _config(args)
    cfg["verify"]["min_severity"] = "info"
    cfg["verify"]["skip_rules"] = []
    rows = [r for path in args.sheet for r in ls.read_sheet(path) if ls.verdict(r)]
    findings, labels = [], []
    inv = Inventory()
    seen: set[tuple[str, str]] = set()
    for r in rows:
        repo, path = str(r.get("repo") or "root"), str(r["path"])
        f = Finding(rule_id=r["rule"], title=r.get("title") or r["rule"],
                    severity=r.get("severity") or "medium", repo_id=repo,
                    location=Location(path=path, start_line=int(r.get("line") or 0)),
                    evidence=f"sheet:{r.get('sample_id')}")
        findings.append(f)
        labels.append(ls.verdict(r))
        if (repo, path) not in seen and args.corpus:
            seen.add((repo, path))
            abspath = args.corpus / repo / path
            if abspath.is_file():
                fi = FileInfo(path=path, abspath=str(abspath), repo_id=repo,
                              language=LANG_BY_EXT.get(abspath.suffix, "unknown"))
                inv.files.append(fi)
                inv.by_repo.setdefault(repo, []).append(fi)
    cfg["verify"]["max_findings"] = args.max_findings
    s = verify.run(findings, inv, cfg)
    totals: dict = {"unreviewed": 0}
    _add_usage(totals, s)
    pairs = [{"rule": f.rule_id, "path": f.location.path, "line": f.location.start_line,
              "label": lab, **_verdict_fields(f)} for f, lab in zip(findings, labels)]
    return pairs, totals


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _pct(v) -> str:
    return "-" if v is None else f"{v * 100:.0f}%"


def _ci(ci) -> str:
    return "-" if not ci else f"{ci[0]:.2f}-{ci[1]:.2f}"


def render(result: dict) -> str:
    o = result["score"]["overall"]
    t = result["usage"]
    L = [f"# Model verification vs labels ({result['source']})", ""]
    if not t.get("calls") and not t.get("cache_hits"):
        L.append(f"**Not measured**: no verdicts were produced ({'; '.join(t.get('reasons') or []) or 'no candidates'}).")
        L.append("")
    L.append(f"model `{t.get('model')}`, prompt `{result['prompt_version']}`; "
             f"{t.get('calls', 0)} call(s), {t.get('cache_hits', 0)} cached, "
             f"{t.get('input_tokens', 0):,} input + {t.get('output_tokens', 0):,} output tokens"
             + (f", about ${result['cost_usd']:.2f}" if result.get("cost_usd") is not None else ""))
    L.append(f"labelled findings {o['n']} (unreviewed, left out: {t.get('unreviewed', 0)})")
    L.append("")
    L.append(f"- confirmed-set precision **{_pct(o['confirmed_precision'])}** "
             f"(95% CI {_ci(o['confirmed_precision_ci95'])}; {o['confirmed_tp']} TP / "
             f"{o['confirmed_fp']} FP among confirmed)")
    L.append(f"- agreement {_pct(o['agreement'])} over {o['decided']} decided, kappa {o['kappa']}")
    L.append(f"- TP rejected {_pct(o['tp_rejected_rate'])} (95% CI {_ci(o['tp_rejected_ci95'])})")
    L.append(f"- unsure {o['unsure_tp'] + o['unsure_fp']} ({o['unsure_tp']} TP, {o['unsure_fp']} FP), "
             f"not verified {o['not_verified']}")
    L.append("")
    L.append("| rule | n | conf TP | conf FP | rej TP | rej FP | unsure | n/v | conf. precision (95% CI) | agreement | kappa |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for rule, e in result["score"]["rules"].items():
        L.append(f"| `{rule}` | {e['n']} | {e['confirmed_tp']} | {e['confirmed_fp']} | {e['rejected_tp']} | "
                 f"{e['rejected_fp']} | {e['unsure_tp'] + e['unsure_fp']} | {e['not_verified']} | "
                 f"{_pct(e['confirmed_precision'])} ({_ci(e['confirmed_precision_ci95'])}) | "
                 f"{_pct(e['agreement'])} | {e['kappa']} |")
    disagree = [p for p in result["pairs"]
                if (p["verdict"], p["label"]) in (("confirmed", "FP"), ("rejected", "TP"))]
    if disagree:
        L += ["", "## Disagreements", ""]
        for p in disagree:
            L.append(f"- {p.get('app', '')} `{p['path']}:{p['line']}` `{p['rule']}`: model "
                     f"{p['verdict']}, label {p['label']} -- {p['reason']}")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    acs = _tool("ai_corpus_score")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--ai-corpus", action="store_true", help="the AI-written corpus (default)")
    src.add_argument("--sheet", type=Path, action="append", help="label_sample.py sheet(s), CSV or JSON")
    ap.add_argument("--corpus", type=Path, default=None,
                    help="AI corpus root (default fixtures/ai-corpus), or the gold corpus root for --sheet")
    ap.add_argument("--only", help="AI corpus: comma-separated languages or lang/task")
    ap.add_argument("--code-only", action="store_true",
                    help="AI corpus: leave out dependency-hygiene labels (corpus.json hygiene_categories)")
    ap.add_argument("--tolerance", type=int, default=acs.DEFAULT_TOLERANCE)
    ap.add_argument("--min-severity", default="info",
                    help="verify findings at or above this (default info: every labelled finding)")
    ap.add_argument("--max-findings", type=int, default=150, help="model calls across the whole run")
    ap.add_argument("--model", help="override verify.model")
    ap.add_argument("--effort", help="override verify.effort")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="verdict cache file")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--markdown", type=Path)
    args = ap.parse_args(argv)
    if args.sheet:
        pairs, usage = sheet_pairs(args)
        source = ", ".join(str(p) for p in args.sheet)
    else:
        args.corpus = args.corpus or acs.CORPUS
        pairs, usage = ai_corpus_pairs(args)
        source = "fixtures/ai-corpus" + (" (code only)" if args.code_only else "")
    from arbiter.verify import PROMPT_VERSION
    usage["reasons"] = sorted(usage.get("reasons") or [])
    result = {"source": source, "prompt_version": PROMPT_VERSION, "usage": usage,
              "cost_usd": cost(usage.get("model") or "", usage.get("input_tokens", 0),
                               usage.get("output_tokens", 0)),
              "score": score(pairs), "pairs": pairs}
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    md = render(result)
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
