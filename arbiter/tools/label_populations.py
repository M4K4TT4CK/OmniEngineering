#!/usr/bin/env python3
"""Backfill the population fields of existing label files, with no model calls.

    python tools/label_populations.py backfill            # every file in PLAN
    python tools/label_populations.py backfill --check    # fail if a file is out of date
    python tools/label_populations.py backfill --only gold-model.jsonl

Label records written before 2026-10-23 (round 4) carry no `stratum_size`,
so the precision ledger could not weight a rule's strata by population
(docs/precision.md, "Population weights"). This recomputes the fields from
what each file was sampled from and writes them in place:

* `report:` files were drawn by `label_models.py label --report --per-rule`
  from a gold run. The population is that run's raw findings, stratified by
  (rule, language) exactly as `label_models.per_rule_sample` stratified
  them, with the held-out split from `tools/gold/heldout.json`. Each label
  is matched to its finding by (repository, finding id); its rule and
  language must agree with the finding's.
* `census:` files labelled every finding of their population (the AI corpus,
  the CVE suspects, ground-truth benchmarks), so each stratum's population
  is its own records (`gt_common.census_population`).

Only the population fields (`label_sample.POPULATION_KEYS`, plus
`population_from`) are written; a record whose other fields would change is
refused, so no verdict, labeller or split can move. A label that cannot be
matched gets `stratum_size: null` and `sampling: "unknown: <why>"`: the
ledger reports it as unknown and the precision guard refuses to let its rule
be verified. Nothing is guessed. The output is deterministic (sorted keys,
gzip mtime 0), so `--check` can hold it.

Calibration files are not backfilled: the ledger never reads them, and
their sample is stratified by truth for a different purpose. Files under
`tools/gold/labels/history/` are superseded prompt versions the ledger drops.
"""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools" / "ground_truth"))
import gt_common as g  # noqa: E402

_spec = importlib.util.spec_from_file_location("label_sample", ROOT / "tools" / "label_sample.py")
ls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ls)

LABELS = ROOT / "tools" / "gold" / "labels"
HELDOUT = ROOT / "tools" / "gold" / "heldout.json"
FIELDS = (*ls.POPULATION_KEYS, "population_from")

# label file -> where its population comes from. A report path is relative
# to the repository root. Provenance:
#   gold-model.jsonl: tools/gold/results/2026-10-23-relabel.md (the v3
#     relabel at b743bc1, `--per-rule ... --seed 20261026` over a fresh gold
#     run at 27febe3, 3,169 findings with a claim); the run's report,
#     sha256 2c6d6105...a8a7 before gzip, is committed beside it.
#   ai-corpus-model.jsonl: `label --ai-corpus` labels every finding; that
#     run made 115 items and labelled 115.
#   cve-suspects-model.jsonl: all 5 suspects of cve-suspects.json.
#   ground truth: every finding the benchmark can judge is labelled.
PLAN: dict[str, str] = {
    "gold-model.jsonl": "report:tools/gold/results/2026-10-23-gold-report-relabel.json.gz",
    "ai-corpus-model.jsonl": "census",
    "cve-suspects-model.jsonl": "census",
    "owasp-java.jsonl": "census",
    "owasp-python.jsonl": "census",
    "cve-fix.jsonl": "census",
    "juliet-java.jsonl.gz": "census",
}


def read_records(path: Path) -> list[dict]:
    raw = path.read_bytes()
    text = (gzip.decompress(raw) if path.name.endswith(".gz") else raw).decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def encode(path: Path, records: list[dict]) -> bytes:
    text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records).encode("utf-8")
    return gzip.compress(text, mtime=0) if path.name.endswith(".gz") else text


def heldout_repos() -> set[str]:
    return set(json.loads(HELDOUT.read_text(encoding="utf-8"))["held_out"]["gold_repos"])


def load_report(path: Path) -> dict:
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.name.endswith(".gz") else raw)


def from_report(records: list[dict], report: dict, held: set[str], origin: str) -> list[dict]:
    """Population fields from the gold run the records were sampled from."""
    findings = ls.findings_from_report(report)
    pops = ls.stratum_populations(findings, lambda f: f["repo"] in held)
    by_id = {(f["repo"], f.get("id")): f for f in findings if f.get("id")}
    out = []
    for r in records:
        f = by_id.get((r.get("repo"), r["finding_id"]))
        why = ("finding not in the report" if f is None else
               f"rule {f['rule']} in the report" if f["rule"] != r["rule_id"] else
               f"language {f['language']!r} in the report" if f["language"] != (r.get("language") or "") else
               "held-out split differs from heldout.json" if (f["repo"] in held) != r["held_out"] else "")
        if why:
            out.append(unknown(r, why))
            continue
        out.append({**r, **pops[(f["rule"], f["language"])], "population_from": origin})
    return out


def unknown(r: dict, why: str) -> dict:
    rec = {k: v for k, v in r.items() if k not in FIELDS}
    return {**rec, "stratum_size": None, "sampling": f"unknown: {why}"}


def backfill(records: list[dict], method: str) -> list[dict]:
    if method == "census":
        out = g.census_population([{k: v for k, v in r.items() if k not in FIELDS} for r in records])
        return [{**r, "population_from": "census"} for r in out]
    if method.startswith("report:"):
        rel = method.split(":", 1)[1]
        return from_report(records, load_report(ROOT / rel), heldout_repos(), rel)
    raise SystemExit(f"unknown method {method!r}")


def unchanged_labels(before: list[dict], after: list[dict]) -> None:
    """Refuse any change outside the population fields."""
    if len(before) != len(after):
        raise SystemExit("backfill changed the number of records")
    for i, (a, b) in enumerate(zip(before, after)):
        strip = lambda r: {k: v for k, v in r.items() if k not in FIELDS}  # noqa: E731
        if strip(a) != strip(b):
            raise SystemExit(f"record {i} ({a.get('finding_id')}): backfill would change more than its population")


def run(plan: dict[str, str], labels_dir: Path, check: bool) -> tuple[int, list[str]]:
    stale, lines = 0, []
    for name, method in plan.items():
        path = labels_dir / name
        if not path.is_file():
            lines.append(f"{name}: missing")
            stale += 1
            continue
        before = read_records(path)
        after = backfill(before, method)
        unchanged_labels(before, after)
        data = encode(path, after)
        unk = sum(1 for r in after if r.get("stratum_size") is None)
        # Compare what the file holds, not its bytes: gzip output differs across Python/zlib versions
        # (3.11 and 3.12+ write different headers for the same text), which made --check fail on 3.11.
        current = path.read_bytes()
        changed = (data != current if not path.name.endswith(".gz")
                   else gzip.decompress(data) != gzip.decompress(current))
        lines.append(f"{name}: {len(after)} records, {unk} with unknown population ({method})"
                     + (" -- out of date" if changed and check else " -- written" if changed else ""))
        if changed:
            stale += 1
            if not check:
                path.write_bytes(data)
    return (stale if check else 0), lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill", help="write the population fields into the label files in PLAN")
    b.add_argument("--check", action="store_true", help="write nothing; exit 1 if any file would change")
    b.add_argument("--only", action="append", default=[], help="only this label file (repeatable)")
    b.add_argument("--labels", type=Path, default=LABELS)
    args = ap.parse_args(argv)
    plan = {k: v for k, v in PLAN.items() if not args.only or k in args.only}
    failures, lines = run(plan, args.labels, args.check)
    print("\n".join(lines))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
