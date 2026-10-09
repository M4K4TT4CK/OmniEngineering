# Architecture

## Contents

- [Module layout](#module-layout)
- [The scan pipeline](#the-scan-pipeline)
- [Probe outcomes and coverage](#probe-outcomes-and-coverage)
- [Fingerprinting](#fingerprinting)
- [Result cache](#result-cache)
- [Scoring](#scoring)
- [The report](#the-report)

---

## Module layout

```text
src/arbiter/
  core.py        Finding, Location, Report, Scorecard, fingerprinting
  inventory.py   acquire + classify + stack detection
  graph.py       TF / CFN / K8s / plan JSON → normalized Resource
  probes.py      native probes and the probe contract
  adapters.py    declarative external-tool adapters
  policy.py      config, profiles, suppressions, scoring, gate
  engine.py      the scan pipeline
  cache.py       the persistent per-file result cache
  claims.py      the claim ledger and its invariants
  controls.py    control-framework evaluation
  learn.py       knowledge file, calibration, adaptive thresholds
  review.py      adjudication batch selection
  review_ui.py   the offline review page and terminal walkthrough
  report.py      json · sarif · console · markdown · html
  ab.py          the A/B harness
  cli.py         commands
  packs/         rule packs, control packs and adapter manifests (data, not code)
fixtures/        synthetic repos with known planted defects
tests/           unit + golden-fixture regression tests
tools/           corpus, injection, discrimination and training tooling
```

Rule packs, control packs and adapter manifests are **data**. Adding a
framework, an external analyzer or a resource rule does not change the core.

## The scan pipeline

`engine.run_scan` executes in a fixed order. Each step exists to make a
particular dishonesty impossible.

1. **Register adapters.** Registration happens before capability checks, so an
   uninstalled tool counts as *not assessed* rather than vanishing from the
   coverage denominator. A tool that is not registered cannot be reported as
   missing.
2. **Resolve the profile** into a capability set (network, model).
3. **Clear the file-read cache.** One scan, one view of the files — a previous
   scan's bytes must never be served for this one's paths.
4. **Resolve targets**, from paths or a system manifest.
5. **Build the inventory**: acquire, classify by language and role, detect
   stacks.
6. **Build the resource graph** from Terraform, CloudFormation, Kubernetes and
   any supplied plan JSON. A directory holding only a plan has no `.tf` files to
   detect, so Terraform is added to the stack set explicitly.
7. **Load knowledge**, once. If `--pin-knowledge` is set and the version hash
   differs, the run fails rather than proceeding with different calibration than
   was pinned.
8. **Resolve adaptive thresholds**, when `quality.adaptive` is set.
9. **Run the probes**, each with an explicit outcome. A probe declared
   `cacheable` is shown only the files the result cache cannot answer for;
   its hits are assembled from the cache and are indistinguishable downstream.
10. **Post-process the findings**: deduplicate, apply calibration, apply
    severity overrides, apply the baseline, apply suppressions — in that order.
11. **Assemble the report**, including lines of code by language and by role.
12. **Compute the scorecard.**
13. **Evaluate control coverage.** Wrapped so that failing to evaluate a
    framework can never fail a scan.
14. **Evaluate the gate.**
15. **Build the claim ledger and verify it.** A report that fails its own
    integrity check is a bug in Arbiter, not a finding about the target.
16. **Clean up** temporary workspaces unless `--keep-workspace` is set.

Suppressions are applied *after* the baseline and calibration, and never delete
a finding — it stays in the report, marked and attributed.

## Probe outcomes and coverage

Every probe produces a `ProbeOutcome` whatever happens, which is what makes
coverage computable. Skip reasons are evaluated in a fixed precedence:

| Order | Condition | Recorded reason |
|---|---|---|
| 1 | not selected by `--only` | `not selected on the command line` |
| 2 | disabled in configuration | `disabled in configuration` |
| 3 | not applicable to this tree | the probe's own explanation |
| 4 | required binary absent | `missing binary: <name>` |
| 5 | profile forbids network | `profile '<name>' forbids network access` |
| 6 | profile forbids model calls | `profile '<name>' forbids model calls` |

Two distinctions carry real weight:

- **Not applicable is not the same as prevented.** A probe that could never have
  applied here — no Python in the tree, a single repo so there are no seams —
  sets `applicable = False`. Conflating the two would make a control look
  unassessed forever in a codebase the check has no business running in.
- **Unconfigured is not an error.** A probe that could not be configured (no
  model provider, for instance) raises `Unavailable` and is recorded as skipped
  with the reason. Reporting it as an error would be noise; reporting it as a
  clean pass would be a lie.

Each outcome declares `checks`, the number of rule classes it represents, so
coverage is accounted per check rather than per probe.

## Fingerprinting

A finding's ID is `f:` followed by the first twelve hex characters of

```text
sha256(rule_id ‖ repo_id ‖ path ‖ logical_address ‖ normalized_evidence[:200])
```

joined with a unit separator, and **deliberately excluding the line number**.

Reformat a file and the baseline survives; change a port number and it does not.
Without this, the "new findings only" gate turns into noise and people switch it
off — which costs far more than the occasional collision the shortened digest
risks.

## Result cache

`cache.py` keeps, in `.arbiter/cache.json` under the first target, one entry
per (probe, file): the findings the probe produced for that file, as produced,
before any post-processing. The key is

```text
sha256( sha256(file bytes) ‖ rules hash ‖ repo id ‖ path )
rules hash = sha256( config as sorted JSON ‖ probe.version ‖ Arbiter version
                     ‖ digest of Arbiter's own source ‖ the probe's context digest )
```

so an edited file, a changed configuration, a new release or an edited probe
all miss, and the same bytes at two paths are two entries. A clean file is
stored as an empty list, which is the common hit.

**What is never cached.** Only probes declared `cacheable` take part:
`secrets`, `supply_chain`, `ast_metrics`, `house_rules_ast` and `authored`.
The declaration claims the probe is file-local — the same file under the same
rules yields the same findings whether or not any other file is present — and
`tools/integrity.py` (CI-13) tests the claim on every run by scanning a file
alone and beside another. `house_rules` is file-scoped but not cacheable: a
`file_exists` rule reports on the repository, with no path to attribute to.
`authored` reads the manifests to decide whether an import is declared, so it
declares them as its context and their digest is in its key. Findings without
a path are never stored. Calibration, overrides, the baseline and
suppressions are never stored either: they run on everything, every scan.

The cache file is excluded from the inventory the way the output directory
is, so Arbiter never reads its own evidence back. `--no-cache` bypasses it,
`--verify-cache` re-runs a sample of hits and reports any divergence as
`arbiter/assurance.cache-divergence`, and CI runs without it: a pull-request
gate measures the tree in front of it.

## Scoring

Each finding's weight is `severity weight × confidence factor`:

| Severity | Weight | | Confidence | Factor |
|---|---|---|---|---|
| `critical` | 40.0 | | `high` | 1.0 |
| `high` | 16.0 | | `medium` | 0.6 |
| `medium` | 5.0 | | `low` | 0.3 |
| `low` | 1.5 | | | |
| `info` | 0.0 | | | |

`info` scores zero: the finding is reported, and carries no weight. That is how
a rule demoted by measurement stays visible instead of being deleted.

Dimension scores roll up with these default weights, overridable under `score`
in `arbiter.yaml`:

| Dimension | Weight |
|---|---|
| `security` | 0.30 |
| `quality` | 0.20 |
| `supply_chain` | 0.15 |
| `interface` | 0.15 |
| `compliance` | 0.10 |
| `drift` | 0.10 |
| `assurance` | 0.00 |

Each `DimensionScore` carries `checks_run` and `checks_applicable` alongside the
score, so a dimension's coverage is always legible next to its grade.

**The overall grade is withheld** when coverage falls below
`score.coverage_threshold` (default 0.60). The scorecard records `withheld` and
`withheld_reason` rather than emitting a number nobody should trust.

## The report

The JSON report is canonical; SARIF, HTML, Markdown and console output are
renderings of it. Beyond findings and probe outcomes it carries:

| Field | Why it is there |
|---|---|
| `loc_by_language`, `loc_by_role` | A Kubernetes rule can only fire on Kubernetes manifests. A rate is only meaningful against the code the rule could have fired on, so the denominator ships with the report. |
| `scorecard` | Per-dimension score and coverage, plus whether the overall grade was withheld. |
| `controls` | Per-framework coverage summary. A compliance figure quoted without its denominator is the thing this tool exists to stop doing. |
| `learning` | Knowledge version, adjudicated counts, what was recalibrated, adaptive thresholds used. |
| `claims` | Every assertion the report makes, with supporting and abstaining checks. |
| `integrity` | Whether the report passed its own invariant check, and any violations. |
| `gate` | The policy decision and what drove it. |

See [evidence.md](evidence.md#claim-integrity) for the claim ledger and its ten
invariants.
