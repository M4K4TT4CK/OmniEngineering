# Configuration

`arbiter.yaml` at the repository root is the whole configuration surface. There
is no second config file, no environment-variable override matrix, and no
implicit user-level defaults.

## Contents

- [The file](#the-file)
- [Profiles](#profiles)
- [Gating](#gating)
- [Suppressions](#suppressions)
- [House rules](#house-rules)
- [Adaptive thresholds](#adaptive-thresholds)
- [External analyzers](#external-analyzers)
- [Where state lives](#where-state-lives)

---

## The file

```yaml
version: 1
profile: offline          # offline | ci | connected | audit

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
| `offline` | no | no | air-gapped and high-side environments |
| `ci` | no | no | fast, deterministic gating |
| `connected` | yes | yes | full depth |
| `audit` | yes | yes | everything, no time budget |

A probe that needs a capability the profile forbids is skipped with that reason
recorded, so the report says what the profile cost you. Coverage falls by
exactly the amount that was not checked — the grade never quietly absorbs it.

Override per run with `arbiter scan . --profile connected`.

## Gating

```yaml
gate:
  fail_on:
    severity: critical
    new: high
    coverage_below: 0.60
  gate_on_inferred: false
  use_calibration: false
```

| Key | Effect |
|---|---|
| `severity` | any finding at or above this severity fails |
| `new` | findings at or above this severity that are absent from the baseline fail |
| `coverage_below` | the run fails if rubric coverage falls below this fraction |
| `gate_on_inferred` | when false (default), findings with `provenance: inferred` never fail a build |
| `use_calibration` | when false (default), learned confidence never changes a gate outcome |

`coverage_below` is the guard against a reassuring result from a scan that
barely ran. Without it, uninstalling an analyzer looks identical to fixing every
finding it produced.

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
    query: "(except_clause) @hit"
    title: "Bare or broad except clause"
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

## Where state lives

| Path | Contents | Commit it? |
|---|---|---|
| `arbiter.yaml` | configuration | yes |
| `.arbiter/baseline.json` | accepted current state | yes |
| `.arbiter/knowledge.json` | calibration ledgers and learned confidence | yes |
| `.arbiter/external-severity.json` | measured severities for external checks | yes |
| `.arbiter/cache.json` | per-file results of the file-local probes, and one whole-tree entry per external analyzer | no — git-ignored |
| `~/.cache/arbiter/tool-versions.json` (`ARBITER_CACHE_DIR`) | the external analyzers' version strings, keyed on each binary's path, mtime and size, so registration does not run every `--version` on every invocation; `--no-cache` leaves it alone, `ARBITER_NO_VERSION_MEMO=1` bypasses it, and a tool replaced in place with the same size and mtime keeps its old string until the file is deleted | no — machine-local |
| `arbiter-out/` | reports from the last run | no — git-ignored |
| `arbiter-ab/` | A/B harness output | no — git-ignored |

Where a run writes is decided by `--out` on the command line. An optional
`out:` key in `arbiter.yaml` tells the doc-drift probe which directory holds
generated output, so a document that names `REPORT.md` under it is not
reported as describing a missing file; it does not move the output itself. The
probe treats any `arbiter-out` segment and the run's own `--out` the same way —
see [What doc_drift leaves alone](probes.md#what-doc_drift-leaves-alone).
