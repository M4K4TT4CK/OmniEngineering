#!/usr/bin/env python3
"""CI precision guard: a rule may be gate-eligible only on evidence.

    python tools/precision_guard.py check                 # shipped rules + arbiter.yaml
    python tools/precision_guard.py check --config other.yaml

Fails when:

  1. a rule declared verified (rule metadata `tier: verified`, or
     `precision.verified_rules` in the config) has no ledger entry, or its
     entry does not reach the bar (Wilson lower bound, label count, kappa);
  2. the ledger's recorded `admitted` flag disagrees with its own counts (a
     hand edit, or a ledger built against another bar);
  3. a labelled rule's count in the planted or AI-corpus snapshot is more
     than 20% above the count recorded when it was labelled, and the
     ledger entry was not updated after the snapshot was: the rule now
     fires on code its labels never saw, so its precision is unmeasured;
  4. tools/gold/precision.json and the packaged copy differ.

The sibling of tools/regression_guard.py: that one asks whether findings
changed, this one whether the precision claim still rests on its labels.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arbiter import precision as P  # noqa: E402

LEDGER = ROOT / "tools" / "gold" / "precision.json"
PACKAGED = ROOT / "src" / "arbiter" / "packs" / "precision.json"
SNAPSHOTS = ROOT / "tools" / "gold" / "snapshots"
TARGETS = ("planted", "ai-corpus")
JUMP = 0.20


def snapshot_date(path: Path, snap: dict) -> str:
    """When a snapshot was recorded: its own `recorded` field, else the date
    of the last commit that touched it, else today (newest possible, so an
    undated snapshot can only make the guard stricter)."""
    if snap.get("recorded"):
        return str(snap["recorded"])
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%cs", "--", str(path)],
                           capture_output=True, text=True, timeout=20, check=False)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return _dt.date.today().isoformat()


def load_snapshots(snapdir: Path = SNAPSHOTS) -> dict[str, dict]:
    out = {}
    for t in TARGETS:
        p = snapdir / f"{t}.json"
        if p.is_file():
            snap = json.loads(p.read_text(encoding="utf-8"))
            out[t] = {"by_rule": snap.get("by_rule", {}), "recorded": snapshot_date(p, snap)}
    return out


def declared(config: dict | None) -> set[str]:
    from arbiter.rule_meta import shipped_rules
    out = {r for r in shipped_rules() if P.declared_verified(r, config)}
    # A config may declare rules Arbiter does not ship (a house rule). They
    # are named, not globbed, to be checked here.
    for g in ((config or {}).get("precision") or {}).get("verified_rules") or []:
        if not any(ch in g for ch in "*?["):
            out.add(g)
    return out


def check(ledger: dict, verified: set[str], snapshots: dict[str, dict],
          packaged_equal: bool = True) -> list[str]:
    """Every failure, as a line. Pure: tests pass the pieces in."""
    from arbiter.rule_meta import ledger_key
    bar = ledger.get("bar") or P.DEFAULT_BAR
    rules = ledger.get("rules") or {}
    out: list[str] = []
    for rule in sorted(verified):
        key = ledger_key(rule)
        entry = rules.get(key)
        if entry is None:
            out.append(f"{rule}: declared verified but has no ledger entry ({key})")
            continue
        ok, why = P.admission(entry, bar)
        if not ok:
            out.append(f"{rule}: declared verified but below the bar: {why}")
    for rule, entry in sorted(rules.items()):
        ok, why = P.admission(entry, bar)
        if "admitted" in entry and bool(entry["admitted"]) != ok:
            out.append(f"{rule}: ledger says admitted={entry['admitted']} but its counts say {ok} ({why})")
        if not (entry.get("tp", 0) + entry.get("fp", 0) + entry.get("unsure", 0)):
            continue
        then_all = entry.get("snapshot_counts") or {}
        for target, snap in snapshots.items():
            now = int(snap["by_rule"].get(rule, 0))
            then = then_all.get(target)
            if then is None:
                continue
            then = int(then)
            if now <= then * (1 + JUMP) or now - then < 1:
                continue
            if str(entry.get("updated", "")) > str(snap["recorded"]):
                continue
            pct = "new" if then == 0 else f"+{(now - then) / then:.0%}"
            out.append(f"{rule}: {target} snapshot count {then} -> {now} ({pct}) since it was "
                       f"labelled on {entry.get('updated', '?')}, and the snapshot "
                       f"({snap['recorded']}) is not older than the ledger entry: re-label it")
    if not packaged_equal:
        out.append("tools/gold/precision.json and src/arbiter/packs/precision.json differ: "
                   "rebuild with tools/precision_ledger.py build")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["check"])
    ap.add_argument("--config", type=Path, default=ROOT / "arbiter.yaml")
    ap.add_argument("--ledger", type=Path, default=LEDGER)
    args = ap.parse_args(argv)
    from arbiter.policy import load_config
    config = load_config(str(args.config)) if args.config.is_file() else None
    ledger = P.load_ledger(args.ledger)
    equal = (not PACKAGED.is_file() or not args.ledger.is_file()
             or args.ledger.read_bytes() == PACKAGED.read_bytes())
    verified = declared(config)
    failures = check(ledger, verified, load_snapshots(), equal)
    for line in failures:
        print(f"FAIL {line}")
    admitted = sum(1 for e in (ledger.get("rules") or {}).values() if P.admission(e, ledger["bar"])[0])
    print(f"{len(verified)} rule(s) declared verified, {len(ledger.get('rules') or {})} in the ledger, "
          f"{admitted} admitted: {'FAIL' if failures else 'ok'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
