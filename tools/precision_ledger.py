#!/usr/bin/env python3
"""Build the per-rule precision ledger from label files.

    # JSON-lines labels (docs/precision.md, "Label format"), from the model
    # labellers, external ground truth, or both:
    python tools/precision_ledger.py build labels/*.jsonl

    # label_sample.py sheets (CSV or JSON), one per labeller; the sheet has
    # no finding id, source or split, so they are given here:
    python tools/precision_ledger.py build --sheet-batch gold-2026-10 \\
        --sheet-source gold --sheet-held-out alice.csv bob.csv

    # What the shipped ledger is built from (current-version model labels;
    # superseded ones live under labels/history/ and are dropped anyway):
    python tools/precision_ledger.py build tools/gold/labels/*.jsonl tools/gold/labels/*.jsonl.gz

    python tools/precision_ledger.py show            # the ledger as a table
    python tools/precision_ledger.py check           # written ledger == rebuilt counts

The ledger is rebuilt from the label files every time, never edited by hand:
the label files are the evidence and the ledger is arithmetic on them. Two
things carry over from the previous ledger, because a label file does not
hold them: each rule's `fp_notes` (merged with the notes on FP labels), and
`updated` / `snapshot_counts` for a rule whose counts did not change, so the
CI precision guard can tell a re-label from a rebuild.

It writes tools/gold/precision.json and the copy shipped in the package
(src/arbiter/packs/precision.json); a test holds the two equal.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arbiter import precision as P  # noqa: E402

LEDGER = ROOT / "tools" / "gold" / "precision.json"
PACKAGED = ROOT / "src" / "arbiter" / "packs" / "precision.json"
SNAPSHOTS = ROOT / "tools" / "gold" / "snapshots"
SNAPSHOT_TARGETS = ("planted", "ai-corpus")
LABELS = {"tp": "tp", "fp": "fp", "unsure": "unsure", "true": "tp", "false": "fp",
          "y": "tp", "n": "fp", "": "unsure"}
ABOUT = ("Per-rule precision ledger (docs/precision.md). Built by tools/precision_ledger.py "
         "from label files; do not edit by hand. A rule is admitted when its Wilson 95% lower "
         "bound reaches bar.rule_lower on at least bar.rule_min_n TP+FP labels, with labeller "
         "kappa at least bar.kappa_min where two labellers looked. `tier` is the claim: verified-"
         "tier precision on held-out labels only, stratified by (rule, source, language) and "
         "weighted by each stratum's population, overall and per category.")


# ---------------------------------------------------------------------------
# Reading labels
# ---------------------------------------------------------------------------

def norm_label(v) -> str:
    out = LABELS.get(str(v if v is not None else "").strip().lower())
    if out is None:
        raise ValueError(f"label must be tp, fp or unsure, got {v!r}")
    return out


def read_jsonl(path: Path) -> list[dict]:
    out = []
    if path.name.endswith(".gz"):
        import gzip
        text = gzip.decompress(path.read_bytes()).decode("utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            rec = json.loads(line)
        except ValueError as exc:
            raise SystemExit(f"{path}:{i}: not JSON: {exc}") from exc
        missing = [k for k in ("finding_id", "rule_id", "label", "labeller", "source", "held_out")
                   if k not in rec]
        if missing:
            raise SystemExit(f"{path}:{i}: missing {', '.join(missing)}")
        if not isinstance(rec["held_out"], bool):
            raise SystemExit(f"{path}:{i}: held_out is true or false")
        try:
            rec["label"] = norm_label(rec["label"])
        except ValueError as exc:
            raise SystemExit(f"{path}:{i}: {exc}") from exc
        rec.setdefault("role", "labeller")
        if rec["role"] not in ("labeller", "adjudicator", "ground_truth"):
            raise SystemExit(f"{path}:{i}: role is labeller, adjudicator or ground_truth")
        out.append(rec)
    return out


def drop_calibration(records: list[dict]) -> tuple[list[dict], int]:
    """Calibration records (`source` starting `calibration:`) relabel
    ground-truth items blind, or label planted good-twin lines no rule fired
    on. Counted in the ledger they would double-count ground truth or invent
    findings, so `labels/*.jsonl` is safe to pass whole."""
    keep = [r for r in records if not str(r.get("source", "")).startswith("calibration:")]
    return keep, len(records) - len(keep)


_MODEL_LABELLER = re.compile(r":prosecutor\+defender(?:\+adjudicator)?/(v\d+)$")


def current_model_prompt() -> str:
    """The model labellers' prompt version (`tools/label_models.py`)."""
    m = re.search(r'^PROMPT_VERSION = "([^"]+)"', (ROOT / "tools" / "label_models.py").read_text(encoding="utf-8"),
                  re.M)
    return m.group(1) if m else ""


