#!/usr/bin/env python3
"""CVE fix pairs: scan the vulnerable and the fixed version, label what moved (ARB-067).

    python tools/ground_truth/cve_pairs.py fetch --pairs tools/ground_truth/cve_pairs.json \\
        --dest ~/wt/corpus-gt/cve
    python tools/ground_truth/cve_pairs.py label --pairs tools/ground_truth/cve_pairs.json \\
        --dest ~/wt/corpus-gt/cve --out tools/gold/labels

`cve_select.py` pins each pair: a GitHub advisory, its fix commit and that
commit's single parent. `fetch` downloads only the source files the fix
changed, at both commits, from raw.githubusercontent.com (read-only; the
trees are data and are never built or run). `label` scans both sides with
Arbiter and compares, per file, against the lines the fix changed:

* a finding on a changed line of the vulnerable version, with no finding
  of the same rule on the corresponding lines of the fix, whose CWE meets
  the advisory's -> `tp`, source `cve-fix` (no model judges it);
* the same but with a CWE the advisory does not name -> `suspect`
  (`cve-fix-other-cwe`): the fix moved it, but the advisory is not about it;
* a finding on a changed line that is still there after the fix ->
  `suspect` (`cve-fix-persists`): the fix did not remove it, so either the
  rule is reading something the fix left alone, or the fix is partial.

Suspects are not labelled here; `label_models.py` labels them. Only the
fix's own files are scanned, so project-level rules (manifests, docs) never
see a whole project and are left out of the suspects.

What this measures is narrow: whether a rule that fires on vulnerable code
goes quiet when the vulnerability is fixed. It says nothing about the
rule's other findings in the project.
"""
from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gt_common as g  # noqa: E402

CWE_ALIASES = {
    "CWE-78": ["CWE-77", "CWE-88"], "CWE-77": ["CWE-78", "CWE-88"], "CWE-88": ["CWE-77", "CWE-78"],
    "CWE-22": ["CWE-23", "CWE-36", "CWE-73"], "CWE-79": ["CWE-80"], "CWE-89": ["CWE-564"],
    "CWE-200": ["CWE-209"], "CWE-209": ["CWE-200"], "CWE-287": ["CWE-347"], "CWE-347": ["CWE-287"],
    "CWE-798": ["CWE-259", "CWE-321"], "CWE-94": ["CWE-95"], "CWE-95": ["CWE-94"],
}
PROJECT_RULES = ("arbiter/supply.", "arbiter/drift.", "arbiter/contract.", "arbiter/authored.undeclared-import",
                 "arbiter/ast.", "arbiter/quality.", "arbiter/assurance.suppression-census")
NEAR = 2


def fetch(pairs: list[dict], dest: Path) -> int:
    failed = 0
    for p in pairs:
        for side, sha in (("vuln", p["vuln_sha"]), ("fix", p["fix_sha"])):
            for path in p["files"]:
                target = dest / p["ghsa"] / side / path
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                url = f"https://raw.githubusercontent.com/{p['repo']}/{sha}/{path}"
                r = subprocess.run(["curl", "-sfL", "-m", "60", "-o", str(target), url],
                                   capture_output=True, timeout=90)
                if r.returncode != 0:
                    failed += 1
                    print(f"  {p['ghsa']} {side} {path}: curl {r.returncode}", file=sys.stderr)
        print(f"  {p['ghsa']} {p['repo']}", file=sys.stderr, flush=True)
    return failed


def changed_lines(old: list[str], new: list[str]) -> list[tuple[range, range]]:
    """(old line range, new line range) per non-equal hunk, 1-based."""
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    return [(range(i1 + 1, i2 + 1), range(j1 + 1, j2 + 1))
            for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]


