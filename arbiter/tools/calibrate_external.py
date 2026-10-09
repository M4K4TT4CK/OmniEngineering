#!/usr/bin/env python3
"""Assign severities to a third-party tool's checks by measuring them.

## The problem

Checkov's open build reports `"severity": null` on every finding. So does
bandit's per-test confidence in practice, and gitleaks has no notion of
severity at all. Arbiter's adapter manifests papered over this with
`severity_const = "medium"`, which means a scan of one Terraform repository
produced 477 findings of identical weight, and no way to tell the two that
matter from the 475 that do not.

Everybody's answer to this is a hand-written severity table -- somebody's
opinion, written once, never revisited. Arbiter already has the machinery to
do better, because it has two populations of real code and a way to ask
whether a check fires harder on the broken one.

## What this does

Runs the external tools across the corpus, and for every individual check they
emit -- every `CKV_AWS_*`, every bandit `B*`, every gitleaks rule -- computes
the same weighted discrimination ratio used for native rules, then writes a
severity table keyed by check id.

    ratio        assigned severity   reading
    ---------    -----------------   --------------------------------------
    >= 10x       medium              clearly discriminating
    >= 3x        low                 discriminating, but common enough in
                                     working code to be worth downgrading
    < 3x         info                does not separate the populations;
                                     reported at zero weight
    no data      unchanged           never seen on either population, so no
                                     claim is made

## Why this does not break the determinism guarantee

Arbiter's standing rule is that learning adjusts confidence and never
severity, because a severity that drifts silently makes a gate meaningless.
That rule is intact here, and the distinction matters:

  * For a native rule, severity is a deliberate policy statement, and this
    tool does not touch it.
  * For an external check, the tool supplied NO severity. Arbiter was
    inventing `medium` for all of them. Replacing an invented constant with a
    measured one is not drift.

The table is written into the knowledge file, versioned with it, and recorded
in every report that uses it -- so `(commit, config, knowledge_version)` still
produces identical bytes, and `--pin-knowledge` still fails a run if the table
moved underneath it.

## What this cannot tell you

Whether a check is CORRECT. A check that fires only on broken code might still
be flagging the wrong line for the wrong reason. Discrimination says the check
carries signal, not that the signal means what the check's title claims. That
distinction is why adjudication by a person is still the thing calibration
actually reads.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import CORPUS  # noqa: E402

from arbiter.engine import run_scan  # noqa: E402
from arbiter.inventory import LANG_BY_EXT  # noqa: E402
from arbiter.policy import load_config  # noqa: E402

# A check needs to have been seen this many times before a ratio computed from
# it is worth acting on. Below it the table records the counts and assigns
# nothing, which is the honest answer rather than a confident guess.
MIN_OBSERVATIONS = 5

# ...and seen in this many distinct repositories before it may be PROMOTED
# above "low". One repository is not a population.
#
# This rail exists because the first version of this tool promoted "Ensure
# every security group and rule has a description" to HIGH. The measurement
# was arithmetically correct and completely wrong: Terragoat is the only
# repository in the corpus with many security groups, and it does not write
# descriptions, so the check scored infinite discrimination on a sample of
# one. A demotion from a thin sample costs little; a promotion from one
# repository puts a documentation-hygiene check on the same footing as a
# public S3 bucket.
MIN_REPOS_TO_PROMOTE = 2

# ratio -> severity. Deliberately coarse: the measurement does not support
# finer distinctions, and pretending otherwise is how severity tables become
# fiction.
#
# Note the ceiling. Measurement CANNOT promote a check to "high", and the
# reason is a distinction worth stating plainly: discrimination measures
# SIGNAL -- how much more often a check fires on broken code than on working
# code -- while severity is supposed to encode CONSEQUENCE, which is how much
# it matters when the check is right. The two correlate and they are not the
# same quantity.
#
# The example that forced this: "Ensure every security group and rule has a
# description" fires 33 times on deliberately broken Terraform and zero times
# on the well-maintained Terraform modules, which are meticulous about
# descriptions. The ratio is real, reproducible, and stack-matched. It is also
# not a security finding, and grading it "high" would put a missing comment on
# the same footing as a public S3 bucket.
#
# So measurement is allowed to say "this carries signal" (up to medium) and
# "this carries no signal" (info, zero weight). It is not allowed to
# manufacture a claim about consequence out of a signal measurement. A check
# reaches "high" only when the tool that owns it says so -- and checkov's open
# build says nothing at all, which is what started this.
BANDS = [(10.0, "medium"), (3.0, "low")]
FLOOR = "info"

EXTERNAL = ["checkov", "bandit", "ruff", "gitleaks", "semgrep"]


def band(ratio: float | None, repos: int = 99) -> str | None:
    if ratio is None:
        return None
    for threshold, sev in BANDS:
        if ratio >= threshold:
            # Seen in only one repository: the ratio may be a property of that
            # repository rather than of the check. Cap the claim at "low".
            if repos < MIN_REPOS_TO_PROMOTE:
                return "low"
            return sev
    return FLOOR


def language_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if name in ("Dockerfile", "Containerfile") or name.startswith("Dockerfile."):
        return "dockerfile"
    if name in ("Makefile", "Jenkinsfile"):
        return name.lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    return LANG_BY_EXT.get(ext, LANG_BY_EXT.get(ext.lower(), "unknown"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/tmp/corpus")
    ap.add_argument("--out", default=".arbiter/external-severity.json")
    ap.add_argument("--only", default=",".join(EXTERNAL))
    ap.add_argument("--timeout-per-repo", type=int, default=900)
    args = ap.parse_args()

    root = Path(args.root)
    cfg = load_config(None)
    # Adapters need the connected profile: semgrep fetches its rules, and an
    # offline profile correctly refuses it. Everything else runs either way.
    cfg = dict(cfg)
    cfg["profile"] = "connected"
    only = [p for p in args.only.split(",") if p]

    hits: dict[str, Counter] = {"clean": Counter(), "vulnerable": Counter(), "examples": Counter()}
    # Lines per population per LANGUAGE, not per population. A check that can
    # only fire on Terraform must be judged against Terraform; measured against
    # whole-corpus size it looks spotless in every repository that contains no
    # Terraform at all. This is the same correction discriminate.py already
    # makes for native rules, and leaving it out here produced a severity table
    # that promoted a documentation-hygiene check to high.
    loc_lang: dict[str, Counter] = {"clean": Counter(), "vulnerable": Counter(),
                                    "examples": Counter()}
    loc: Counter = Counter()
    rule_langs: dict[str, set] = defaultdict(set)
    rule_repos: dict[str, set] = defaultdict(set)
    titles: dict[str, str] = {}
    tools: dict[str, str] = {}
    ran, failed, timed_out = [], [], []

    class _Timeout(Exception):
        pass

    def _alarm(_sig, _frame):
        raise _Timeout()

    # Windows has no SIGALRM, the same gap Adapter._kill_group already works
    # around for process-group signalling. There's no portable way to
    # interrupt a blocked call in the main thread without also aliasing a
    # real Ctrl+C, so the Windows path is shaped differently rather than
    # faked with the same primitive: the scan runs in a worker thread, and
    # the budget stops *waiting* for it instead of stopping it. The abandoned
    # worker still exits on its own -- every adapter already enforces its own
    # subprocess timeout -- it just isn't blocking this loop's progress.
    has_alarm = hasattr(signal, "SIGALRM")
    if has_alarm:
        signal.signal(signal.SIGALRM, _alarm)
    else:
        from concurrent.futures import ThreadPoolExecutor
        from concurrent.futures import TimeoutError as _FutureTimeout
        _executor = ThreadPoolExecutor(max_workers=1)

    def _run_bounded(path: Path, budget: int):
        if has_alarm:
            signal.alarm(max(0, int(budget)))
            try:
                return run_scan([str(path)], cfg, only=only, use_adapters=True)
            except _Timeout:
                raise TimeoutError from None
            finally:
                signal.alarm(0)
        future = _executor.submit(run_scan, [str(path)], cfg, only=only, use_adapters=True)
        try:
            return future.result(timeout=max(0, int(budget)))
        except _FutureTimeout:
            raise TimeoutError from None

    for name, (population, _stack) in CORPUS.items():
        path = root / name
        if not path.is_dir():
            continue
        t0 = time.time()
        # A wall-clock budget per repository. Without it one 1,000,000-line
        # repository can hold the whole run for the better part of an hour
        # while an external tool grinds through it, and the result is not
        # worth the wait: the checks that repository would contribute are
        # already observed elsewhere in the corpus. A repository that runs out
        # of time is RECORDED as skipped rather than silently dropped, because
        # a check seen in fewer repositories has weaker evidence and the table
        # should say so.
        try:
            rep = _run_bounded(path, args.timeout_per_repo)
        except TimeoutError:
            timed_out.append(name)
            print(f"  {name:<20}{population:<11}   timed out after "
                  f"{args.timeout_per_repo}s — skipped", flush=True)
            continue
        except Exception as exc:  # noqa: BLE001 - one repository that crashes the scan is recorded as failed; the run continues
            failed.append((name, f"{type(exc).__name__}: {exc}"[:120]))
            continue
        loc[population] += max(1, sum(r.loc for r in rep.repos))
        for lang, count in rep.loc_by_language.items():
            loc_lang[population][lang] += count
        n = 0
        for f in rep.active():
            if "/" not in f.rule_id:
                continue
            tool = f.rule_id.split("/", 1)[0]
            if tool not in only:
                continue
            hits[population][f.rule_id] += 1
            titles.setdefault(f.rule_id, f.title[:120])
            tools[f.rule_id] = tool
            rule_repos[f.rule_id].add(name)
            if f.location.path:
                rule_langs[f.rule_id].add(language_of(f.location.path))
            n += 1
        ran.append(name)
        print(f"  {name:<20}{population:<11}{n:>6} external findings"
              f"{time.time() - t0:>8.0f}s", flush=True)

    table: dict[str, dict] = {}
    for rule in sorted(set(hits["clean"]) | set(hits["vulnerable"])):
        good_n, bad_n = hits["clean"][rule], hits["vulnerable"][rule]
        langs = rule_langs.get(rule) or set()
        denom = {p: sum(loc_lang[p][x] for x in langs) or loc[p] for p in loc_lang}
        good = good_n / (denom["clean"] / 1000) if denom["clean"] else 0.0
        bad = bad_n / (denom["vulnerable"] / 1000) if denom["vulnerable"] else 0.0
        if not bad:
            ratio = None
        elif not good:
            ratio = float("inf")
        else:
            ratio = bad / good
        enough = (good_n + bad_n) >= MIN_OBSERVATIONS
        n_repos = len(rule_repos.get(rule, ()))
        sev = band(ratio, n_repos) if enough else None
        table[rule] = {
            "tool": tools.get(rule, ""),
            "title": titles.get(rule, ""),
            "clean_hits": good_n,
            "vulnerable_hits": bad_n,
            "example_hits": hits["examples"][rule],
            "languages": sorted(langs),
            "repos_seen": n_repos,
            "clean_kloc": round(denom["clean"] / 1000, 1),
            "vulnerable_kloc": round(denom["vulnerable"] / 1000, 1),
            "ratio": "inf" if ratio == float("inf") else (round(ratio, 2) if ratio else None),
            "severity": sev,
            "basis": ("measured" if sev and n_repos >= MIN_REPOS_TO_PROMOTE
                      else "measured-single-repo" if sev
                      else "insufficient-observations"),
        }

    assigned = {r: v for r, v in table.items() if v["severity"]}
    by_sev = Counter(v["severity"] for v in assigned.values())
    print(f"\n  {len(ran)} repositories scanned, {len(failed)} failed, "
          f"{len(timed_out)} timed out")
    for name in timed_out:
        print(f"    TIMED OUT {name} — its checks are unobserved from this repository")
    for name, why in failed[:10]:
        print(f"    FAILED {name}: {why}")
    print(f"  {len(table)} distinct external checks seen, "
          f"{len(assigned)} with enough observations to assign a severity")
    for sev in ("high", "medium", "low", "info"):
        print(f"    {by_sev.get(sev, 0):>5}  {sev}")
    print(f"  {len(table) - len(assigned)} left unassigned — seen fewer than "
          f"{MIN_OBSERVATIONS} times, so no claim is made about them")
    capped = sum(1 for v in assigned.values() if v["basis"] == "measured-single-repo")
    if capped:
        print(f"  {capped} capped at 'low': seen in only one repository, so the "
              "ratio may\n    be a property of that repository rather than of "
              "the check")

    ranked = sorted(
        (v for v in table.values() if v["severity"] in ("high", "medium")),
        key=lambda v: -(float("inf") if v["ratio"] == "inf" else (v["ratio"] or 0)),
    )
    print("\n  THE CHECKS THAT ACTUALLY SEPARATE THE POPULATIONS")
    for v in ranked[:15]:
        r = "inf" if v["ratio"] == "inf" else f"{v['ratio']}x"
        print(f"    {r:>9}  {v['severity']:<7}{v['clean_hits']:>5} good"
              f"{v['vulnerable_hits']:>5} broken   {v['title'][:58]}")

    noise = sorted((v for v in table.values() if v["severity"] == "info"),
                   key=lambda v: -v["clean_hits"])
    print("\n  THE CHECKS THAT DO NOT — reported at zero weight")
    for v in noise[:10]:
        r = "inf" if v["ratio"] == "inf" else f"{v['ratio']}x"
        print(f"    {r:>9}  {v['clean_hits']:>5} good"
              f"{v['vulnerable_hits']:>5} broken   {v['title'][:58]}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "schema_version": "1.0",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repos_scanned": len(ran),
        "repos_timed_out": timed_out,
        "repos_failed": [n for n, _ in failed],
        "lines_by_population": dict(loc),
        "bands": [[t, s] for t, s in BANDS] + [[0, FLOOR]],
        "min_observations": MIN_OBSERVATIONS,
        "min_repos_to_promote": MIN_REPOS_TO_PROMOTE,
        "lines_by_population_language": {p: dict(c) for p, c in loc_lang.items()},
        "checks": table,
    }, indent=2))
    print(f"\n  wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