def drop_superseded_model_labels(records: list[dict], current: str) -> tuple[list[dict], int]:
    """Model labels (`tools/label_models.py`, labeller `<model>:prosecutor+
    defender[+adjudicator]/<version>`) made with an older prompt than
    `current`. Each prompt version is a different labeller with its own
    calibration, and a finding labelled by two versions would be counted
    under whichever came last, so the ledger reads one version only. The
    superseded files are kept under `tools/gold/labels/history/`. Ground
    truth, sheets and other labellers pass through."""
    if not current:
        return records, 0
    keep = []
    for r in records:
        m = _MODEL_LABELLER.search(str(r.get("labeller", "")))
        if m and m.group(1) != current:
            continue
        keep.append(r)
    return keep, len(records) - len(keep)


def read_sheet(path: Path, batch: str, source: str, held_out: bool) -> list[dict]:
    """A label_sample.py sheet as label records. The finding is the sheet's
    sample id within `batch` (every copy of one sample shares its ids)."""
    if path.suffix == ".json":
        rows = json.loads(path.read_text(encoding="utf-8"))
    else:
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        try:
            label = norm_label(r.get("verdict"))
        except ValueError:
            label = "unsure"
        out.append({"finding_id": f"{batch}:{r['sample_id']}", "rule_id": r["rule"], "label": label,
                    "labeller": (r.get("labeller") or "").strip() or path.stem, "source": source,
                    "held_out": held_out, "role": "labeller", "note": r.get("notes") or "",
                    "language": r.get("language") or "",
                    # A sheet is one split, so its stratum's held-out size is
                    # all of it or none of it. A blank size stays unknown.
                    **({"stratum_size": int(r["stratum_size"]),
                        "stratum_held_out_size": int(r["stratum_size"]) if held_out else 0}
                       if str(r.get("stratum_size") or "").strip() else {})})
    return out


# ---------------------------------------------------------------------------
# Arithmetic -- pure
# ---------------------------------------------------------------------------

_ROLE_ORDER = {"ground_truth": 0, "adjudicator": 1, "labeller": 2}


def _size(v) -> int | None:
    return None if v is None or v == "" else int(v)


def finding_stratum(recs: list[dict]) -> dict:
    """The sampling stratum one finding was drawn from: its source and
    language (the rule is the finding's own), and the population sizes its
    label record carries (docs/precision.md, "Population weights"). Taken
    from the first record, ground truth first, that carries a size; when
    none does, the sizes are None: unknown, never the label count."""
    ordered = sorted(recs, key=lambda r: _ROLE_ORDER.get(r.get("role", "labeller"), 3))
    r = next((x for x in ordered if _size(x.get("stratum_size")) is not None), ordered[0])
    size = _size(r.get("stratum_size"))
    held = _size(r.get("stratum_held_out_size"))
    return {"source": r["source"], "language": r.get("language") or "", "size": size,
            "held_out_size": held if size is not None else None,
            "rule_size": _size(r.get("rule_population")) if size is not None else None,
            "rule_held_out_size": _size(r.get("rule_held_out_population")) if size is not None else None}


