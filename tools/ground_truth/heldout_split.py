#!/usr/bin/env python3
"""Freeze the held-out split the precision claim is reported on (ARB-067).

    python tools/ground_truth/heldout_split.py --out tools/gold/heldout.json

30% of the gold repositories (`get_pinned` lines of tools/fetch_corpus.sh,
less the OWASP benchmarks), stratified by language with a recorded seed:
each language gets floor(30% of its repos), and the remainder up to 30% of
the whole goes to the languages with the largest fractional share, ties
broken by the seeded generator. Every ground-truth source (OWASP Benchmark,
CVE fix pairs, Juliet) is held out in full.

The file is written once and then frozen: rerunning with the same seed and
pins gives the same split, and `--check` fails if the committed file and a
fresh computation disagree. Rule authors must not open held-out findings
while working on a rule; docs/testing-harness.md says why.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

SEED = 20261022
FRACTION = 0.30


def split(pins: list, benchmarks: set[str], seed: int = SEED, fraction: float = FRACTION) -> dict:
    by_lang = defaultdict(list)
    for p in pins:
        if p.name not in benchmarks:
            by_lang[p.language].append(p.name)
    total = sum(len(v) for v in by_lang.values())
    want = round(total * fraction)
    take = {lang: math.floor(len(v) * fraction) for lang, v in by_lang.items()}
    rng = random.Random(seed)
    langs = sorted(by_lang)
    rng.shuffle(langs)  # the tie-break
    rest = sorted(langs, key=lambda l: -(len(by_lang[l]) * fraction - take[l]))
    for lang in rest[: max(0, want - sum(take.values()))]:
        take[lang] += 1
    held = []
    for lang in sorted(by_lang):
        held += rng.sample(sorted(by_lang[lang]), take[lang])
    return {"gold_repos": sorted(held),
            "per_language": {l: sorted(set(held) & set(by_lang[l])) for l in sorted(by_lang)},
            "gold_repos_total": total}


def build(seed: int = SEED) -> dict:
    import gold_benchmark as gb
    pins = gb.parse_pins((ROOT / "tools" / "fetch_corpus.sh").read_text(encoding="utf-8"))
    benchmarks = set(json.loads((ROOT / "tools" / "gold" / "benchmarks.json").read_text(encoding="utf-8")))
    s = split(pins, benchmarks, seed)
    return {
        "schema_version": 1,
        "frozen": "2026-10-22",
        "seed": seed,
        "fraction": FRACTION,
        "method": "tools/ground_truth/heldout_split.py: per-language floor(30%), remainder by largest share, "
                  "seeded tie-break and draw",
        "held_out": {
            **s,
            "ground_truth_sources": ["owasp-java", "owasp-python", "cve-fix", "juliet-java"],
        },
        "development": {
            "ai_corpus": "fixtures/ai-corpus is development data: rules were tuned on it. A second "
                         "corpus from a different generator model, held out, is future work.",
            "planted": "fixtures/planted is a regression check, never part of a precision claim.",
        },
        "policy": "Rule authors must not open findings, labels or excerpts from held-out repositories or "
                  "ground-truth sources while changing a rule. The precision claim is reported on the "
                  "held-out split only; a rule tuned on what it is measured on overstates itself.",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "tools" / "gold" / "heldout.json")
    ap.add_argument("--check", action="store_true", help="fail if --out differs from a fresh split")
    args = ap.parse_args(argv)
    doc = build()
    if args.check:
        have = json.loads(args.out.read_text(encoding="utf-8"))
        same = have["held_out"]["gold_repos"] == doc["held_out"]["gold_repos"]
        print("held-out split matches" if same else "held-out split differs from a fresh computation")
        return 0 if same else 1
    args.out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"{len(doc['held_out']['gold_repos'])} of {doc['held_out']['gold_repos_total']} gold repos held out "
          f"-> {args.out}: {doc['held_out']['gold_repos']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
