#!/usr/bin/env python3
"""Score the planted corpus per language: what is caught, missed, or misfired.

    python tools/planted_score.py              # native probes only (fast)
    python tools/planted_score.py --adapters   # with semgrep, bandit, ... too
    python tools/planted_score.py --json

The corpus and its markers are described in src/arbiter/planted.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arbiter import planted


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--adapters", action="store_true", help="also run the external analyzers")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("languages", nargs="*")
    args = ap.parse_args()
    if args.adapters:
        from arbiter.adapters import register_adapters
        register_adapters()
    corpus = ROOT / "fixtures" / "planted"
    rows = []
    for lang in args.languages or planted.languages(corpus):
        s = planted.score(corpus, lang, use_adapters=args.adapters)
        rows.append({
            "language": lang, "expected": len(s.expected), "caught": len(s.caught),
            "missed": [f"{e.rule} {e.path}:{e.line}" for e in s.missed],
            "unexpected_bad": [f"{r} {p}:{ln}" for r, p, ln in s.unexpected_bad],
            "false_positives_good": [f"{r} {p}:{ln}" for r, p, ln in s.false_positives_good],
        })
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"{r['language']:<11} {r['caught']}/{r['expected']} caught, "
                  f"{len(r['false_positives_good'])} on good twins, {len(r['unexpected_bad'])} unmarked")
            for key in ("missed", "false_positives_good", "unexpected_bad"):
                for item in r[key]:
                    print(f"    {key}: {item}")
    bad = any(r["missed"] or r["false_positives_good"] or r["unexpected_bad"] for r in rows)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
