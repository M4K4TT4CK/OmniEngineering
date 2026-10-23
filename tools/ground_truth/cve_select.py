#!/usr/bin/env python3
"""Pick CVE fix pairs from GitHub Security Advisories and pin them (ARB-067).

    python tools/ground_truth/cve_select.py --per-language 12 \\
        --out tools/ground_truth/cve_pairs.json

For each language's ecosystem (pip, npm, go, maven, rubygems) and each CWE
Arbiter claims, this walks reviewed advisories newest first and keeps one
whose references include a GitHub fix commit that

* has exactly one parent (the parent is the vulnerable version),
* changes at least one non-test source file of that language, and
* is small (at most 8 such files and 400 changed lines), so the patched
  lines are the fix rather than a release's worth of churn.

The CWEs are visited round-robin, so a language's pairs spread over its
classes instead of all being the most common one. The output pins every
pair by commit SHA; `cve_pairs.py fetch` downloads exactly those files at
exactly those commits. Read-only GitHub API calls through `gh api`; no code
is fetched here, only advisory and commit metadata.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ECOSYSTEMS = {"python": "pip", "typescript": "npm", "go": "go", "java": "maven", "ruby": "rubygems"}
EXTENSIONS = {"python": (".py",), "typescript": (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"),
              "go": (".go",), "java": (".java",), "ruby": (".rb",)}
CWES = ["CWE-89", "CWE-78", "CWE-77", "CWE-22", "CWE-79", "CWE-918", "CWE-434", "CWE-502",
        "CWE-352", "CWE-287", "CWE-798", "CWE-200"]
MAX_FILES, MAX_LINES = 8, 400
_COMMIT = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/commit/([0-9a-f]{40})$")
_TEST = re.compile(r"(^|/)(tests?|spec|__tests__|testdata|fixtures?|examples?|docs?)/|_test\.go$|"
                   r"\.(test|spec)\.[jt]sx?$|Tests?\.java$|_spec\.rb$|(^|/)test_[^/]*\.py$|\.min\.js$")


def gh(path: str, **params) -> object:
    cmd = ["gh", "api", "-X", "GET", path]
    for k, v in params.items():
        cmd += ["-f", f"{k}={v}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return json.loads(r.stdout)


def source_files(files: list[dict], language: str) -> list[dict]:
    return [f for f in files if f["filename"].endswith(EXTENSIONS[language])
            and not _TEST.search(f["filename"]) and f.get("status") in ("modified",)]


def candidate(adv: dict, language: str) -> dict | None:
    for ref in adv.get("references") or []:
        m = _COMMIT.match(ref.strip())
        if not m:
            continue
        owner, repo, sha = m.groups()
        try:
            c = gh(f"repos/{owner}/{repo}/commits/{sha}")
        except RuntimeError:
            continue
        if len(c.get("parents") or []) != 1:
            continue
        files = source_files(c.get("files") or [], language)
        churn = sum(f["additions"] + f["deletions"] for f in files)
        if not files or len(files) > MAX_FILES or churn > MAX_LINES:
            continue
        return {"ghsa": adv["ghsa_id"], "cve": adv.get("cve_id"), "language": language,
                "ecosystem": ECOSYSTEMS[language],
                "package": (adv.get("vulnerabilities") or [{}])[0].get("package", {}).get("name"),
                "cwes": sorted({c["cwe_id"] for c in adv.get("cwes") or []}),
                "summary": adv.get("summary", "")[:160], "published": adv.get("published_at"),
                "repo": f"{owner}/{repo}", "fix_sha": sha, "vuln_sha": c["parents"][0]["sha"],
                "files": sorted(f["filename"] for f in files)}
    return None


def select(language: str, per_language: int, pages: int, seen_repos: set) -> list[dict]:
    queues = {cwe: [] for cwe in CWES}
    fetched = {cwe: 0 for cwe in CWES}
    out, used = [], set()
    exhausted: set[str] = set()
    while len(out) < per_language and len(exhausted) < len(CWES):
        for cwe in CWES:
            if len(out) >= per_language or cwe in exhausted:
                continue
            pick = None
            while pick is None:
                if not queues[cwe]:
                    if fetched[cwe] >= pages:
                        exhausted.add(cwe)
                        break
                    fetched[cwe] += 1
                    try:
                        page = gh("/advisories", ecosystem=ECOSYSTEMS[language], cwes=cwe.split("-")[1],
                                  type="reviewed", per_page=50, page=fetched[cwe])
                    except RuntimeError as exc:
                        print(f"  {language} {cwe}: {exc}", file=sys.stderr)
                        exhausted.add(cwe)
                        break
                    if not page:
                        exhausted.add(cwe)
                        break
                    queues[cwe].extend(page)
                adv = queues[cwe].pop(0)
                if adv["ghsa_id"] in used or adv.get("withdrawn_at"):
                    continue
                c = candidate(adv, language)
                # one pair per repository per language, so one project's style
                # does not stand in for the language
                if c and c["repo"] not in seen_repos:
                    pick = c
            if pick:
                used.add(pick["ghsa"])
                seen_repos.add(pick["repo"])
                out.append({**pick, "selected_for": cwe})
                print(f"  {language} {cwe}: {pick['ghsa']} {pick['repo']} {len(pick['files'])} file(s)",
                      file=sys.stderr, flush=True)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--per-language", type=int, default=12)
    ap.add_argument("--pages", type=int, default=2, help="advisory pages (50 each) per CWE per language")
    ap.add_argument("--language", action="append", default=[])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    pairs = []
    seen: set = set()
    for lang in args.language or list(ECOSYSTEMS):
        pairs += select(lang, args.per_language, args.pages, seen)
    doc = {"schema_version": 1, "source": "GitHub Security Advisories (reviewed), fix commit from references",
           "criteria": {"cwes": CWES, "max_files": MAX_FILES, "max_changed_lines": MAX_LINES,
                        "one_parent": True, "non_test_source_only": True},
           "pairs": pairs}
    args.out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"{len(pairs)} pairs -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
