# CI and Automation

## Contents

- [GitHub Actions](#github-actions)
- [GitLab CI](#gitlab-ci)
- [Any other runner](#any-other-runner)
- [Scanning only what changed](#scanning-only-what-changed)
- [A ready-made workflow](#a-ready-made-workflow)
- [Choosing a gate](#choosing-a-gate)
- [Arbiter's own CI](#arbiters-own-ci)
- [Continuous training](#continuous-training)

---

## GitHub Actions

```yaml
# .github/workflows/arbiter.yml
- uses: ./ci/github-action
  with:
    target: .
    profile: ci
    baseline: .arbiter/baseline.json
```

The action runs `arbiter gate --github`, so one run reaches the pull request
three ways:

| Where | What | How |
|---|---|---|
| the job log and the diff | one annotation per finding: `::error` for what failed the gate, `::notice` for findings in files the change did not touch, `::warning` for the rest | workflow commands on stdout, on automatically when `GITHUB_ACTIONS=true` |
| the job summary | the pull-request comment: verdict, reasons, grade, what was not assessed | appended to `$GITHUB_STEP_SUMMARY` |
| the Security tab | the active findings at or above `--sarif-min-severity` (pr-check uses `medium`; without the flag, every active finding), with rule and remediation | `report.sarif` uploaded with `github/codeql-action/upload-sarif` |

The SARIF upload needs `permissions: security-events: write` (and `contents:
read`) on the job or the workflow; the annotations and the summary need
nothing beyond the default token. The upload step runs with
`continue-on-error: true` and `if: always()`: code scanning is not enabled on
every fork or mirror, and a refused upload must not turn the gate's own
verdict into a failure, while a failed gate is exactly the run whose findings
should be visible. Annotations never carry evidence, only the rule id, the
title and the location, so the log is not where a credential gets reprinted.

## GitLab CI

A template is in `ci/gitlab/arbiter.gitlab-ci.yml`.

## Any other runner

Arbiter is a Python package with one runtime dependency, and the gate is an exit
code:

```bash
pip install -e .
arbiter gate . --profile ci --baseline .arbiter/baseline.json --format json,sarif
```

| Code | Meaning |
|---|---|
| `0` | pass |
| `1` | gate failure |
| `2` | error |

## Scanning only what changed

A full scan costs about a minute per half-million lines. That is fine nightly
and too slow to sit in front of a merge. Caching file reads bought 20%, which
was the measurement that mattered: reading was never the bottleneck, the
analysis is.

So `--changed` reads less.

```bash
arbiter gate . --changed origin/main --baseline .arbiter/baseline.json
```

Every probe declares a scope. **file** means every finding depends only on the
file it is in — a hardcoded secret is a secret whether or not the rest of the
tree was read. **repo** means the answer depends on relationships between
files, and a subset-based answer is not weaker, it is false. **change** means
the probe reads a few files it names itself plus the changed paths — the
`governance` probe, which holds the failure ledger against the change — so a
partial scan is the question it was built for, and it runs in both modes.

In a partial scan the file-scoped and change-scoped probes run, and the
repo-scoped ones are recorded as skipped with the partial scan named as the
reason — so they stay in the coverage denominator as not-assessed rather than
vanishing. The files read
are the changed ones plus dependency manifests, lockfiles, CI workflows and
Terraform, which is where a rule genuinely reasons about a file it is not
reporting on.

**All five external analyzers are repo-scoped, so a partial scan does not run
them.** ruff, bandit, checkov, semgrep and gitleaks each declare `scope = "repo"`
in their manifest, and a `--changed` run records all five as not assessed. The
reason is different from the one that holds back a native repo probe: those
genuinely reason across files, whereas nobody has measured whether these five
return the same findings from a subset. Until someone does, claiming they do
would be a guess printed as a fact. The practical consequence is worth stating
plainly — **a pull-request gate runs Arbiter's own probes and no third-party
analyzer at all.**

Traefik, 171 changed files of 2,293: **59s → 6s**. The file-scoped findings are
identical to the full scan's on the files both read — 158 and 158, nothing
missing, nothing extra. `scope="file"` is a claim of exactness, so it is tested
rather than assumed.

### What a partial scan may not say

- The overall grade is **withheld outright**. The gate is a question a subset
  can answer ("did anything cross a threshold"); a grade is a summary of the
  repository, and a number that looks like one but describes a diff is the
  thing this tool exists not to produce.
- "Probe ran and found nothing" becomes "found nothing in the files it was
  given".
- No claim in the report is scoped complete except the coverage measurement
  itself, which is a statement *about* the incompleteness.

That last one is invariant **CI-11**, and it caught a real bug while this was
being built: a dimension can reach 100% check coverage in a partial scan,
because every probe carrying it is file-scoped and ran — having read a third of
the files. Fixed at the source rather than exempted.

A ref that does not exist refuses rather than scanning nothing. An empty diff
and a failed diff look identical downstream, and the second would produce a
green gate that read no files at all.

Findings that land in a context file the branch did not touch are tagged
`outside-this-change`, so a pull request is not blamed for a lockfile it never
opened. The baseline is what keeps them out of the gate.

Findings in the change are tagged `req:<ID>` for the requirement ids the
commits since the base that touched their file cite (the prefix comes from
the registry's `requirement_id_prefix`, `REQ` by default; old ids resolve
through `id_aliases`), marked `req-scope:commits`. A file no such commit cited
carries every id the range cites and `req-scope:open` instead, so the tags
read as context rather than attribution. The Markdown report and the
pull-request comment summarise them in a **By requirement** table that names
the scope.

## A ready-made workflow

`examples/pull-request-gate/` holds a two-job workflow and the config that goes
with it:

| | pull request | nightly / main |
|---|---|---|
| what it reads | the changed files, plus manifests, lockfiles, CI and Terraform | everything |
| how long | seconds | a minute per half-million lines |
| what it blocks on | anything critical, anything new at high | nothing; it reports |
| grade | withheld | reported |
| refreshes the baseline | no | yes, from the default branch only |

The nightly job is the only one allowed to refresh the baseline. Refreshing it
from a partial scan would quietly forgive every finding in the files that scan
did not read.

## Choosing a gate

The `ci` profile forbids network and model calls, which makes a run fast and
deterministic — the same commit produces the same bytes.

Three settings matter more than the rest:

```yaml
gate:
  fail_on:
    severity: critical    # consequence
    new: high             # direction of travel
    coverage_below: 0.60  # refuse to certify a thin scan
```

`new: high` is what makes adoption survivable on an existing codebase: the
baseline absorbs what is already there, and the build fails on what you add.
Because fingerprints exclude line numbers, reformatting does not invalidate the
baseline.

`coverage_below` is the guard against a reassuring result from a scan that
barely ran. Without it, an analyzer that failed to install looks identical to an
analyzer that found nothing.

Refresh the baseline deliberately, never automatically:

```bash
arbiter baseline arbiter-out/report.json
```

## Arbiter's own CI

Two workflows, split by how long they take rather than by what they cover.

| | `pr-check.yml` | `train.yml` |
|---|---|---|
| when | every pull request | 08:00 UTC nightly |
| what | the fast tier of the test suite (`-m "not slow"`), `tools/integrity.py`, `tools/mutate_tests.py`, `python omni doctor`, `python omni gate`; on Linux also the five analyzers, the `api` extra, and `arbiter gate .`; and, in a parallel `slow-tier` job, the slow tier (`-m slow`) with the analyzers installed | the full measurement cycle |
| how long | Windows about two minutes; the Linux cell is dominated by the self-gate's semgrep pass (about six minutes over this tree) now that the adapter tests have moved to the slow tier; the slow tier is about five minutes locally, most of it one full scan through every analyzer | about two hours |
| platforms | ubuntu-latest **and** windows-latest; the slow tier on ubuntu-latest only | ubuntu-latest |
| writes to the repo | no | yes — the five accumulating files |

**Why the pull-request check runs on two platforms.** ARB-006 was a crash on
every adapter timeout on Windows: `os.killpg` does not exist there, so the
process-group kill path raised `AttributeError` and the analyzer outlived the
timeout meant to stop it. It was found by hand, months later, because no
automation had ever run on Windows. UTF-8 decoding (ARB-007) and path handling
are sensitive the same way, and a developer's machine is not a control.

The matrix is on this workflow and not the nightly one deliberately. Running a
two-hour measurement cycle twice would cost four hours to learn what a one-minute
test run already tells you, and the corpus measurements are not platform-
sensitive — the code paths around them are.

`fail-fast: false`, because cancelling the Windows job when Linux fails hides
precisely the class of defect the matrix was added to find. Neither job carries
`continue-on-error`: an advisory check is a check nobody reads.

**Arbiter evaluates itself.** The Linux job ends with `arbiter gate .` under
this repository's own `arbiter.yaml`: fail on any critical, fail on any new
high, and with no baseline every finding is new. It runs after
`tools/install_tools.sh`, so the external analyzers are present and the
self-gate cannot pass by failing to run half its probes. The first time it ran
it reported three high findings: the `authored` probe matching the TLS-off
patterns listed in its own module docstring (ARB-032). The Windows job does
not run the self-gate, because not every analyzer installs there and a gate at
lower coverage would answer a different question. The same job installs the
`api` extra, so the sixteen hosted-API tests run on every pull request instead
of skipping (ARB-034).

**Two tiers (ARB-046).** The suite's wall clock was almost entirely the tests
that run a real analyzer through the adapters or scan something larger than
the fixtures, and none of those says anything about the platform. They carry
the `slow` marker (registered in `pyproject.toml`); the matrix runs
`-m "not slow"` on every cell and the `slow-tier` job runs `-m slow` once, on
Linux, alongside the matrix, so the pull request waits for the slower of the
two rather than their sum. `.ai/test-suites.json` registers the fast tier as
the gate's suite and the slow tier as an integration suite the gate does not
run. Locally the fast tier takes about two minutes and the slow tier about five, measured on a loaded four-core machine where the whole suite took eleven minutes before the split.

**Skips are printed, not counted.** The pull-request check runs `pytest -rs`,
so every skip appears in the log with its reason. Windows legitimately skips
things Linux does not, and a skip that is silently absent from the output reads
exactly like a test that was never collected. The nightly cycle still runs
`pytest -q` and tails three lines, because its job is the corpus measurements
and the pull-request check has already answered this question on both
platforms by then.

**The Windows branch is also tested on Linux.** `_kill_tree` is unreachable on a
machine that has process groups, so four tests take the platform away instead of
waiting for one — deleting `os.killpg`, then asserting the fallback kills a real
process, uses `taskkill /T` rather than killing only the direct child, and still
kills the process when `taskkill` is missing or refuses. The matrix and those
tests answer different questions and neither replaces the other.

## Continuous training

`.github/workflows/train.yml` runs the measurement cycle nightly on GitHub's
runners and commits the results back. **It never edits a rule. It only
measures.** A run takes about ten minutes.

Each run:

1. downloads the practice repositories, if they are not already present
2. runs every rule against them and records what it found
3. measures each rule's ability to tell good code from broken code
4. plants known faults and checks the rules catch them
5. checks the tool never claims to have checked something it skipped
6. runs the test suite
7. commits the results back to the repository

Because step 7 writes to the repository, the next run starts from everything the
previous runs learned.

Run a cycle by hand at any time:

```bash
./tools/train_cycle.sh           # about fifteen minutes
PUSH=1 ./tools/train_cycle.sh    # and save the results
```

### The half that needs a person

Deciding what a result *means* is the part a schedule cannot do: whether a
finding on a well-maintained repository is the rule's fault or the code's,
whether a ratio is real or an artefact of how it was measured. Every real defect
found so far came from that half.

`training/WORKLIST.md`, regenerated by every nightly run, is the handoff. It
ranks what to look at and says why, so a session starts from a question rather
than a pile of tables.

Its six checks, in the order they have actually paid off:

| Check | Why it is there |
|---|---|
| Critical or high on well-maintained code | The strongest signal available. Three real defects in one afternoon, including a critical on three literal dots. |
| A severity the measurement does not support | A rule that fires no harder on broken code is describing a style, not detecting a defect. |
| A rule with no controls | The dangerous state, because it looks perfect: recall reads 1.0000 whether the rule is precise or fires on everything. |
| A rule nothing has ever exercised | An assertion wearing the costume of a measurement. |
| A stack with no broken counterpart | Its rules cannot be measured at all. Closing eight of these found three criticals on good code. |
| No finding reviewed by a person | Calibration reads **only** the adjudicated ledger. No schedule can fill it, because it is a judgement about what you would act on. |

An empty queue is itself a finding: the corpus has stopped teaching anything,
and the next useful move is to widen it, not to run it again.

Once a fortnight is enough. The nightly job keeps the evidence current in the
meantime.

### Two things to know about the numbers

**Running more trials does not make the tool more trustworthy past a point.**
Twenty thousand faults generated from fifteen patterns is closer to fifteen
independent tests than to twenty thousand. What improves the evidence is more
kinds of fault and more kinds of code, not more repetitions. That is why the
practice set spans thirteen languages and five infrastructure formats, and why
every fault is planted into a real file rather than a made-up one.

**Most real defects have come from widening, not from repeating.** Every time the
practice set grew, it found something the previous set could not. See
[evidence.md](evidence.md#every-stack-needs-a-broken-counterpart).