def classify(pair: dict, vuln: list[dict], fix: list[dict], texts: dict[str, tuple[list[str], list[str]]]):
    """Yield (finding, outcome) for vulnerable-side findings on changed lines."""
    fix_by = {}
    for f in fix:
        fix_by.setdefault((f["path"], f["rule"]), []).append(f["line"])
    for f in vuln:
        if f["rule"].startswith(PROJECT_RULES) or f["path"] not in texts:
            continue
        old, new = texts[f["path"]]
        hunk = next(((o, n) for o, n in changed_lines(old, new) if f["line"] in o), None)
        if hunk is None:
            continue
        o, n = hunk
        lo, hi = (n.start, n.stop) if len(n) else (n.start, n.start)
        text = old[f["line"] - 1].strip() if f["line"] - 1 < len(old) else ""
        persists = any(lo - NEAR <= ln < hi + NEAR or (ln - 1 < len(new) and new[ln - 1].strip() == text)
                       for ln in fix_by.get((f["path"], f["rule"]), []))
        if persists:
            yield f, "cve-fix-persists"
        elif g.cwe_match(f["cwe"], pair["cwes"], CWE_ALIASES):
            yield f, "cve-fix"
        else:
            yield f, "cve-fix-other-cwe"


def label_pairs(pairs: list[dict], dest: Path) -> tuple[list[dict], list[dict], list[dict]]:
    labels, suspects, per_pair = [], [], []
    for p in pairs:
        root = dest / p["ghsa"]
        if not (root / "vuln").is_dir() or not (root / "fix").is_dir():
            per_pair.append({"ghsa": p["ghsa"], "status": "not fetched"})
            continue
        vuln, fix = g.scan_tree(root / "vuln"), g.scan_tree(root / "fix")
        texts = {}
        for path in p["files"]:
            try:
                texts[path] = ((root / "vuln" / path).read_text(encoding="utf-8", errors="replace").splitlines(),
                               (root / "fix" / path).read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                continue
        outcomes = Counter()
        for f, outcome in classify(p, vuln, fix, texts):
            outcomes[outcome] += 1
            f = {**f, "repo": f"{p['repo']}@{p['vuln_sha'][:12]}"}
            extra = {"ghsa": p["ghsa"], "advisory_cwes": p["cwes"]}
            if outcome == "cve-fix":
                labels.append(g.label(f, "tp", labeller="ground-truth", source="cve-fix", held_out=True,
                                      reason=f"{p['ghsa']} ({', '.join(p['cwes'])}): fires on a line the "
                                             f"fix {p['fix_sha'][:12]} changed, gone after it", **extra))
            else:
                suspects.append({**f, "suspect": outcome, **extra,
                                 "root": str(root / "vuln").replace(str(Path.home()), "~", 1), "language": g.language_of(f["path"])})
        per_pair.append({"ghsa": p["ghsa"], "language": p["language"], "cwes": p["cwes"],
                         "vuln_findings": len(vuln), "fix_findings": len(fix), **outcomes})
    return labels, suspects, per_pair


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["fetch", "label"])
    ap.add_argument("--pairs", type=Path, default=g.ROOT / "tools" / "ground_truth" / "cve_pairs.json")
    ap.add_argument("--dest", type=Path, default=Path("~/wt/corpus-gt/cve"))
    ap.add_argument("--out", type=Path, default=g.ROOT / "tools" / "gold" / "labels")
    ap.add_argument("--suspects", type=Path, help="where to write the suspects JSON (default: beside --out)")
    args = ap.parse_args(argv)
    pairs = json.loads(args.pairs.read_text(encoding="utf-8"))["pairs"]
    dest = args.dest.expanduser()
    if args.cmd == "fetch":
        return 1 if fetch(pairs, dest) else 0
    labels, suspects, per_pair = label_pairs(pairs, dest)
    g.write_jsonl(args.out / "cve-fix.jsonl", g.census_population(labels))
    sus = args.suspects or args.out / "cve-suspects.json"
    sus.write_text(json.dumps({"suspects": suspects, "per_pair": per_pair}, indent=1) + "\n", encoding="utf-8")
    yielded = sum(1 for r in per_pair if r.get("vuln_findings"))
    print(f"{len(pairs)} pairs ({yielded} with any finding): {len(labels)} tp labels, "
          f"{len(suspects)} suspects {dict(Counter(s['suspect'] for s in suspects))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