def final_verdicts(records: list[dict]) -> dict[str, dict]:
    """finding_id -> {rule_id, label, held_out, source, labellers, pair}.

    Ground truth wins; then an adjudicator's label; then the labellers'
    labels when every tp/fp among them agrees. A disagreement nobody has
    adjudicated is `unsure`: it counts toward neither TP nor FP. `pair` is
    the first two labellers' tp/fp labels (by name), for kappa."""
    by: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by[r["finding_id"]].append(r)
    out = {}
    for fid, recs in by.items():
        rules = {r["rule_id"] for r in recs}
        if len(rules) > 1:
            raise SystemExit(f"finding {fid} is labelled under more than one rule: {sorted(rules)}")
        truth = [r for r in recs if r["role"] == "ground_truth"]
        adj = [r for r in recs if r["role"] == "adjudicator"]
        lab = sorted((r for r in recs if r["role"] == "labeller"), key=lambda r: r["labeller"])
        decided = [r["label"] for r in lab if r["label"] != "unsure"]
        if truth:
            label = truth[-1]["label"]
        elif adj:
            label = adj[-1]["label"]
        elif decided and len(set(decided)) == 1:
            label = decided[0]
        else:
            label = "unsure"
        seen: dict[str, str] = {}
        for r in lab:
            if r["label"] != "unsure" and r["labeller"] not in seen:
                seen[r["labeller"]] = r["label"]
        pair = tuple(list(seen.values())[:2]) if len(seen) >= 2 else None
        out[fid] = {"rule_id": rules.pop(), "label": label,
                    # A finding is held out only if every record says so.
                    "held_out": all(r["held_out"] for r in recs),
                    "source": recs[0]["source"], "labellers": sorted({r["labeller"] for r in recs}),
                    "sources": sorted({r["source"] for r in recs}),
                    "dates": sorted(r["date"] for r in recs if r.get("date")),
                    "notes": [r["note"] for r in recs if r.get("note") and r["label"] == "fp"],
                    "stratum": finding_stratum(recs),
                    "dimension": next((r["dimension"] for r in recs if r.get("dimension")), ""),
                    "cwe": sorted({c for r in recs for c in (r.get("cwe") or [])}),
                    "pair": pair}
    return out


def _round(x):
    return None if x is None else round(x, 4)


def _new_stratum(source: str, language: str) -> dict:
    return {"source": source, "language": language, "population": None, "held_out_population": None,
            "unknown": 0, "tp": 0, "fp": 0, "unsure": 0, "held_out": {"tp": 0, "fp": 0, "unsure": 0}}


def _agree_size(rule: str, where: str, old, new):
    if old is not None and new is not None and old != new:
        raise SystemExit(f"{rule} {where}: label records give two population sizes ({old} and {new}); "
                         "labels drawn from different runs need different `source` names")
    return new if old is None else old


def rule_strata(rule: str, verdicts: list[dict]) -> tuple[list[dict], dict]:
    """A rule's strata (source, language) with their populations and label
    counts, and the rule's population per source. A stratum any of whose
    labels lacks a recorded size is unknown as a whole (population None)."""
    strata: dict[tuple, dict] = {}
    sources: dict[str, dict] = {}
    for v in verdicts:
        st = v["stratum"]
        key = (st["source"], st["language"])
        e = strata.setdefault(key, _new_stratum(*key))
        e[v["label"]] += 1
        if v["held_out"]:
            e["held_out"][v["label"]] += 1
        if st["size"] is None:
            e["unknown"] += 1
            continue
        where = f"stratum {key[0]}/{key[1] or '-'}"
        e["population"] = _agree_size(rule, where, e["population"], st["size"])
        e["held_out_population"] = _agree_size(rule, where + " (held out)", e["held_out_population"],
                                               st["held_out_size"])
        src = sources.setdefault(st["source"], {"population": None, "held_out_population": None})
        src["population"] = _agree_size(rule, f"source {st['source']}", src["population"], st["rule_size"])
        src["held_out_population"] = _agree_size(rule, f"source {st['source']} (held out)",
                                                 src["held_out_population"], st["rule_held_out_size"])
    out = []
    for key in sorted(strata):
        e = strata[key]
        if e.pop("unknown"):
            e["population"] = e["held_out_population"] = None
        out.append(e)
    return out, sources


def _weighted(strata: list[dict], held_out: bool) -> dict | None:
    rows = [{"key": f"{s['source']}/{s['language'] or '-'}",
             "tp": s["held_out"]["tp"] if held_out else s["tp"],
             "fp": s["held_out"]["fp"] if held_out else s["fp"],
             "population": s["held_out_population"] if held_out else s["population"]} for s in strata]
    return P.weighted_precision(rows)


