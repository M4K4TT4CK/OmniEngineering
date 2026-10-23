#!/usr/bin/env python3
"""Ground-truth labels from benchmarks with published answers (ARB-067).

    python tools/ground_truth/benchmarks_gt.py owasp --corpus ~/wt/corpus-gold \\
        --out tools/gold/labels
    python tools/ground_truth/benchmarks_gt.py juliet --root ~/wt/corpus-gt/juliet-java \\
        --out tools/gold/labels

No model judges these. Each Arbiter finding is labelled by the benchmark's
own answer for the code it sits in, and only when the finding claims the
CWE that answer is about:

* **OWASP Benchmark** (Java 1.2, Python 0.1; `tools/gold/benchmarks.json`):
  every test case is one file with one CWE and a `real` flag in
  `expectedresults-*.csv`. A finding in a case file whose CWEs meet the
  case's CWE (with the benchmark's aliases) is `tp` when the case is real
  and `fp` when it is marked safe.
* **Juliet Java 1.3** (NIST SARD; `tools/ground_truth/fetch_gt.sh` pins the
  archive by SHA-256): each file's flawed code is in methods named `bad*`
  (or a `_bad` class), its fixed variants in `good*` methods (`goodG2B`:
  safe source into the same sink; `goodB2G`: tainted source into a safe
  sink). A finding of the directory's CWE inside a `bad` method is `tp`,
  inside a `good` method `fp`. CWE-79 is Juliet's CWE-80/81/83, CWE-22 is
  CWE-23/36.

A finding whose CWE the case says nothing about gets no label here: the
benchmark answers one question per case. Findings outside the case files
(the benchmarks' harness code) are ignored.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gt_common as g  # noqa: E402

BENCH = g.ROOT / "tools" / "gold" / "benchmarks.json"
OWASP = {"owasp-benchmark-java": "owasp-java", "owasp-benchmark-python": "owasp-python"}

JULIET_SHA256 = "d985f4177c2bcd7b03455a05c1c8f2e755f55c9eb250accd052f05f877347e60"
JULIET_DIRS = {  # directory -> the CWEs a finding may carry to be about it
    "CWE80_XSS": ["CWE-79", "CWE-80"],
    "CWE81_XSS_Error_Message": ["CWE-79", "CWE-81"],
    "CWE83_XSS_Attribute": ["CWE-79", "CWE-83"],
    "CWE23_Relative_Path_Traversal": ["CWE-22", "CWE-23"],
    "CWE36_Absolute_Path_Traversal": ["CWE-22", "CWE-36"],
    "CWE78_OS_Command_Injection": ["CWE-78", "CWE-77"],
    "CWE89_SQL_Injection": ["CWE-89", "CWE-564"],
    "CWE190_Integer_Overflow": ["CWE-190"],
}


def read_expected(path: Path) -> dict[str, dict]:
    out = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row or row[0].lstrip().startswith("#"):
                continue
            name, category, real, cwe = (c.strip() for c in row[:4])
            out[name] = {"category": category, "real": real.lower() == "true", "cwe": f"CWE-{int(cwe)}"}
    return out


def owasp_labels(findings: list[dict], expected: dict, aliases: dict, test_dir: str,
                 bench: str) -> list[dict]:
    prefix = test_dir.rstrip("/") + "/"
    out = []
    for f in findings:
        if not f["path"].startswith(prefix):
            continue
        case = expected.get(Path(f["path"]).stem)
        if case is None or not g.cwe_match(f["cwe"], [case["cwe"]], aliases):
            continue
        verdict = "tp" if case["real"] else "fp"
        out.append(g.label({**f, "repo": bench}, verdict, labeller="ground-truth",
                           source=OWASP[bench], held_out=True,
                           reason=f"{Path(f['path']).stem} {case['category']} {case['cwe']} "
                                  f"{'real' if case['real'] else 'safe'}",
                           truth_cwe=case["cwe"]))
    return out


# --- Juliet ------------------------------------------------------------------

_METHOD = re.compile(r"^\s*(?:public|private|protected|static|\s)*[\w<>\[\], ]+\s+(\w+)\s*\([^;]*$")


def juliet_side(text: str, path: str, line: int) -> str | None:
    """'bad', 'good', or None: which variant the code at `line` belongs to."""
    span = g._function_span(text, path, "java", line)
    name = ""
    if span:
        head = text.splitlines()[span[0] - 1: span[0] + 2]
        for ln in head:
            m = _METHOD.match(ln)
            if m:
                name = m.group(1)
                break
    low = name.lower()
    if low.startswith("bad") or low.startswith("helperbad") or "bad" in low and "good" not in low:
        return "bad"
    if low.startswith("good") or low.startswith("helpergood"):
        return "good"
    stem = Path(path).stem
    if stem.endswith("_bad"):
        return "bad"
    if re.search(r"_good\w*$", stem):
        return "good"
    return None


def juliet_labels(root: Path, findings_by_dir: dict[str, list[dict]]) -> tuple[list[dict], Counter]:
    out, skipped = [], Counter()
    for d, findings in findings_by_dir.items():
        truth = JULIET_DIRS[d]
        for f in findings:
            if not g.cwe_match(f["cwe"], truth, {}):
                skipped["other-cwe"] += 1
                continue
            p = root / f["path"]
            text = p.read_text(encoding="utf-8", errors="replace")
            side = juliet_side(text, str(p), f["line"])
            if side is None:
                skipped["no-variant"] += 1
                continue
            out.append(g.label({**f, "repo": "juliet-java-1.3"}, "tp" if side == "bad" else "fp",
                               labeller="ground-truth", source="juliet-java", held_out=True,
                               reason=f"Juliet {d} {side} variant", truth_cwe=truth[0]))
    return out, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("owasp")
    o.add_argument("--corpus", type=Path, default=Path("~/wt/corpus-gold"))
    o.add_argument("--out", type=Path, required=True)
    j = sub.add_parser("juliet")
    j.add_argument("--root", type=Path, default=Path("~/wt/corpus-gt/juliet-java"))
    j.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    if args.cmd == "owasp":
        cfg = json.loads(BENCH.read_text(encoding="utf-8"))
        for bench, short in OWASP.items():
            repo = args.corpus.expanduser() / bench
            c = cfg[bench]
            expected = read_expected(repo / c["expected_results"])
            findings = g.scan_tree(repo / c["test_dir"])
            # scan_tree paths are relative to the scanned directory
            findings = [{**f, "path": f"{c['test_dir']}/{f['path']}"} for f in findings]
            labels = owasp_labels(findings, expected, c.get("cwe_aliases", {}), c["test_dir"], bench)
            g.write_jsonl(args.out / f"{short}.jsonl", g.census_population(labels))
            n = Counter(r["label"] for r in labels)
            print(f"{bench}: {len(findings)} findings, {len(labels)} labelled {dict(n)}")
        return 0

    root = args.root.expanduser()
    base = root / "Java" / "src" / "testcases"
    by_dir = {}
    for d in JULIET_DIRS:
        if not (base / d).is_dir():
            print(f"missing {base / d}: run tools/ground_truth/fetch_gt.sh", file=sys.stderr)
            return 2
        fs = g.scan_tree(base / d)
        by_dir[d] = [{**f, "path": f"Java/src/testcases/{d}/{f['path']}"} for f in fs]
        print(f"  {d}: {len(fs)} findings", file=sys.stderr, flush=True)
    labels, skipped = juliet_labels(root, by_dir)
    g.write_jsonl(args.out / "juliet-java.jsonl.gz", g.census_population(labels))
    print(f"juliet: {len(labels)} labelled {dict(Counter(r['label'] for r in labels))}; "
          f"skipped {dict(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
