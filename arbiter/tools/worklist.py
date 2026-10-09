#!/usr/bin/env python3
"""Turn a training cycle's measurements into a ranked list of things to fix.

## Why this exists

A training cycle produces a lot of numbers and no instructions. Somebody --
a person, or a Claude session opening this repository weeks from now with no
memory of the last one -- then has to read all of it and work out what to do.
That step is where the work stalls, and it is the step a machine can do.

So this reads what the cycle just measured and writes a queue: the highest-
value thing to fix first, with the evidence for why it is first, in an order
that reflects what has actually paid off. Every real defect found so far came
from one of these six checks, in roughly this order of yield.

Run it after a cycle:

    python tools/worklist.py --out training/WORKLIST.md

It never changes a rule. It decides nothing. It only says where to look, so
that the next session starts from a question instead of from a pile of tables.

## The six checks, and why each one is here

1. CRITICAL OR HIGH ON WELL-MAINTAINED CODE. The strongest signal there is.
   A critical is what turns somebody's build red, so one on good code is worth
   more attention than a hundred low-severity notes. Three real defects came
   out of this check in a single afternoon, including a critical, high-
   confidence "leaked private key" finding that was pointing at three literal
   dots in a documentation example.

2. A SEVERITY THE MEASUREMENT DOES NOT SUPPORT. A security rule that fires no
   harder on deliberately broken code than on working code is describing a
   coding style, not detecting a defect, and should not be moving anybody's
   grade.

3. A RULE WITH NO CONTROLS. The dangerous state, because it looks perfect. A
   rule with measured recall and no controls could be firing on absolutely
   everything and every number would still read 1.0000. Six rules were in that
   state at once.

4. A RULE NOTHING HAS EVER EXERCISED. It fired on none of the corpus and has
   no injection trials, so nothing is known about it in either direction. It
   is an assertion wearing the costume of a measurement.

5. A STACK WITH NO BROKEN COUNTERPART. If a language appears only in
   well-maintained repositories, its rules cannot be measured at all -- there
   is nothing to compare against. Eight of thirteen languages were in this
   state, and closing it is what surfaced the three criticals in check 1.

6. TOO LITTLE EVIDENCE TO JUDGE. Not a defect: a gap. These rules need more
   or more varied code before any verdict about them means anything.

7. REAL-WORLD FIX PAIRS WAITING FOR A VERDICT. A commit where a maintainer
   changed code a rule fired on, after which it stopped firing. The only
   evidence in the system the tool did not generate for itself.

8. CONTESTED FINDINGS. Two analyzers, same line, different conclusions.
   Exactly one is wrong, so a verdict there is worth more than twenty on
   findings everyone already agrees about.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from arbiter.probes import _load_resource_rules  # noqa: E402

# A language needs at least this many lines in the well-maintained population
# before its lack of a broken counterpart is worth raising. Below it, the
# language is barely represented either way.
MIN_LANG_LINES = 5_000
UNSUPPORTED_RATIO = 1.5
JUDGEABLE = {"security", "compliance"}


def _load(path: str) -> dict | None:
    try:
        doc = json.loads(Path(path).read_text())
    except Exception:
        return None
    return doc if isinstance(doc, dict) else None


def _rows(doc: dict | None, *required: str) -> list[dict] | None:
    """Rows from a results file, or None when they are not the shape expected.

    This runs unattended every night. A half-written or mismatched results file
    must degrade to "that input is missing" rather than killing the queue,
    because a missing worklist is indistinguishable from a clean one and that
    is the failure this whole tool exists to prevent.
    """
    if not doc:
        return None
    rows = doc.get("rows")
    if not isinstance(rows, list):
        return None
    if rows and not all(isinstance(r, dict) and all(k in r for k in required)
                        for r in rows):
        return None
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-summary", default="/tmp/corpus-out/summary.json")
    ap.add_argument("--discrimination", default="/tmp/discriminate.json")
    ap.add_argument("--knowledge", default=".arbiter/knowledge.json")
    ap.add_argument("--out", default="")
    ap.add_argument("--packs", action="append", default=[],
                    help="extra control-pack directory; repeatable")
    ap.add_argument("--fix-pairs", default="training/fix-pairs.json")
    ap.add_argument("--disagreements", default="training/disagreements.json")
    args = ap.parse_args()

    corpus = _load(args.corpus_summary)
    disc = _load(args.discrimination)
    # Validate the shape before anything reads it.
    if _rows(corpus, "expectation", "critical", "high") is None:
        corpus = None
    if _rows(disc, "rule", "severity") is None:
        disc = None
    know = _load(args.knowledge) or {}
    pairs = (_load(args.fix_pairs) or {}).get("pairs") or []
    disagree = _load(args.disagreements) or {}
    missing = [n for n, d in (("corpus", corpus), ("discrimination", disc)) if d is None]

    L: list[str] = []
    # Deliberately not `n`: `item()` closes over this with nonlocal, so any
    # loop variable of the same name inside main() silently resets the
    # numbering. That happened, and the queue printed items 1, 2, 15, 2, 3.
    item_count = 0

    def item(priority: str, title: str, *why: str) -> None:
        nonlocal item_count
        item_count += 1
        L.append(f"### {item_count}. [{priority}] {title}\n")
        L.extend(why)
        L.append("")

    L.append("# What to work on next\n")
    L.append(f"Generated {_dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds')} "
             "by `tools/worklist.py` from the last training cycle.\n")
    L.append("Nothing here is a decision. Each entry says what the measurement "
             "shows and what question it raises.\n")
    if missing:
        L.append(f"> Incomplete: no results found for {', '.join(missing)}. "
                 "Run `./tools/train_cycle.sh` first.\n")

    # ---- 1. criticals and highs on well-maintained code ---------------------
    if corpus:
        offenders = [r for r in corpus["rows"]
                     if r["expectation"] == "clean" and (r["critical"] or r["high"])]
        if offenders:
            body = ["A critical or high is what turns a build red. On code written "
                    "carefully by people who know the tools, it is almost always the "
                    "rule that is wrong, not the code.\n"]
            for r in sorted(offenders, key=lambda r: -(r["critical"] * 10 + r["high"])):
                body.append(f"- **{r['repo']}** ({r['stack_label']}): "
                            f"{r['critical']} critical, {r['high']} high")
            body.append("\nOpen the report for each, read the finding, and decide "
                        "whether it is a true positive you would act on. If not, it "
                        "is a defect — fix it as a downgrade with a regression test "
                        "naming the repository, not as a deletion.")
            item("HIGHEST", "Criticals or highs on well-maintained code", *body)
        else:
            L.append("No criticals or highs on well-maintained code. That is the "
                     "check that has found the most, so it being quiet is the single "
                     "best sign available.\n")

    # ---- 2. severities the measurement does not support ---------------------
    if disc:
        rows = disc["rows"]
        unsupported = [
            r for r in rows
            if r.get("judgeable") and r["severity"] != "info" and not r["thin"]
            and isinstance(r.get("weighted_ratio"), (int, float))
            and r["weighted_ratio"] < UNSUPPORTED_RATIO
        ]
        if unsupported:
            body = ["These security rules carry a scoring severity, but fire about as "
                    "hard on working code as on deliberately broken code.\n"]
            for r in sorted(unsupported, key=lambda r: r["weighted_ratio"]):
                body.append(f"- `{r['rule']}` — {r['weighted_ratio']:.1f}x weighted, "
                            f"severity **{r['severity']}** "
                            f"({r['clean_hits']} on good code, {r['vuln_hits']} on broken)")
            body.append("\nBefore demoting, check the two ways this measurement lies: "
                        "is the finding count dominated by test fixtures the tool "
                        "already discounted, and is the denominator the language the "
                        "rule actually fires on? Both have produced a wrong answer here "
                        "before. If the ratio survives both, demote to `info` — which "
                        "reports at zero weight rather than deleting the rule.")
            item("HIGH", "Severities the measurement does not support", *body)

    # ---- 3. rules with no controls ------------------------------------------
    ledger = know.get("rules") or {}
    trained = set(ledger)
    no_controls = []
    for rid, st in ledger.items():
        # The control ledger is the "negatives" side: look-alikes the rule was
        # shown and should have ignored. Zero of them with a non-zero positive
        # count is the state where recall reads 1.0000 and means nothing.
        neg = st.get("synthetic_negatives")
        if neg is None:
            neg = (st.get("synthetic_clean_pass") or 0) + (st.get("synthetic_false_alarm") or 0)
        pos = st.get("synthetic_positives")
        if pos is None:
            pos = (st.get("synthetic_detected") or 0) + (st.get("synthetic_missed") or 0)
        if pos and not neg:
            no_controls.append(rid)
    if no_controls:
        body = ["Recall is measured; the false-alarm rate is not measured at all. "
                "A rule in this state that fires on everything scores a perfect "
                "1.0000 and looks like the best rule in the tool.\n"]
        body += [f"- `{r}`" for r in sorted(no_controls)]
        body.append("\nWrite controls in `tools/inject.py`. A control must be "
                    "something a careful reader would also call harmless — pairing "
                    "names and values at random produced `client_secret=my-tls-cert-2024` "
                    "and measured the generator instead of the rule.")
        item("HIGH", "Rules whose false-alarm rate is unmeasured", *body)

    # ---- 4. rules nothing has ever exercised --------------------------------
    if disc:
        fired = {r["rule"] for r in disc["rows"]}
        declared = {f"arbiter/resource.{r['id']}" for r in _load_resource_rules()}
        silent = sorted(declared - fired - trained)
        if silent:
            body = ["These rules fired on none of the corpus and have no injection "
                    "trials. Nothing is known about them in either direction — they "
                    "are assertions, not measurements.\n"]
            body += [f"- `{r}`" for r in silent]
            body.append("\nEither add a repository that exercises them, or add an "
                        "injection case. An injection case is cheaper and proves the "
                        "rule works mechanically; a repository proves it is right "
                        "about real code. The two find different failures.")
            item("MEDIUM", "Rules nothing has ever exercised", *body)

    # ---- 5. stacks with no broken counterpart -------------------------------
    if disc:
        loc = disc.get("loc_by_population_language") or {}
        good, bad = Counter(loc.get("clean", {})), Counter(loc.get("vulnerable", {}))
        gaps = sorted(
            ((lang, g, bad.get(lang, 0)) for lang, g in good.items()
             if g >= MIN_LANG_LINES and bad.get(lang, 0) < g / 50 and lang != "unknown"),
            key=lambda t: -t[1],
        )
        if gaps:
            body = ["A language that appears only in well-maintained repositories "
                    "cannot be measured. There is nothing to compare its rules "
                    "against, so they can be asserted but never tested.\n"]
            for lang, g, b in gaps[:12]:
                body.append(f"- **{lang}** — {g:,} lines of good code, "
                            f"{b:,} broken")
            body.append("\nAdd a deliberately-vulnerable repository for each, to "
                        "`tools/fetch_corpus.sh` and `tools/corpus.py`. Closing eight "
                        "of these gaps is what surfaced three false criticals that "
                        "had survived every injection trial ever run.")
            item("MEDIUM", "Stacks with no deliberately-broken counterpart", *body)

    # ---- 5a. a control mapped to a check that never fires --------------------
    if disc:
        seen = {r["rule"] for r in disc["rows"]}
        seen |= set(ledger)
        try:
            from arbiter.controls import load_frameworks
            dead: dict[str, list[str]] = {}
            for fw in load_frameworks(args.packs):
                for c in fw.controls:
                    missing = [ch for ch in c.satisfied_by
                               if ch.startswith("arbiter/") and ch not in seen]
                    if missing and len(missing) == len(c.satisfied_by):
                        dead.setdefault(fw.id, []).append(f"{c.id} -> {', '.join(missing[:3])}")
        except Exception:  # noqa: BLE001 - the dead-control check is advisory; a pack that will not load leaves it empty
            dead = {}
        if dead:
            body = ["A control whose every covering check never fires anywhere in the "
                    "corpus can never be reported as violated. It will read as "
                    "SATISFIED forever, in a compliance report, on any codebase.\n"]
            for fw, items in sorted(dead.items()):
                body.append(f"- **{fw}**")
                body += [f"    - {x}" for x in sorted(items)[:6]]
            body.append("\nEither the check is dead and the mapping should go, or the "
                        "check is alive and the corpus does not exercise it — in which "
                        "case add a repository or an injection case that does. A "
                        "permanent pass is the worst thing a compliance report can "
                        "contain, because it looks exactly like evidence.")
            item("HIGH", "Controls whose checks never fire — a permanent false pass", *body)

    # ---- 5b. nobody has adjudicated a real finding --------------------------
    adjudicated = know.get("adjudicated") or {}
    if ledger and not adjudicated:
        item("HIGH", "No real finding has ever been reviewed by a person",
             "Calibration reads **only** the adjudicated ledger — the one a person "
             "fills in — and it is empty, so the calibration machinery is currently "
             "doing nothing at all.\n",
             "That separation is deliberate. Faults the injector generates come from "
             "a pattern somebody chose, so they measure whether a rule works "
             "mechanically. Only a person looking at a real finding on real code says "
             "whether the things it flags are worth flagging. Mixing them would let a "
             "hundred thousand generated cases drown out ten real ones — which is why "
             "no amount of nightly running will ever fill this in.\n",
             "It is also the one gap that no schedule and no session can close, "
             "because it is a judgement about what you would actually act on.\n",
             "```\narbiter scan ./some-repo\narbiter feedback f:8c41 --false-positive"
             "\narbiter feedback f:9d02 --true-positive\narbiter learn\n```\n",
             "Twenty adjudications on one rule is the point where `arbiter learn` "
             "stops calling it unproven. A dozen on the noisiest rules is worth more "
             "than another million trials.")

    # ---- 5c. real-world pairs waiting for a verdict --------------------------
    unconfirmed = [p for p in pairs if p.get("confirmed") is None]
    if unconfirmed:
        by_rule = Counter(p["rule"] for p in unconfirmed)
        body = ["A commit where a maintainer changed code a rule fired on, after "
                "which it stopped firing. This is the only evidence in the whole "
                "system that the tool did not generate for itself — nobody wrote "
                "these commits to be found by a scanner.\n"]
        for rule, count in by_rule.most_common(8):
            body.append(f"- `{rule}` — {count} candidate pair(s)")
        body.append("\nEach one is a CANDIDATE: a finding also disappears when the "
                    "code around it is rewritten for unrelated reasons. Confirm that "
                    "the change addressed the finding, then it becomes a permanent "
                    "regression case — this rule must fire on the parent commit and "
                    "must not fire on the child, forever. A rule that fires on both "
                    "sides of a commit that plainly fixed the thing is wrong, and "
                    "nothing else in the pipeline would have told you.")
        item("HIGH", f"{len(unconfirmed)} real-world fix pairs waiting for a verdict",
             *body)

    # ---- 5d. contested findings, the cheapest thing to learn ------------------
    contested = disagree.get("contested") or []
    gaps_by_family = Counter(r.get("family", "?") for r in (disagree.get("coverage_gap") or []))
    if contested:
        body = [f"{len(contested)} findings where both Arbiter and an external "
                "analyzer cover the kind of defect, and only one of them fired. "
                "Exactly one is wrong about that line, so a verdict there resolves a "
                "real uncertainty instead of confirming a settled one.\n"]
        only = Counter(r.get("only", "?") for r in contested)
        for who, count in only.most_common():
            body.append(f"- {count} where only **{who}** fired")
        body.append("\nA batch is already prepared. Twenty of these are worth more "
                    "than twenty random findings, because a finding two independent "
                    "tools agree on is the least informative thing a person can spend "
                    "a verdict on.")
        item("HIGH", "Contested findings, ready to adjudicate", *body)
    if gaps_by_family:
        body = ["An external analyzer checks something Arbiter has no rule for at "
                "all. This is not a disagreement and adjudicating it teaches nothing "
                "— it is a list of rules worth writing.\n"]
        for fam, count in gaps_by_family.most_common(8):
            body.append(f"- **{fam}** — {count} finding(s) nothing native covers")
        item("MEDIUM", "Kinds of defect only the external tools catch", *body)

    # ---- 6. too little evidence to judge ------------------------------------
    if disc:
        thin = [r for r in disc["rows"] if r["thin"] and r.get("judgeable")]
        if thin:
            body = ["Not a defect — a gap. Fewer than five findings in total, so the "
                    "ratio beside each is arithmetic rather than evidence.\n"]
            for r in sorted(thin, key=lambda r: r["rule"]):
                body.append(f"- `{r['rule']}` — {r['clean_hits']} good, "
                            f"{r['vuln_hits']} broken")
            body.append("\nMore repetitions will not help. Effective sample size is "
                        "bounded by how many different kinds of code and fault exist, "
                        "not by trial count. These need more varied code.")
            item("LOW", "Rules with too little evidence to judge", *body)

    if item_count == 0 and not missing:
        L.append("\nNothing queued. Every check came back clean, which means the "
                 "corpus has stopped telling us anything new — the next useful move "
                 "is to widen it, not to run it again.\n")

    # ---- context for whoever reads this next --------------------------------
    L.append("\n---\n\n## Where things stand\n")
    if corpus:
        for pop in ("vulnerable", "clean", "examples"):
            g = [r for r in corpus["rows"] if r["expectation"] == pop]
            if not g:
                continue
            lo = sum(r["loc"] for r in g)
            L.append(f"- **{pop}**: {len(g)} repos, {lo:,} lines, "
                     f"{sum(r['findings'] for r in g)} findings, "
                     f"{sum(r['critical'] for r in g)} critical, "
                     f"{sum(r['high'] for r in g)} high")
    if ledger:
        L.append(f"- **{len(ledger)}** rules have injection results; "
                 f"**{len(know.get('adjudicated') or {})}** findings have been "
                 "reviewed by a person")
    L.append("\nThe measurement traps that have produced a wrong answer here before, "
             "all three worth re-reading before acting on any number above:\n")
    L.append("1. Counting teaching repositories as production code — it made the "
             "noise rate look four times worse than it was.")
    L.append("2. Dividing by whole-repository size — it makes a Kubernetes rule look "
             "spotless inside a large Go project.")
    L.append("3. Counting findings the tool already discounted as test fixtures — "
             "195 of 203 findings for one rule were exactly that.")

    text = "\n".join(L) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
        print(f"  wrote {args.out} ({item_count} items queued)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
