#!/usr/bin/env python3
"""Regression guards: saved finding snapshots and scan-time budgets.

    python tools/regression_guard.py check                       # every target present
    python tools/regression_guard.py check --targets ai-corpus,planted --no-perf
    python tools/regression_guard.py fetch --corpus ~/corpus-guard   # the small pinned repos
    python tools/regression_guard.py check --corpus ~/corpus-guard
    python tools/regression_guard.py update --corpus ~/wt/corpus-gold   # after an intended change

Two questions the other benchmarks do not ask:

  Did the findings change?  A snapshot is the exact list of (rule, path,
      line) Arbiter reports on a fixed tree: the in-tree corpora, and a few
      small gold repositories pinned to one commit. A finding that vanishes
      is a possible recall regression (a rule deleted, a query narrowed too
      far); one that appears is a possible false positive. Either fails the
      check and prints the diff. An intended change is accepted by running
      `update` and committing the new snapshot, so the diff is reviewed with
      the rule change that caused it.

  Did the scan get slower?  Each target has a recorded scan time. A
      reference workload is timed in the same run, and
      the budget scales by how much slower this machine is on it than the
      machine the baseline was recorded on, so a CI runner is not held to a
      laptop's numbers. Over `baseline * machine_factor * tolerance + slack`
      fails. The reference is a fixed pure-Python workload that runs no
      Arbiter code (see calibration_workload): a reference Arbiter took
      part in would slow down with the regression and hide it.

Targets are listed in tools/gold/guards.json; snapshots and the time
baseline live in tools/gold/snapshots/. Pinned repositories are untrusted:
they are fetched at one commit with hooks off and no submodules or LFS, and
read, never built or run.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

GUARDS = ROOT / "tools" / "gold" / "guards.json"
SNAPSHOTS = ROOT / "tools" / "gold" / "snapshots"
FETCH_SCRIPT = ROOT / "tools" / "fetch_corpus.sh"


@dataclass
class Target:
    name: str
    path: Path
    pinned_sha: str = ""
    url: str = ""


@dataclass
class Result:
    name: str
    findings: list[str] = field(default_factory=list)
    seconds: float = 0.0
    missing_reason: str = ""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_guards(path: Path = GUARDS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _pins() -> dict:
    import re
    pat = re.compile(r"^get_pinned\s+(\S+)\s+(\S+)\s+([0-9a-f]{40})\s+(\S+)\s*$")
    out = {}
    for line in FETCH_SCRIPT.read_text(encoding="utf-8").splitlines():
        m = pat.match(line)
        if m:
            out[m.group(1)] = (m.group(2), m.group(3))
    return out


def resolve_targets(guards: dict, corpus: Path | None, names: list[str] | None = None) -> list[Target]:
    pins = _pins()
    out = []
    for name, spec in guards["targets"].items():
        if names and name not in names:
            continue
        if spec.get("path"):
            out.append(Target(name, ROOT / spec["path"]))
            continue
        url, sha = pins.get(name, ("", ""))
        base = corpus if corpus else Path("/nonexistent")
        out.append(Target(name, base / name, pinned_sha=sha, url=url))
    return out


def _head(repo: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                           text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def available(t: Target) -> str:
    """Empty when the target can be scanned, else why not."""
    if not t.path.is_dir():
        return f"{t.path} not found"
    if t.pinned_sha and _head(t.path) != t.pinned_sha:
        return f"{t.path} not at pin {t.pinned_sha[:12]}"
    return ""


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def finding_keys(path: Path) -> list[str]:
    """`rule<TAB>path<TAB>line` for every active finding, sorted."""
    from arbiter.engine import run_scan
    from arbiter.policy import load_config
    report = run_scan([str(path)], load_config(None), use_adapters=False, use_cache=False)
    return sorted(f"{f.rule_id}\t{f.location.path}\t{f.location.start_line}" for f in report.active())


def timed_scan(path: Path) -> tuple[list[str], float]:
    started = time.perf_counter()
    keys = finding_keys(path)
    return keys, round(time.perf_counter() - started, 2)


def calibration_workload() -> None:
    """A fixed piece of pure-Python work shaped like a scan (regex over text,
    dict and string churn, JSON round trips) and touching no Arbiter code.

    It must not run Arbiter: the first version timed a scan of the planted
    corpus, and a seeded `sleep` in the rule runner slowed the reference by
    the same factor as every target, so the budget scaled up with the
    regression and the guard passed (2026-10-22). A machine-speed reference
    has to be blind to the thing being measured."""
    import hashlib
    import re as _re
    words = [f"name_{i % 977}_{i * 31 % 1009}" for i in range(60_000)]
    text = "\n".join(" ".join(words[i:i + 12]) for i in range(0, len(words), 12))
    pat = _re.compile(r"\bname_(\d+)_(\d+)\b")
    counts: dict[str, int] = {}
    for _ in range(16):
        for m in pat.finditer(text):
            counts[m.group(1)] = counts.get(m.group(1), 0) + int(m.group(2))
        json.loads(json.dumps(counts, sort_keys=True))
        hashlib.sha256(text.encode()).hexdigest()


def reference_seconds(guards: dict | None = None, repeats: int = 5) -> float:
    """Best of `repeats` runs of the calibration workload: the minimum is the
    least noisy estimate of what this machine can do."""
    best = float("inf")
    for _ in range(repeats):
        started = time.perf_counter()
        calibration_workload()
        best = min(best, time.perf_counter() - started)
    return round(best, 3)


# ---------------------------------------------------------------------------
# Comparison -- pure
# ---------------------------------------------------------------------------

def snapshot_diff(saved: list[str], now: list[str]) -> dict:
    a, b = Counter(saved), Counter(now)
    removed = sorted((a - b).elements())
    added = sorted((b - a).elements())
    lost_rules = sorted({k.split("\t")[0] for k in a} - {k.split("\t")[0] for k in b})
    return {"removed": removed, "added": added, "rules_lost": lost_rules}


def perf_budget(baseline: float, factor: float, perf: dict) -> float:
    return round(baseline * factor * perf["tolerance"] + perf["slack_seconds"], 2)


def machine_factor(ref_now: float, ref_base: float, perf: dict) -> float:
    """How much slower this machine is than the baseline machine, never
    below `min_factor`: a faster runner must not tighten the budget past
    what the baseline machine itself would pass."""
    if ref_base <= 0:
        return 1.0
    return round(max(perf.get("min_factor", 1.0), ref_now / ref_base), 3)


def snapshot_path(name: str) -> Path:
    return SNAPSHOTS / f"{name}.json"


def read_snapshot(name: str) -> dict | None:
    p = snapshot_path(name)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def write_snapshot(t: Target, keys: list[str]) -> None:
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    by_rule = Counter(k.split("\t")[0] for k in keys)
    # `recorded` dates the snapshot for tools/precision_guard.py, which asks
    # whether a rule's labels predate a jump in its count here.
    data = {"target": t.name, "pinned_sha": t.pinned_sha, "arbiter_commit": _head(ROOT),
            "recorded": time.strftime("%Y-%m-%d"),
            "count": len(keys), "by_rule": dict(sorted(by_rule.items())), "findings": keys}
    snapshot_path(t.name).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def perf_baseline_path() -> Path:
    return SNAPSHOTS / "perf-baseline.json"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def check(guards: dict, targets: list[Target], do_snap: bool, do_perf: bool,
          scan=timed_scan, ref_seconds=None, out=print) -> int:
    failures = 0
    perf = guards["perf"]
    base = json.loads(perf_baseline_path().read_text()) if perf_baseline_path().is_file() else {}
    factor = 1.0
    if do_perf and base:
        ref_now = ref_seconds if ref_seconds is not None else reference_seconds(guards)
        factor = machine_factor(ref_now, base.get("reference_seconds", 0), perf)
        out(f"reference {ref_now}s (baseline {base.get('reference_seconds')}s): machine factor {factor}")
    for t in targets:
        why = available(t)
        if why:
            out(f"SKIP {t.name}: {why}")
            continue
        keys, secs = scan(t.path)
        if do_snap:
            saved = read_snapshot(t.name)
            if saved is None:
                out(f"FAIL {t.name}: no snapshot; run `update`")
                failures += 1
            else:
                d = snapshot_diff(saved["findings"], keys)
                if d["removed"] or d["added"]:
                    failures += 1
                    out(f"FAIL {t.name}: {len(d['removed'])} findings gone, {len(d['added'])} new"
                        + (f"; rules that no longer fire at all: {', '.join(d['rules_lost'])}"
                           if d["rules_lost"] else ""))
                    for k in d["removed"][:25]:
                        out(f"    - {k}")
                    for k in d["added"][:25]:
                        out(f"    + {k}")
                else:
                    out(f"ok   {t.name}: {len(keys)} findings match the snapshot")
        if do_perf:
            b = base.get("targets", {}).get(t.name)
            if b is None:
                out(f"FAIL {t.name}: no time baseline; run `update`")
                failures += 1
            else:
                budget = perf_budget(b, factor, perf)
                status = "ok  " if secs <= budget else "FAIL"
                failures += secs > budget
                out(f"{status} {t.name}: scan {secs}s, budget {budget}s (baseline {b}s)")
    return 1 if failures else 0


def update(guards: dict, targets: list[Target], do_snap: bool, do_perf: bool, repeats: int) -> int:
    base = json.loads(perf_baseline_path().read_text()) if perf_baseline_path().is_file() else {"targets": {}}
    if do_perf:
        base["reference_seconds"] = reference_seconds(guards)
        base["recorded"] = time.strftime("%Y-%m-%d")
        base["arbiter_commit"] = _head(ROOT)
    for t in targets:
        why = available(t)
        if why:
            print(f"SKIP {t.name}: {why}")
            continue
        runs = [timed_scan(t.path) for _ in range(repeats if do_perf else 1)]
        keys = runs[0][0]
        if any(r[0] != keys for r in runs):
            print(f"WARN {t.name}: findings differ between runs; snapshot is not deterministic")
        if do_snap:
            write_snapshot(t, keys)
        if do_perf:
            base["targets"][t.name] = min(r[1] for r in runs)
        print(f"recorded {t.name}: {len(keys)} findings, {min(r[1] for r in runs)}s")
    if do_perf:
        SNAPSHOTS.mkdir(parents=True, exist_ok=True)
        perf_baseline_path().write_text(json.dumps(base, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def fetch(targets: list[Target]) -> int:
    """Shallow fetch of each pinned target at its exact commit (same flags as
    fetch_corpus.sh's get_pinned)."""
    bad = 0
    for t in targets:
        if not t.pinned_sha:
            continue
        if _head(t.path) == t.pinned_sha:
            print(f"have {t.name}")
            continue
        t.path.mkdir(parents=True, exist_ok=True)
        env = {"GIT_LFS_SKIP_SMUDGE": "1"}
        import os
        env = {**os.environ, **env}
        steps = [["git", "-c", "init.defaultBranch=guard", "init", "-q"],
                 ["git", "remote", "add", "origin", t.url],
                 ["git", "-c", "core.hooksPath=/dev/null", "fetch", "--quiet", "--depth", "1",
                  "--no-tags", "origin", t.pinned_sha],
                 ["git", "-c", "core.hooksPath=/dev/null", "-c", "advice.detachedHead=false",
                  "checkout", "--quiet", "FETCH_HEAD"]]
        for cmd in steps:
            if subprocess.run(cmd, cwd=t.path, env=env, timeout=600).returncode != 0:
                print(f"FAILED {t.name}")
                bad += 1
                break
        else:
            print(f"got  {t.name} @ {t.pinned_sha[:12]}")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["check", "update", "fetch"])
    ap.add_argument("--corpus", type=Path, help="directory holding the pinned repositories")
    ap.add_argument("--targets", help="comma-separated target names (default: all)")
    ap.add_argument("--no-perf", action="store_true")
    ap.add_argument("--no-snapshots", action="store_true")
    ap.add_argument("--repeats", type=int, default=3, help="update: scans per target for the time baseline")
    ap.add_argument("--guards", type=Path, default=GUARDS)
    args = ap.parse_args(argv)
    guards = load_guards(args.guards)
    names = args.targets.split(",") if args.targets else None
    corpus = args.corpus.expanduser() if args.corpus else None
    targets = resolve_targets(guards, corpus, names)
    if args.cmd == "fetch":
        if corpus is None:
            ap.error("fetch needs --corpus")
        return fetch(targets)
    if args.cmd == "update":
        return update(guards, targets, not args.no_snapshots, not args.no_perf, args.repeats)
    return check(guards, targets, not args.no_snapshots, not args.no_perf)


if __name__ == "__main__":
    raise SystemExit(main())