def rule_entries(verdicts: dict[str, dict], bar: dict) -> dict[str, dict]:
    from arbiter.rule_meta import meta
    per: dict[str, dict] = {}
    for v in verdicts.values():
        e = per.setdefault(v["rule_id"], {
            "tp": 0, "fp": 0, "unsure": 0, "held_out": {"tp": 0, "fp": 0, "unsure": 0},
            "labellers": set(), "sources": set(), "dates": [], "pairs": [], "notes": [],
            "verdicts": [], "dimension": v["dimension"], "cwe": set(v["cwe"])})
        e[v["label"]] += 1
        if v["held_out"]:
            e["held_out"][v["label"]] += 1
        e["labellers"].update(v["labellers"])
        e["sources"].update(v["sources"])
        e["dates"] += v["dates"]
        e["notes"] += v["notes"]
        e["cwe"].update(v["cwe"])
        e["verdicts"].append(v)
        if v["pair"]:
            e["pairs"].append(v["pair"])
    out = {}
    for rule, e in sorted(per.items()):
        n = e["tp"] + e["fp"]
        ci = P.wilson(e["tp"], n)
        kappa = P.cohens_kappa([a for a, _ in e["pairs"]], [b for _, b in e["pairs"]])
        agree = (sum(a == b for a, b in e["pairs"]) / len(e["pairs"])) if e["pairs"] else None
        strata, sources = rule_strata(rule, e["verdicts"])
        unknown = sum(s["tp"] + s["fp"] + s["unsure"] for s in strata if s["population"] is None)
        m = meta(rule) or {}
        entry = {
            "tp": e["tp"], "fp": e["fp"], "unsure": e["unsure"], "n": n,
            "held_out": e["held_out"],
            "precision": _round(e["tp"] / n) if n else None,
            "wilson_lower": _round(ci[0]) if ci else None,
            "wilson_upper": _round(ci[1]) if ci else None,
            "kappa": _round(kappa), "kappa_pairs": len(e["pairs"]),
            "observed_agreement": _round(agree),
            "labellers": sorted(e["labellers"]), "sources": sorted(e["sources"]),
            "dates": {"first": e["dates"][0] if e["dates"] else "",
                      "last": e["dates"][-1] if e["dates"] else ""} if e["dates"] else {},
            # Findings of the rule in the runs its labels were drawn from; None
            # when any label's population is unknown (`population_unknown`).
            "population": None if unknown else sum(s["population"] for s in strata),
            "held_out_population": None if unknown or any(s["held_out_population"] is None for s in strata)
            else sum(s["held_out_population"] for s in strata),
            "population_unknown": unknown,
            "strata": strata,
            "source_population": dict(sorted(sources.items())),
            "weighted": None if unknown else _weighted(strata, held_out=False),
            "dimension": e["dimension"] or m.get("dimension", ""),
            "cwe": sorted(e["cwe"] | set(m.get("cwe") or [])),
            "fp_notes": list(dict.fromkeys(e["notes"])),
        }
        entry["admitted"], entry["admitted_reason"] = P.admission(entry, bar)
        out[rule] = entry
    return out


def stratified_claim(rules: dict[str, dict], names: list[str], bar: dict) -> dict:
    """The held-out, population-weighted precision over the strata of
    `names` (docs/precision.md, "Tier claim"). Unknown populations make it
    None and say which rules; held-out findings in strata with no decided
    held-out label count against the bound, as if every one were an FP."""
    unknown = sorted(r for r in names if rules[r].get("population_unknown")
                     or rules[r].get("held_out_population") is None)
    if unknown:
        return {"estimate": None, "claim": False, "population_unknown": unknown}
    rows, total = [], 0
    for r in names:
        strata = rules[r].get("strata") or []
        sources = rules[r].get("source_population") or {}
        for s in strata:
            if s["held_out"]["tp"] + s["held_out"]["fp"]:
                rows.append({"key": f"{r}|{s['source']}/{s['language'] or '-'}", "tp": s["held_out"]["tp"],
                             "fp": s["held_out"]["fp"], "population": s["held_out_population"]})
        for src in {s["source"] for s in strata}:
            listed = sum(s["held_out_population"] for s in strata if s["source"] == src)
            total += max(listed, (sources.get(src) or {}).get("held_out_population") or 0)
    w = P.weighted_precision(rows)
    if w is None:
        return {"estimate": None, "claim": False, "population_unknown": []}
    labelled = w["population"]
    coverage = labelled / total if total else 1.0
    lower = round(w["wilson_lower"] * coverage, 4)
    est = {**w, "held_out_population": total, "unlabelled_population": total - labelled,
           "coverage": round(coverage, 4), "claim_lower": lower}
    return {"estimate": est, "claim": lower >= bar["tier_lower"], "population_unknown": []}


