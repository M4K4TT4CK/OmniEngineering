#!/usr/bin/env python3
"""Mine real before/after pairs from repository history.

## The only ground truth the tool did not generate for itself

Everything else Arbiter measures against, it made. Injection plants faults from
patterns somebody chose and scores the rules against controls drawn from the
same generator: excellent evidence that a rule works mechanically, no evidence
at all about code in the wild. Corpus discrimination compares two populations
that were also chosen. Both are worth having and both share a blind spot, which
is that a rule can pass every one of them while being wrong about real code.

A commit is different. Somebody who knew the system decided something needed
changing and changed it. Nobody wrote it to be found by a scanner.

So: walk a repository's history, scan each commit and its parent, and look for
a finding that is present in the parent and gone in the child at the same site.
That transition is a candidate real-world fix, and it is the strongest single
piece of evidence a rule can have:

  * the rule fired on code a maintainer later judged worth changing, and
  * it stopped firing once they changed it.

A rule that fires on both sides of a commit that plainly fixed the thing is
wrong, and nothing else in the pipeline would have told you.

## Why commit messages are not used as the filter

The obvious version greps commit messages for "fix", "security", "encrypt".
That was tried first and it is a bad filter in both directions. Library
repositories are full of feature commits that say "encryption" and fix nothing;
real remediations are routinely committed as "update manifests" or "address
review comments". Filtering on the message finds the commits somebody wrote a
tidy message for, which is not the same set.

Walking transitions instead needs no vocabulary and no assumption about how
teams write messages. The finding appearing and then disappearing IS the
signal.

## What a pair proves, and what it does not

A transition is a *candidate*. Findings also disappear because the file was
deleted, renamed, or moved wholesale, and none of those are fixes. Deletions
and renames are filtered out here, but the remainder still needs a person to
confirm the change addressed the finding rather than removing the code around
it. So pairs land in the adjudication queue rather than straight into the
ledger — the same rule the rest of the tool follows: a machine may find the
candidate, a person decides what it means.

Once confirmed, a pair becomes a permanent regression case: this rule MUST
fire on this commit and MUST NOT fire on its successor, forever.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arbiter.core import Finding  # noqa: E402
from arbiter.engine import run_scan  # noqa: E402
from arbiter.policy import load_config  # noqa: E402

# Only dimensions where "the finding went away" plausibly means "somebody fixed
# it". A quality metric changing across a commit is refactoring, not a fix.
INTERESTING = {"security", "compliance", "supply_chain"}

# Words that, appearing in a commit message, corroborate a transition in the
# matching rule family. This is a RANKING hint and never a verdict: the commit
# message is not used to FIND pairs (see the module docstring for why), only to
# put the ones a maintainer described first in the queue, so a person's first
# few verdicts land on the clearest cases.
CORROBORATING = {
    "secrets": ("secret", "credential", "password", "token", "key", "remove",
                "rotate", "leak", "uri", "connection string"),
    "supply": ("pin", "sha", "version", "lock", "bump", "digest"),
    "resource": ("security", "context", "privilege", "root", "capabilit",
                 "encrypt", "harden", "policy", "limit", "readonly",
                 "read-only", "nonroot", "non-root"),
    "authored": ("tls", "verify", "insecure", "certificate"),
}


def corroborated(rule: str, subject: str) -> bool:
    """True when the commit message describes the kind of change the rule is about."""
    family = rule.split("/")[-1].split(".")[0]
    words = CORROBORATING.get(family, ())
    low = subject.lower()
    return any(w in low for w in words)

NATIVE = ["secrets", "resource_policy", "supply_chain", "authored"]

# File kinds worth walking history for. Scanning every commit of a large
# repository is pointless when most of them touch code no rule reads.
RELEVANT_SUFFIX = (".tf", ".tfvars", ".yaml", ".yml", ".json", ".py", ".js",
                   ".ts", ".go", ".rb", ".php", ".java", ".env", ".sh",
                   ".properties", ".toml", ".conf")


def _git(repo: Path, *args: str, timeout: int = 120) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout


def candidate_commits(repo: Path, limit: int, paths: list[str]) -> list[str]:
    """Commits that changed a file some rule could read."""
    args = ["log", "--no-merges", "--format=%H", f"-n{limit * 4}"]
    if paths:
        args += ["--", *paths]
    shas = [s for s in _git(repo, *args).split("\n") if s.strip()]
    out = []
    for sha in shas:
        names = _git(repo, "show", "--name-only", "--format=", sha).split("\n")
        touched = [n for n in names if n.strip().endswith(RELEVANT_SUFFIX)]
        if not touched:
            continue
        out.append(sha)
        if len(out) >= limit:
            break
    return out


def _export(repo: Path, sha: str, dest: Path) -> bool:
    dest.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(["git", "-C", str(repo), "archive", sha],
                              capture_output=True, timeout=180)
        if proc.returncode != 0 or not proc.stdout:
            return False
        subprocess.run(["tar", "-x", "-C", str(dest)], input=proc.stdout,
                       capture_output=True, timeout=180)
        return True
    except Exception:  # noqa: BLE001 - an export that fails for any reason is "no pair", not a crashed miner
        return False


def _scan(path: Path, cfg: dict) -> dict[tuple, Finding]:
    """Findings keyed by a site identity that survives line movement."""
    rep = run_scan([str(path)], cfg, only=NATIVE, use_adapters=False)
    out: dict[tuple, Finding] = {}
    for f in rep.active():
        if f.dimension not in INTERESTING:
            continue
        out[(f.rule_id, f.location.path, f.location.logical)] = f
    return out


def mine(repo: Path, name: str, limit: int, cfg: dict, paths: list[str]) -> list[dict]:
    pairs: list[dict] = []
    shas = candidate_commits(repo, limit, paths)
    if not shas:
        return pairs
    for sha in shas:
        parent = _git(repo, "rev-parse", f"{sha}^").strip()
        if not parent:
            continue
        changed = [n for n in _git(repo, "show", "--name-only", "--format=", sha).split("\n")
                   if n.strip()]
        # A finding that vanishes because the file did is not a fix.
        status = _git(repo, "show", "--name-status", "--format=", sha)
        deleted = {ln.split("\t")[-1] for ln in status.split("\n")
                   if ln.startswith(("D", "R"))}
        with tempfile.TemporaryDirectory() as tmp:
            before_dir, after_dir = Path(tmp) / "before", Path(tmp) / "after"
            if not _export(repo, parent, before_dir) or not _export(repo, sha, after_dir):
                continue
            try:
                before, after = _scan(before_dir, cfg), _scan(after_dir, cfg)
            except Exception:  # noqa: BLE001 - a commit whose scan crashes is skipped; the pairs that compare are kept
                continue
        subject = _git(repo, "log", "-1", "--format=%s", sha).strip()[:140]
        for key, f in before.items():
            if key in after or key[1] in deleted:
                continue
            if key[1] not in changed:
                continue  # the file did not change in this commit
            pairs.append({
                "repo": name,
                "fixed_in": sha[:12],
                "vulnerable_at": parent[:12],
                "rule": f.rule_id,
                "path": f.location.path,
                "logical": f.location.logical,
                "title": f.title,
                "severity": f.severity,
                "evidence": f.evidence[:160],
                "subject": subject,
                # A hint for ordering the queue, not a verdict. See CORROBORATING.
                "corroborated_by_message": corroborated(f.rule_id, subject),
                "confirmed": None,   # a person decides; see the module docstring
            })
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/tmp/corpus")
    ap.add_argument("--repo", action="append", default=[],
                    help="repository directory name; repeatable. Default: all cloned")
    ap.add_argument("--commits", type=int, default=40,
                    help="how many relevant commits to walk per repository")
    ap.add_argument("--paths", action="append", default=[],
                    help="limit history to these paths; repeatable")
    ap.add_argument("--out", default="training/fix-pairs.json")
    args = ap.parse_args()

    root = Path(args.root)
    cfg = load_config(None)
    names = args.repo or sorted(p.name for p in root.iterdir() if (p / ".git").is_dir())

    all_pairs: list[dict] = []
    t0 = time.time()
    for name in names:
        repo = root / name
        if not (repo / ".git").is_dir():
            continue
        depth = len([s for s in _git(repo, "log", "--format=%H", "-n2").split("\n") if s])
        if depth < 2:
            print(f"  skip {name}: shallow clone, no history to walk "
                  f"(git fetch --deepen=300 first)")
            continue
        t = time.time()
        pairs = mine(repo, name, args.commits, cfg, args.paths)
        all_pairs.extend(pairs)
        print(f"  {name:<20}{len(pairs):>4} candidate pair(s){time.time() - t:>8.0f}s",
              flush=True)

    # Clearest cases first: a person's first verdicts are worth the most.
    all_pairs.sort(key=lambda p: (not p["corroborated_by_message"], p["repo"]))
    by_rule = Counter(p["rule"] for p in all_pairs)
    strong = [p for p in all_pairs if p["corroborated_by_message"]]
    print(f"\n  {len(all_pairs)} candidate fix pairs from {len(names)} repositories "
          f"in {time.time() - t0:.0f}s")
    print("\n  RULES WITH REAL-WORLD EVIDENCE")
    print("  (a maintainer changed code this rule fired on, and it stopped firing)")
    for rule, n in by_rule.most_common(20):
        print(f"    {n:>4}  {rule}")
    if not by_rule:
        print("    none yet — walk more commits, or repositories that deploy things")

    if strong:
        print(f"\n  {len(strong)} of these have a commit message describing exactly "
              "the kind of change\n  the rule is about. Those are the clearest and "
              "come first in the queue:")
        for p in strong[:8]:
            print(f"    {p['rule'].split('/')[-1]:<34}{p['subject'][:64]}")

    print("\n  Every pair is a CANDIDATE. A finding also disappears when the code "
          "around it\n  is rewritten for unrelated reasons, so each one needs a "
          "person to confirm the\n  change addressed the finding. Confirmed pairs "
          "become permanent regression\n  cases: this rule must fire on the parent "
          "and must not fire on the child.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if out.is_file():
        try:
            existing = json.loads(out.read_text()).get("pairs", [])
        except Exception:  # noqa: BLE001 - an unreadable existing file starts the list fresh
            existing = []
    # Never discard a confirmed verdict by re-running the miner.
    confirmed = {(p["repo"], p["fixed_in"], p["rule"], p["path"]): p.get("confirmed")
                 for p in existing if p.get("confirmed") is not None}
    for p in all_pairs:
        key = (p["repo"], p["fixed_in"], p["rule"], p["path"])
        if key in confirmed:
            p["confirmed"] = confirmed[key]
    out.write_text(json.dumps({
        "schema_version": "1.0",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commits_walked_per_repo": args.commits,
        "pairs": all_pairs,
    }, indent=2))
    print(f"\n  wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
