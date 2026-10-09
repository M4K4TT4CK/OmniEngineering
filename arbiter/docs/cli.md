# Command Reference

Every command reads and writes files; none executes the target repository.

## Contents

- [Analysis](#analysis)
- [Baselines and comparison](#baselines-and-comparison)
- [Adjudication and calibration](#adjudication-and-calibration)
- [Integrity and compliance](#integrity-and-compliance)
- [The air-gapped bundle](#the-air-gapped-bundle)
- [Output formats](#output-formats)
- [Run history](#run-history)
- [Exit codes](#exit-codes)

---

## Analysis

```bash
arbiter scan ./repo                             # analyze and report
arbiter scan --system arbiter-system.yaml       # several repos, one verdict
arbiter scan . --tfplan tfplan.json             # read a Terraform plan
arbiter scan . --profile connected              # override the configured profile
arbiter scan . --pin-knowledge k:4cf3d3fa2f02   # freeze calibration for a release gate
arbiter gate ./repo --baseline .arbiter/baseline.json   # exit 1 on policy failure
arbiter scan . --no-history                     # leave history.jsonl alone this run
arbiter probes ./repo                           # what can run here, and why not
arbiter explain f:8c41d2ae9b07                  # one finding in full
```

`scan` reports. `gate` reports and enforces — it is the form to use in CI, where
the exit code is the product. See [configuration.md](configuration.md#gating)
for the thresholds it reads.

### The result cache

```bash
arbiter scan .                                  # reads and writes .arbiter/cache.json
arbiter scan . --no-cache                       # every probe over every file; nothing written
arbiter scan . --verify-cache                   # re-run a 5% sample of cache hits and compare
arbiter gate . --no-cache                       # what CI runs
arbiter scan . --cache-path /tmp/arbiter-cache.json
```

The file-local probes (`secrets`, `supply_chain`, `ast_metrics`,
`house_rules_ast`, `authored`) keep their per-file findings in
`.arbiter/cache.json` under the first target, keyed on the file's bytes, the
configuration, the probe and Arbiter versions and a digest of Arbiter's own
source. An unchanged file under unchanged rules is served from the cache; an
edited file, a changed `arbiter.yaml` or an edited probe is re-read. Every
other probe runs in full every time, and calibration, severity overrides, the
baseline and suppressions are applied to cached and fresh findings alike. The
report records `scan_scope.cache` (`hits`, `misses`, `verified`) and each
cached probe's outcome says how many files came from the cache.

`--verify-cache` re-runs each cacheable probe on a random sample of its hits
(5%, at least one file). A cached entry that no longer matches becomes a
finding, `arbiter/assurance.cache-divergence` (severity high), naming the file
and the probe, and the entry is replaced. `--no-cache` neither reads nor
writes the file, as does `cache: {enabled: false}` in `arbiter.yaml`. The cache
file is excluded from the inventory and is git-ignored; CI never uses it.

## Baselines and comparison

```bash
arbiter baseline arbiter-out/report.json        # snapshot current state
arbiter diff before.json after.json             # new / fixed / unchanged
arbiter ab --spec examples/ab-native-vs-checkov.yaml --out ab-out
```

`diff` accepts `--format console,json,markdown,pr-comment`.

A baseline is what makes "fail on new findings only" usable: findings are
matched by fingerprint, which deliberately excludes the line number, so
reformatting a file does not invalidate it. See
[architecture.md](architecture.md#fingerprinting).

The A/B harness is documented in [ab-testing.md](ab-testing.md).

## Adjudication and calibration

```bash
arbiter review arbiter-out/report.json          # adjudicate a batch in one pass
arbiter review report.json --html               # ...as a page you can tap through
arbiter review report.json --interactive        # ...or one keypress each, in the terminal
arbiter review --apply arbiter-out/review.md    # record the verdicts
arbiter feedback f:8c41 --false-positive        # adjudicate a single finding
arbiter learn                                   # what has been learned, and what it supports
```

Why this ledger is the only input to calibration, and how the batch is chosen,
is covered in [calibration.md](calibration.md).

## Integrity and compliance

```bash
arbiter verify arbiter-out/report.json          # does any claim outrun its basis?
arbiter verify arbiter-out/report.json --show-claims
arbiter controls arbiter-out/report.json        # control coverage, including the gaps
arbiter controls arbiter-out/report.json --framework FedRAMP-Moderate-r5
```

See [evidence.md](evidence.md#claim-integrity) and
[compliance.md](compliance.md).

## The air-gapped bundle

```bash
arbiter bundle build --out ./arbiter-bundle                  # wheels, scripts, manifest
arbiter bundle build --out ./arbiter-bundle --no-deps-download   # skip the one step that needs an index
arbiter bundle build --out ./arbiter-bundle --source /path/to/checkout
arbiter bundle verify ./arbiter-bundle                       # exit 1 on a missing, altered or extra file
```

`build` writes Arbiter and its runtime dependencies as wheels, the checkout's
knowledge file when it has one, `install.sh` / `install.ps1` and a
`MANIFEST.json` with a SHA-256 per file. The five analyzers are not included,
and the manifest says so: their redistribution review (ARB-005) is pending.
`verify` recomputes every hash and refuses anything the manifest does not name.
See [bundle.md](bundle.md).

The hosted surfaces (`arbiter api serve`, `arbiter api key`, `arbiter mcp`,
`arbiter remote`) are documented in [hosted-api.md](hosted-api.md) and
[mcp.md](mcp.md).

---

## Output formats

```bash
arbiter scan . --format json,sarif,html,markdown,console
arbiter gate . --format json,sarif,annotations --github   # inside GitHub Actions
```

JSON is canonical and is written first; every other format is a rendering of it.
Anything a renderer shows can be recomputed from the JSON, and no format carries
a fact the JSON does not.

| Format | Use |
|---|---|
| `json` | canonical record; input to `baseline`, `diff`, `verify`, `controls`, `review` |
| `sarif` | code scanning: findings under the Security tab and inline on a pull request |
| `html` | a self-contained page, no server and no network |
| `markdown` | PR comments and reports for people |
| `pr-comment` | the compact verdict `diff` posts, rendered from one report |
| `annotations` | GitHub workflow commands, one line per finding, printed to stdout after the console output and kept as `annotations.txt` |
| `console` | terminal output, the default alongside `json` |

## Run history

```bash
arbiter dashboard                               # arbiter-out/history.jsonl -> arbiter-out/dashboard.html
arbiter dashboard --history ci/history.jsonl --out ci/trend.html
```

Every `scan` and `gate` appends one line to `<out>/history.jsonl`: time,
commit, system, profile, scan mode, grade, score, coverage, counts by severity,
new findings at high or above, the gate result, duration and the finding
total. No evidence, no paths, no titles — the line is safe to keep where the
report is not. `--no-history` on either command skips the append.

`dashboard` renders that file as a self-contained page: the latest grade,
coverage, new-high count and gate as tiles, score and coverage over time,
findings stacked by severity per run, and the runs newest first. It exits 0
on an empty or missing history and says so on the page; a corrupt line is
skipped, not fatal.


`annotations` marks the findings named in `gate.failing_ids` as `::error`, findings
tagged `outside-this-change` as `::notice`, and every other active finding as
`::warning`. Each line carries the file, the line, the rule id and the title, and
nothing else: never the evidence, so a build log cannot reprint a credential.
Suppressed findings are not printed.

`--github` / `--no-github` on `scan` and `gate` turns GitHub Actions mode on or
off. The default is automatic: on when `GITHUB_ACTIONS=true`. When on, `annotations`
is added to the formats and the pull-request comment is appended to the job summary
(`$GITHUB_STEP_SUMMARY`) when that variable is set.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | pass |
| `1` | gate failure |
| `2` | error |

A gate failure is a policy decision and is reported as such. An error means
Arbiter could not complete the run — it never degrades into a pass.