def tier_claim(rules: dict[str, dict], bar: dict, verified: set[str]) -> dict:
    """The held-out claim, over the rules that are verified (declared and
    admitted), overall and per category (dimension and each CWE). A
    category's 99% claim is made only where its own lower bound reaches it."""
    names = sorted(r for r in verified if r in rules and rules[r]["admitted"])
    whole = stratified_claim(rules, names, bar)
    cats: dict[str, list[str]] = defaultdict(list)
    for r in names:
        if rules[r]["dimension"]:
            cats[rules[r]["dimension"]].append(r)
        for c in rules[r]["cwe"]:
            cats[c].append(r)
    categories = {}
    for c, members in sorted(cats.items()):
        sc = stratified_claim(rules, members, bar)
        categories[c] = {**(sc["estimate"] or {"precision": None, "wilson_lower": None, "claim_lower": None}),
                         "rule_ids": members, "claim": sc["claim"],
                         "population_unknown": sc["population_unknown"]}
    return {"held_out_only": True, "method": "stratified", "rules": names,
            "overall": whole["estimate"], "claim": whole["claim"],
            "population_unknown": whole["population_unknown"],
            "categories": categories}


def snapshot_counts() -> dict[str, dict[str, int]]:
    out = {}
    for t in SNAPSHOT_TARGETS:
        p = SNAPSHOTS / f"{t}.json"
        if p.is_file():
            out[t] = json.loads(p.read_text(encoding="utf-8")).get("by_rule", {})
    return out


def _counts(e: dict) -> tuple:
    """What makes a rebuild a re-label: the counts, and who labelled (a new
    labeller prompt version is a re-label even when the counts come out the
    same)."""
    return (e["tp"], e["fp"], e["unsure"], tuple(sorted(e["held_out"].items())),
            tuple(sorted(e.get("labellers") or [])))


def build(records: list[dict], previous: dict | None, today: str, verified: set[str],
          snapshots: dict[str, dict[str, int]] | None = None) -> dict:
    prev = (previous or {}).get("rules") or {}
    bar = {**P.DEFAULT_BAR, **((previous or {}).get("bar") or {})}
    rules = rule_entries(final_verdicts(records), bar)
    snapshots = snapshots or {}
    for rule, e in rules.items():
        old = prev.get(rule) or {}
        e["fp_notes"] = list(dict.fromkeys([*(old.get("fp_notes") or []), *e["fp_notes"]]))
        if "tp" in old and isinstance(old.get("held_out"), dict) and _counts(old) == _counts(e):
            e["updated"] = old.get("updated", today)
            e["snapshot_counts"] = old.get("snapshot_counts", {})
        else:
            e["updated"] = today
            e["snapshot_counts"] = {t: int(c.get(rule, 0)) for t, c in snapshots.items()}
    # A rule with notes but no labels keeps its notes: the verify pass reads them.
    for rule, old in prev.items():
        if rule not in rules and old.get("fp_notes"):
            rules[rule] = {"tp": 0, "fp": 0, "unsure": 0, "n": 0,
                           "held_out": {"tp": 0, "fp": 0, "unsure": 0},
                           "fp_notes": old["fp_notes"], "admitted": False,
                           "admitted_reason": "0 label(s)", "updated": old.get("updated", today),
                           "snapshot_counts": {}}
    return {"_about": ABOUT, "version": 1, "bar": bar, "generated": today,
            "rules": dict(sorted(rules.items())), "tier": tier_claim(rules, bar, verified)}


def declared_verified(config: dict | None = None) -> set[str]:
    from arbiter.rule_meta import shipped_rules
    return {r for r in shipped_rules() if P.declared_verified(r, config)}


