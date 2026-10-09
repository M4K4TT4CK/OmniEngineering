# Calibration and Learning

Adapting and being reproducible pull against each other: if behaviour depends on
history, the same commit passes on Monday and fails on Tuesday, and baselines,
gates and accreditation artefacts stop meaning anything. Arbiter separates the
two.

## Contents

- [Offline learning, pinned execution](#offline-learning-pinned-execution)
- [Three limits that keep it honest](#three-limits-that-keep-it-honest)
- [Adjudication](#adjudication)
- [The nightly job](#the-nightly-job)
- [Calibrating someone else's tool](#calibrating-someone-elses-tool)

The constants this page relies on -- twenty observations, the ratio bands,
the confidence interval -- are listed with how firmly each is held in
[testing-parameters.md](testing-parameters.md).

---

## Offline learning, pinned execution

```bash
arbiter feedback f:8c41d2ae9b07 --false-positive --note "vendored fixture"
arbiter learn                                  # what has been learned, and what it supports
arbiter scan . --pin-knowledge k:4cf3d3fa2f02  # freeze it for a release gate
```

Learning is offline and accumulates in `.arbiter/knowledge.json`. Execution reads
**one pinned knowledge version** and records its hash, so
`(commit, config, knowledge version)` always produces identical bytes.

`.arbiter/knowledge.json` holds, for every rule:

- how many planted faults it was shown, and how many it caught
- how many look-alikes it was shown, and how many it correctly ignored
- how many real findings a person reviewed and judged right or wrong

Those last numbers are kept separate from the first two on purpose. Generated
faults come from a chosen pattern, so they tell you whether a rule works
mechanically. Only a person looking at a real finding tells you whether the
things it flags in real life are worth flagging. Mixing the two would let a
hundred thousand generated cases drown out ten real ones.

## Three limits that keep it honest

- **Confidence moves, severity never does.** How often a rule is right is
  measurable; how much it matters when it is right is a policy judgement.
- **Learning never changes a gate outcome** unless `gate.use_calibration` is set.
- **One finding moves the statistics once.** Re-adjudicating a fingerprint is
  refused.

A rule under 20 adjudicated observations reports as **unproven** rather than
inheriting a flattering estimate from a handful of samples.

## Adjudication

Calibration reads one ledger: findings a person judged right or wrong. Not the
injection trials, not the corpus discrimination — those measure whether a rule
works mechanically and whether it separates populations, and neither answers
whether the things it flags are things you would act on.

That ledger sat at zero, because adjudicating meant copying fingerprints one at
a time.

```bash
arbiter review report.json --html          # one self-contained page
arbiter review report.json --interactive   # one keypress per finding
arbiter review --apply review.md           # record the verdicts
```

The page has no server, no network and no build step, so it works from a phone
with the wifi off — which matters, because the reports worth adjudicating are
often the ones you cannot send anywhere. One finding at a time with the code
around it: adjudicating from a list encourages skimming, and a skimmed verdict
is worse than none, because this ledger is the only thing calibration reads.

Which findings you see is the whole question. The batch is chosen to move the
most rules past the twenty-observation line — rules already close to the
threshold first, spread across files so twenty instances of one mistake are not
counted as twenty observations, never re-asking an adjudicated finding.

### Every verdict carries a name

A verdict records who is answerable for it, when it was made, and which command
it came through. The reviewer comes from `--reviewer`, or from
`git config user.email`; there is no third fallback, and a command with neither
refuses rather than writing an anonymous mark. Something like the OS username
would put a name on a permanent record without anyone choosing it.

Permanence is the reason. This ledger refuses to re-adjudicate a fingerprint, so
a mark cannot be corrected — and a mark that is both permanent and anonymous
cannot even be distrusted, because there is no way to find which ones to doubt.
Attribution does not make a verdict right. It makes a bad batch findable.

**It is attribution, not authentication.** Nothing here establishes that a
person rather than a script produced a mark, and it should never be described
as though it does. `entry_point` narrows where to look and no further. The rule
that verdicts come from people is a procedural one; what the code guarantees is
that every verdict has a name attached and that no verdict is written without
one.

Verdicts recorded before this existed migrate with the reviewer
`unattributed`. They are real evidence and are kept, but they are not given a
plausible name on the way through — an invented one would read as a fact later.

### A true positive can open a failure-ledger entry

`arbiter review --apply FILE --ledger .ai/failures/failure-ledger.json` drafts
one `open` entry in the OmniEngineering failure ledger for every verdict it
records as a true positive: the finding's title, severity, rule and location,
and a `how_detected` naming the finding id so applying the same marks twice
adds nothing. Root cause, fix and regression test are left for the person who
fixes it (`omni failure update FAIL-### ...`); the bridge records what the
scanner knows and nothing it does not. The file it writes passes
`omni failure check`, and `omni graph why <path>` then shows the finding
beside the requirements and tests already attached to that file.

### Adjudicating needs a terminal

`arbiter feedback` and `arbiter review --interactive` check that stdin is a
terminal and refuse when it is not. Piping into either one — from a script, a
CI step, an agent shelling out — gets a refusal rather than a verdict.

**This is a guard against accident, not proof of personhood.** Anything
determined allocates a pseudo-terminal and walks straight through, and that is
not a gap to be closed later; no check available to a local CLI can tell a
person from a program that wants to look like one. What it stops is the case
that actually happens: something writes a permanent mark nobody remembers
making, into a ledger that refuses to re-adjudicate it.

A deliberate batch import is legitimate, so there is a way to do one:

```bash
arbiter feedback f:8c41d2ae9b07 --false-positive --batch   # recorded as an import
arbiter review report.json --apply review.md               # the batch path proper
```

The override is easy to pass on purpose. An override an agent cannot pass is an
override a person cannot pass either, so the value is not the obstacle — it is
the record. Both paths record an entry point that says no terminal was
involved: `feedback-batch` and `review-apply`, alongside `import`. Those three
are the set worth filtering on later, because they are the verdicts that could
have been produced by something that was not a person.

`review --interactive` has no override, because there is no coherent one. A
keypress interface driven by something that is not a keyboard is a batch import
wearing another name, so the refusal points at `--apply`, which is honest about
what it is.

## The nightly job

`.github/workflows/train.yml` runs `tools/train_cycle.sh` at 08:00 UTC. It
downloads the practice repositories, runs every rule against them, measures
discrimination, plants faults, checks the tool never overclaims, runs the
tests, and writes the results back to the repository. **Nothing in it changes a
rule.** It measures and records; deciding what a result means needs a person,
and `training/WORKLIST.md` is the handoff.

Five files accumulate across runs and are committed:

| File | What it holds |
|---|---|
| `.arbiter/knowledge.json` | the ledger — synthetic counts, and any adjudications |
| `.arbiter/external-severity.json` | measured severities for external checks |
| `training/WORKLIST.md` | what to look at next, and why |
| `training/fix-pairs.json` | real before/after pairs mined from history |
| `training/disagreements.json` | where two analyzers contradict each other |

Everything else the cycle writes is stamped per run and deliberately ignored.
It is one night's logs, and keeping it would grow the repository without
telling a later run anything.

### The write-back is checked before the work, not after

Run 1 measured for fifty-five minutes and committed none of it. `training/`
matched a gitignore entry, so `git add -A .arbiter training` exited 1, and
`bash -e` failed the job two seconds after the cycle had finished successfully.
Every number was thrown away, and the failure looked like the measurement had
broken rather than the bookkeeping.

`tools/check_writeback.sh` now runs first, in CI as its own step and inside the
cycle itself. It asserts none of the five is ignored and dry-runs the exact
`git add` the job ends with. The check is instant and the cycle is not, so a
write-back that cannot happen is a reason not to start. A push that loses a race
to another commit is the same lost night by a different route, so the job
rebases before pushing and fails the step if that does not work.

## Calibrating someone else's tool

Checkov's open build reports `"severity": null` on every finding. Arbiter's
adapter invented `medium` for all of them, so one Terraform repository produced
477 findings of identical weight and no way to tell the two that matter from the
475 that do not.

`tools/calibrate_external.py` measures every individual external check — every
`CKV_AWS_*`, every bandit `B*` — against the same three-population corpus used
for native rules, and assigns severity from the measured ratio:

| Measured ratio | Assigned |
|---|---|
| ≥ 10× | medium |
| ≥ 3× | low |
| < 3× | info — reported, zero weight |
| fewer than 5 observations | nothing assigned; no claim made |
| seen in only one repository | capped at low |

Note the ceiling: **measurement cannot promote a check to `high`.**
Discrimination measures *signal* — how much more often a check fires on broken
code. Severity encodes *consequence* — how much it matters when the check is
right. Those correlate and are not the same quantity.

The example that forced the rule: "Ensure every security group and rule has a
description" fires 33 times on deliberately broken Terraform and zero times on
the well-maintained `terraform-aws-modules` repositories, which are meticulous
about descriptions. The ratio is real, reproducible and stack-matched. It is
also not a security finding, and grading it `high` would put a missing comment
on the same footing as a public S3 bucket. So measurement may say "this carries
signal" and may say "this carries none"; it may not manufacture a claim about
consequence.

On Terragoat, 477 findings of identical `medium` became 296 medium, 151 low and
30 info — 181 regraded from measurement.

This does not breach the standing rule that learning never touches severity. For
a native rule, severity is a deliberate policy statement and nothing may move it.
For an external check the tool supplied *no* severity — replacing an invented
constant with a measured one is not drift. The table lives in the knowledge
file, is part of its version hash, and `--pin-knowledge` still fails a run if it
moved.
