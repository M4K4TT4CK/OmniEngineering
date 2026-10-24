# Configuration

`arbiter.yaml` at the repository root is the whole configuration surface. There
is no second config file, no environment-variable override matrix, and no
implicit user-level defaults. Arbiter looks for `arbiter.yaml`, then
`arbiter.yml`, then `.arbiter.yaml` in the first target; `--config FILE`
overrides that. Every key is optional: anything you leave out takes the default
in `DEFAULTS` in `src/arbiter/policy.py`. The only environment variables a scan
reads are credentials and cache locations (`ANTHROPIC_API_KEY` for the model
passes, `ARBITER_CACHE_DIR`, `ARBITER_NO_VERSION_MEMO`), never policy.

This repository's own [`arbiter.yaml`](../arbiter.yaml) is a working example.

## Contents

- [The file](#the-file)
- [Profiles](#profiles)
- [Model verification](#model-verification)
- [Gating](#gating)
- [Precision tiers](#precision-tiers)
- [Scoring](#scoring)
- [Choosing probes](#choosing-probes)
- [Suppressions](#suppressions)
- [House rules](#house-rules)
- [Adaptive thresholds](#adaptive-thresholds)
- [External analyzers](#external-analyzers)
- [Other keys](#other-keys)
- [Where state lives](#where-state-lives)

---

## The file

```yaml
version: 1
profile: offline          # offline | ci | assisted | connected | audit

gate:
  fail_on:
    severity: critical    # any critical fails the build
    new: high             # new highs fail; pre-existing ones don't
    coverage_below: 0.60  # refuse to certify a thin scan
  gate_on_inferred: false

suppress:
  - rule: "arbiter/quality.no-tests"
    reason: "tests live in a sibling repository"
    expires: 2027-01-31   # expiry is mandatory

rules:
  - id: code-note-anchors
    type: reference_integrity
    refs:    { files: "**/*.py", pattern: "#\\s*note:\\s*(?P<ref>[\\w./#-]+)" }
    anchors: { files: ".ai/code-notes/**/*.md", pattern: "^#+\\s*(?P<anchor>.+)$" }
    severity: high
```

## Profiles

A profile is a capability budget, not a severity setting.

| Profile | Network | Model | For |
|---|---|---|---|
| `offline` (default) | no | no | air-gapped and high-side environments |
| `ci` | no | no | fast, deterministic gating |
| `assisted` | only the model call | verification only | `ci` plus the model verification pass ([precision.md](precision.md#model-verification)) |
| `connected` | yes | yes | full depth: registry lookups, the judgement pass |
| `audit` | yes | yes | everything, no time budget |

A probe that needs a capability the profile forbids is skipped with that reason
recorded, so the report says what the profile cost you. Coverage falls by
exactly the amount that was not checked — the grade never quietly absorbs it.

Override per run with `arbiter scan . --profile connected`.

## Model verification

The `verify:` block configures the optional model verification pass, which
asks a model to confirm or reject each finding and records the verdict on it
(how it works: [precision.md](precision.md#model-verification)). It needs
`ANTHROPIC_API_KEY`; without one, every candidate is recorded as
`not_verified` and the report says so. Defaults:

```yaml
verify:
  enabled: null            # null: the profile decides (on for assisted)
  provider: anthropic
  model: claude-opus-5-5
  effort: medium
  min_severity: medium
  max_findings: 150        # model calls per scan
  max_tokens: 400000       # input + output tokens per scan
  max_output_tokens: 4000  # per call
  excerpt_lines: 120
  cache: true              # .arbiter/verify-cache.json; cache_path overrides
  skip_rules: ["arbiter/ast.*", "arbiter/quality.*", "arbiter/assurance.suppression-census"]
  # fp_notes_path: FILE    # known false-positive notes; default: the shipped ledger
```

| Profile | Verification runs |
|---|---|
| `offline`, `ci` | never, whatever `verify.enabled` or `--model-verify` say |
| `assisted` | yes, unless `verify.enabled: false` or `--no-model-verify` |
| `connected`, `audit` | only with `verify.enabled: true` or `--model-verify` |

Do not confuse this with `arbiter verify REPORT`, which checks a report's
claim ledger and calls no model ([cli.md](cli.md#integrity-and-compliance)).

## Gating

```yaml
gate:
  fail_on:
    severity: critical
    new: high
    coverage_below: 0.60
  gate_on_inferred: false
  fail_on_probe_error: false
  tier_mode: legacy
  tiers: [verified]
```

| Key | Effect |
|---|---|
| Key | Default | Effect |
|---|---|---|
| `fail_on.severity` | `critical` | any finding at or above this severity fails |
| `fail_on.new` | none | findings at or above this severity that are absent from the baseline (`--baseline`) fail |
| `fail_on.coverage_below` | none | the run fails if rubric coverage falls below this fraction |
| `gate_on_inferred` | `false` | when false, findings with `provenance: inferred` (from a model) never fail a build |
| `fail_on_probe_error` | `false` | when true, a probe that errored fails the build |
| `tier_mode` | `legacy` | `legacy`: `fail_on` reads every active finding. `tiered`: only findings whose precision tier is in `tiers` ([below](#precision-tiers)). Any other value is an error |
| `tiers` | `[verified]` | under `tier_mode: tiered`, the tiers that can fail the gate: any of `verified`, `advisory`, `inferred` |

Suppressed findings never fail the gate. The gate reads severity, never
confidence, so calibration ([calibration.md](calibration.md)) cannot change a
gate outcome. (Older docs mention a `use_calibration` key; nothing reads it.)

`coverage_below` is the guard against a reassuring result from a scan that
barely ran. Without it, uninstalling an analyzer looks identical to fixing every
finding it produced.

## Precision tiers

Every finding in every report carries a precision tier, `verified`,
`advisory` or `inferred`, and a one-line `tier_reason`. The tier says whether
the finding is precise enough to block a `tiered` gate; it never hides or
re-grades a finding. The rules for assigning tiers are in
[precision.md](precision.md#tiers).

```yaml
precision:
  ledger: null            # null = the ledger shipped with Arbiter (src/arbiter/packs/precision.json)
  verified_rules: []      # rule ids or globs declared verified; the ledger must still admit them
  deterministic_profiles: [offline, ci]   # profiles that cannot ask a model
```

Today no rule is declared verified, so every deterministic finding is
`advisory` and a `tier_mode: tiered` gate blocks nothing. That is why
`legacy` is the default. Switch a repository to `tiered` only once rules it
cares about are declared verified and admitted by the ledger
([precision.md](precision.md#current-numbers) has the current state).

## Scoring

```yaml
score:
  coverage_threshold: 0.6     # below this rubric coverage the overall grade is withheld
  weights:                    # per dimension; the defaults are below
    security: 0.30
    quality: 0.20
    supply_chain: 0.15
    interface: 0.15
    compliance: 0.10
    drift: 0.10
    assurance: 0.0            # deliberately zero: it measures the checking, not the code
```

`coverage_threshold` decides when the grade is withheld; `gate.fail_on.coverage_below`
decides when the gate fails. They are separate keys.

## Choosing probes

```yaml
probes:
  disable: [judgement]                 # same as --skip on every run
  severity_overrides:
    "arbiter/quality.todo-density": info   # rule id or glob -> severity
  adapters_parallel: 2                 # see External analyzers
```

`--only` and `--skip` on the command line choose probes for one run; `arbiter
probes .` lists the probe names. A disabled probe is reported as skipped, so
coverage shows what it cost.

## Suppressions

```yaml
suppress:
  - rule: "arbiter/*"
    path: "fixtures/**"
    reason: "planted defects — this is the golden-fixture test corpus"
    expires: 2099-01-01
```

Three properties, each deliberate:

- **`reason` and `expires` are mandatory.** An expired suppression stops
  suppressing rather than failing the run, so the finding returns and is
  re-decided instead of quietly persisting forever.
- **Suppressed findings stay in the report**, marked and attributed. They are
  not deleted; a reader can always see what was set aside and on whose say-so.
- **Patterns match rule and path**, including external rules
  (`checkov/*`), so a third-party analyzer can be quieted in one place.

## House rules

Five rule types cover repository-specific policy without writing code:

| Type | Asserts |
|---|---|
| `file_exists` | a required file is present |
| `file_absent` | a forbidden file is not present |
| `content_match` | a pattern is present, or forbidden, in matching files |
| `reference_integrity` | every reference resolves to a declared anchor |
| `metric_threshold` | a measured metric stays within a limit |

Plus `ast_query`, which runs a tree-sitter query and reports the captures —
structural, so it catches the construct however it is formatted:

```yaml
rules:
  - id: no-bare-except
    type: ast_query
    languages: [python]
    files: "src/**/*.py"
    # The predicate is the rule: `(except_clause) @hit` alone matches every handler.
    query: '(except_clause (identifier) @t (#match? @t "^(Exception|BaseException)$")) @hit'
    capture: hit
    title: "Broad except clause"
    remediation: "Catch the specific exception, or re-raise after handling."
    severity: low
```

`ast_query` requires the `ast` extra (tree-sitter). Without it the rule reports
as not assessed, naming the missing dependency.

## Adaptive thresholds

Thresholds can adapt to the repository rather than to history, which needs no
knowledge file and preserves reproducibility:

```yaml
quality:
  adaptive: true
  max_file_lines: 900       # the floor, still enforced
  max_function_lines: 140
```

File length, function length and complexity limits are drawn from that
codebase's own p95, with a floor so a uniformly bad codebase cannot normalize
its way to a clean report:

```text
file_lines      n=30   median=143  p95=795
function_lines  n=210  median=12   p95=75     # the fixed 120 limit was too lax here
complexity      n=210  median=5.5  p95=27
```

A distribution with fewer than 30 samples is ignored and the fixed threshold
stands.

## External analyzers

The adapters (ruff, bandit, semgrep, checkov, gitleaks) run after the native
probes, concurrently, in a thread pool of `probes.adapters_parallel` workers
(default: the smaller of 4 and the CPU count; `1` runs them one after
another). Their outcomes and findings are written into the report in
registry order whatever order they finished in, so the report's bytes do not
depend on the scheduler.

```yaml
probes:
  adapters_parallel: 2
```

With the result cache on, each adapter is also memoised whole in
`.arbiter/cache.json`: one entry per adapter, keyed on its name, the tool's
version, the configuration, Arbiter's own source, a digest of every inventory
file and of the analyzer configuration at the repository root
(`pyproject.toml`, `ruff.toml`, `.ruff.toml`, `setup.cfg`, `.bandit`,
`bandit.yaml`, `.semgrep.yml`, `.semgrep.yaml`, `.semgrep/`, `.checkov.yaml`,
`.checkov.yml`, `.gitleaks.toml`). A hit replays the stored findings and the
probe's outcome says `replayed from cache (nothing the tool reads changed)`.
It is all-or-nothing by design: none of these tools has been measured to be
file-local, so one changed byte in one file re-runs the whole adapter rather
than guessing which of its findings survive. `--verify-cache` re-runs one
memoised adapter per scan and reports a divergence as
`arbiter/assurance.cache-divergence`; `--no-cache` disables the memo along
with the rest of the cache.

## Other keys

| Key | Effect |
|---|---|
| `quality.max_file_lines`, `quality.max_function_lines`, `quality.max_complexity` | fixed thresholds (defaults 800, 120, 20); see [Adaptive thresholds](#adaptive-thresholds) |
| `cache.enabled` | `false` turns the result cache off, like `--no-cache` ([cli.md](cli.md#the-result-cache)) |
| `controls.frameworks`, `controls.packs` | limit the control-coverage summary to these framework ids; extra pack directories ([compliance.md](compliance.md)) |
| `terraform.plans` | Terraform plan JSON files to read, as `--tfplan` ([probes.md](probes.md#terraform-supply-a-plan)) |
| `judgement.provider`, `judgement.model`, `judgement.timeout`, `judgement.api_key_env` | the judgement pass ([probes.md](probes.md#the-judgement-pass)) |
| `out` | tells the doc-drift probe where generated output lives ([below](#where-state-lives)) |

## Where state lives

| Path | Contents | Commit it? |
|---|---|---|
| `arbiter.yaml` | configuration | yes |
| `.arbiter/baseline.json` | accepted current state | yes |
| `.arbiter/knowledge.json` | calibration ledgers and learned confidence | yes |
| `.arbiter/external-severity.json` | measured severities for external checks | yes |
| `.arbiter/cache.json` | per-file results of the file-local probes, and one whole-tree entry per external analyzer | no — git-ignored |
| `.arbiter/verify-cache.json` | model verification verdicts, keyed on the excerpt, model and prompt version | optional — not git-ignored; committing it lets another machine reproduce the verdicts without model calls |
| `~/.cache/arbiter/tool-versions.json` (`ARBITER_CACHE_DIR`) | the external analyzers' version strings, keyed on each binary's path, mtime and size, so registration does not run every `--version` on every invocation; `--no-cache` leaves it alone, `ARBITER_NO_VERSION_MEMO=1` bypasses it, and a tool replaced in place with the same size and mtime keeps its old string until the file is deleted | no — machine-local |
| `arbiter-out/` | reports from the last run | no — git-ignored |
| `arbiter-ab/` | A/B harness output | no — git-ignored |

Where a run writes is decided by `--out` on the command line. An optional
`out:` key in `arbiter.yaml` tells the doc-drift probe which directory holds
generated output, so a document that names `REPORT.md` under it is not
reported as describing a missing file; it does not move the output itself. The
probe treats any `arbiter-out` segment and the run's own `--out` the same way —
see [What doc_drift leaves alone](probes.md#what-doc_drift-leaves-alone).