def write(ledger: dict, paths=(LEDGER, PACKAGED)) -> None:
    text = json.dumps(ledger, indent=1, sort_keys=False) + "\n"
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def render(ledger: dict) -> str:
    lines = [f"{'rule':<56} {'TP':>4} {'FP':>4} {'?':>3} {'lower':>6} {'kappa':>6} {'pop':>5} "
             f"{'wtd':>6}  admitted"]
    for rule, e in ledger["rules"].items():
        lo = f"{e['wilson_lower']:.3f}" if e.get("wilson_lower") is not None else "-"
        k = f"{e['kappa']:.2f}" if e.get("kappa") is not None else "-"
        pop = "?" if e.get("population_unknown") else str(e.get("population") or "-")
        w = (e.get("weighted") or {}).get("precision")
        wtd = f"{w:.3f}" if w is not None else "-"
        lines.append(f"{rule:<56} {e['tp']:>4} {e['fp']:>4} {e['unsure']:>3} {lo:>6} {k:>6} {pop:>5} "
                     f"{wtd:>6}  {'yes' if e['admitted'] else 'no'}: {e.get('admitted_reason', '')}")
    unknown = sorted(r for r, e in ledger["rules"].items() if e.get("population_unknown"))
    if unknown:
        lines.append(f"population unknown for {len(unknown)} rule(s) (labels with no stratum_size; "
                     f"tools/label_populations.py): {', '.join(unknown)}")
    t = ledger.get("tier") or {}
    o = t.get("overall")
    if o:
        lines.append(f"verified tier, held-out, population-weighted: precision {o['precision']} "
                     f"(95% lower {o['wilson_lower']}, n_eff {o['n_eff']}; coverage {o['coverage']}, "
                     f"claim lower {o['claim_lower']}) over {len(t['rules'])} rule(s)"
                     f"; 99% claim: {'yes' if t.get('claim') else 'no'}")
    elif t.get("population_unknown"):
        lines.append(f"verified tier: population unknown for {', '.join(t['population_unknown'])}; no claim")
    else:
        lines.append("verified tier: no held-out labels on any verified rule; no claim")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="rebuild the ledger from label files")
    b.add_argument("labels", type=Path, nargs="*", help=".jsonl label files, or sheets (.csv/.json)")
    b.add_argument("--sheet-batch", default="sheet", help="sheets: the sample these sheets label")
    b.add_argument("--sheet-source", default="sheet", help="sheets: corpus name")
    b.add_argument("--sheet-held-out", action="store_true", help="sheets: the sample is held out")
    b.add_argument("--date", default=_dt.date.today().isoformat())
    b.add_argument("--out", type=Path, help="write here only (default: the ledger and its packaged copy)")
    b.add_argument("--model-prompt", default=None,
                   help="model labels to keep, by labeller prompt version (default: the current "
                        "tools/label_models.py PROMPT_VERSION; 'any' keeps every version)")
    sub.add_parser("show")
    sub.add_parser("check", help="fail when the written ledger differs from its packaged copy")
    args = ap.parse_args(argv)
    if args.cmd == "show":
        print(render(P.load_ledger(LEDGER)))
        return 0
    if args.cmd == "check":
        same = LEDGER.read_bytes() == PACKAGED.read_bytes()
        print("ok: ledger and packaged copy match" if same else
              "FAIL: tools/gold/precision.json and src/arbiter/packs/precision.json differ")
        return 0 if same else 1
    records: list[dict] = []
    for p in args.labels:
        records += (read_jsonl(p) if p.name.endswith((".jsonl", ".jsonl.gz"))
                    else read_sheet(p, args.sheet_batch, args.sheet_source, args.sheet_held_out))
    records, skipped = drop_calibration(records)
    if skipped:
        print(f"left out {skipped} labeller-calibration records (they measure the labellers, not the rules)",
              file=sys.stderr)
    version = current_model_prompt() if args.model_prompt is None else \
        ("" if args.model_prompt == "any" else args.model_prompt)
    records, stale = drop_superseded_model_labels(records, version)
    if stale:
        print(f"left out {stale} model labels made with a labeller prompt other than {version}",
              file=sys.stderr)
    previous = P.load_ledger(LEDGER) if LEDGER.is_file() else None
    ledger = build(records, previous, args.date, declared_verified(), snapshot_counts())
    write(ledger, (args.out,) if args.out else (LEDGER, PACKAGED))
    print(render(ledger))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
