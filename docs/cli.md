# Command Reference

Every command reads and writes files; none executes the target repository.
The command is `arbiter`, the console script `pip install -e .` puts on your
`PATH` (there is no `python -m arbiter`). `arbiter <command> --help` prints
every flag of a command; this page explains them and shows what to type.

## Contents

- [Commands at a glance](#commands-at-a-glance)
- [Analysis](#analysis)
- [Baselines and comparison](#baselines-and-comparison)
- [Adjudication and calibration](#adjudication-and-calibration)
- [Integrity and compliance](#integrity-and-compliance)
- [The air-gapped bundle](#the-air-gapped-bundle)
- [Hosted and MCP surfaces](#hosted-and-mcp-surfaces)
- [Output formats](#output-formats)
- [Run history](#run-history)
- [Exit codes](#exit-codes)

---

## Commands at a glance

| Command | What it does |
|---|---|
| `scan` | analyze and report |
| `gate` | analyze, report, and exit 1 on policy failure |
| `probes` | list probes and whether each can run here |
| `explain` | show one finding in full |
| `baseline` | write a baseline from a report |
| `diff` | compare two reports: new, fixed, unchanged |
| `ab` | run two arms over the same target and compare ([ab-testing.md](ab-testing.md)) |
| `review` | adjudicate a batch of findings in one pass |
| `feedback` | adjudicate one or more findings by id |
| `learn` | show what calibration has learned |
| `verify` | check a report's claims against their basis (no model involved) |
| `controls` | control coverage per framework, including what was not assessed |
| `dashboard` | render the run history as one self-contained page |
| `bundle` | build and verify an air-gapped install bundle |
| `api` | serve the hosted API and issue its keys ([hosted-api.md](hosted-api.md)) |
| `mcp` | serve the MCP tools over stdio or HTTPS ([mcp.md](mcp.md)) |
| `remote` | run `scan`, `gate`, `review-queue` or `health` against a hosted instance ([hosted-api.md](hosted-api.md)) |

## Analysis

```bash
arbiter scan ./repo                             # analyze and report
arbiter scan --system arbiter-system.yaml       # several repos, one verdict
arbiter scan . --tfplan tfplan.json             # read a Terraform plan
arbiter scan . --profile ci                     # override the configured profile
arbiter scan . --only secrets,supply_chain      # run only these probes
arbiter scan . --skip judgement                 # run everything but this
arbiter scan . --no-adapters                    # native probes only, no external analyzers
arbiter scan . --pin-knowledge k:4cf3d3fa2f02   # freeze calibration for a release gate
arbiter gate ./repo --baseline .arbiter/baseline.json   # exit 1 on policy failure
arbiter scan . --no-history                     # leave history.jsonl alone this run
arbiter probes ./repo                           # what can run here, and why not
arbiter explain f:8c41d2ae9b07                  # one finding in full, from arbiter-out/report.json
```

`scan` reports. `gate` reports and enforces — it is the form to use in CI, where
the exit code is the product. See [configuration.md](configuration.md#gating)
for the thresholds it reads.

Flags shared by `scan` and `gate`:

| Flag | Effect |
|---|---|
| `--config FILE` | `arbiter.yaml` to use (default: the one in the first target) |
| `--profile NAME` | `offline`, `ci`, `assisted`, `connected` or `audit` ([configuration.md](configuration.md#profiles)) |
| `--system FILE` | scan the repositories an `arbiter-system.yaml` lists ([systems.md](systems.md)) |
| `--only`, `--skip` | comma-separated probe names (`arbiter probes` lists them) |
| `--no-adapters` | native probes only |
| `--baseline FILE` | label each finding `new` or `existing` against a baseline |
| `--knowledge FILE`, `--pin-knowledge HASH` | calibration file (default `.arbiter/knowledge.json`), and fail unless it is exactly this version |
| `--changed REF`, `--only-files PATHS` | partial scan: only these files; checks that need the whole tree are recorded as not assessed ([ci.md](ci.md#scanning-only-what-changed)) |
| `--tfplan [REPO=]PATH` | Terraform plan or state JSON; repeatable |
| `--model-verify`, `--no-model-verify` | turn the model verification pass on or off for this run; it never runs under `offline` or `ci` ([configuration.md](configuration.md#model-verification)) |
| `--no-cache`, `--cache-path FILE` | bypass or relocate the result cache ([below](#the-result-cache)) |
| `--github`, `--no-github` | GitHub Actions mode ([below](#output-formats)); on by default when `GITHUB_ACTIONS=true` |
| `--sarif-min-severity SEV` | drop findings below `SEV` from `report.sarif` only |
| `--no-history` | do not append to `<out>/history.jsonl` |
| `--out DIR`, `--format LIST` | output directory (default `arbiter-out`) and formats (default `json,console`) |

`scan` also takes `--limit N` (findings shown on the console, default 40),
`--open` / `--no-open` (open the HTML report when done; on by default at a
terminal) and `--verify-cache`.

`explain` and `feedback` read `arbiter-out/report.json` unless `--report`
names another. `explain` needs the full id; `feedback` also accepts the end of
one (`8c41d2ae9b07`).

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
cached probe's outcome says how many files came from the cache. External
analyzers are memoised whole; see
[configuration.md](configuration.md#external-analyzers).

`--verify-cache` re-runs each cacheable probe on a random sample of its hits
(5%, at least one file). A cached entry that no longer matches becomes a
finding, `arbiter/assurance.cache-divergence` (severity high), naming the file
and the probe, and the entry is replaced. `--no-cache` neither reads nor
writes the file, as does `cache: {enabled: false}` in `arbiter.yaml`. The cache
file is excluded from the inventory and is git-ignored; CI never uses it.

The model verdict cache (`.arbiter/verify-cache.json`) is separate and does
not follow `--no-cache`; see [precision.md](precision.md#determinism-and-cost).

## Baselines and comparison

```bash
arbiter baseline arbiter-out/report.json        # snapshot current state into .arbiter/baseline.json
arbiter baseline arbiter-out/report.json --migrate .arbiter/baseline.json \
    --out .arbiter/baseline.json                # carry a pre-ARB-080 baseline into occurrence ids
arbiter diff before.json after.json             # new / fixed / unchanged
arbiter diff before.json after.json --format markdown,pr-comment --out diff-out
arbiter ab --spec examples/ab-native-vs-checkov.yaml --out ab-out
```

`diff` accepts `--format console,json,markdown,pr-comment` (default
`console`); `--out DIR` writes the chosen formats to files.

A baseline is what makes "fail on new findings only" usable: findings are
matched by fingerprint, which deliberately excludes the line number, so
reformatting a file does not invalidate it. Repeats of the same evidence in
one file each get their own ID (an occurrence ordinal); a baseline cut before
that keeps matching every repeat it collapsed, and `--migrate` rewrites it in
the new format without accepting anything new. See
[architecture.md](architecture.md#fingerprinting).

The A/B harness is documented in [ab-testing.md](ab-testing.md).

## Adjudication and calibration

```bash
arbiter review arbiter-out/report.json          # write arbiter-out/review.md: 20 findings to mark
arbiter review report.json --html               # ...as a page you can tap through
arbiter review report.json --interactive        # ...or one keypress each, in the terminal
arbiter review --apply arbiter-out/review.md    # record the verdicts
arbiter feedback f:8c41d2ae9b07 --false-positive --note "test fixture"   # one finding
arbiter learn                                   # what has been learned, and what it supports
```

Verdicts are attributed to `--reviewer` (default: `git config user.email`).
`feedback` refuses to record without a terminal unless `--batch` is given, and
then marks the verdicts as a batch import; `review --interactive` needs a
terminal. Why this ledger is the only input to calibration, and
how the batch is chosen, is covered in [calibration.md](calibration.md).

These verdicts calibrate *confidence* in your repository. They are not the
labels behind the precision ledger, which are built separately
([precision.md](precision.md#ledger)).

## Integrity and compliance

```bash
arbiter verify arbiter-out/report.json          # does any claim outrun its basis?
arbiter verify arbiter-out/report.json --show-claims
arbiter controls --list                         # the frameworks available
arbiter controls arbiter-out/report.json        # control coverage, including the gaps
arbiter controls arbiter-out/report.json --framework FedRAMP-Moderate-r5
arbiter controls arbiter-out/report.json --framework CWE-Top25-2024 --state not_assessed
```

`arbiter verify` checks the report's claim ledger against the twelve
invariants; it calls no model and is unrelated to `--model-verify`. See
[evidence.md](evidence.md#claim-integrity) and [compliance.md](compliance.md).

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

## Hosted and MCP surfaces

```bash
arbiter api key add --user alice@example.com    # mint a key (default expiry 90 days)
arbiter api key list
arbiter api key revoke <short-id>
arbiter api serve --cert cert.pem --key key.pem # TLS only; localhost unless --host says otherwise
arbiter remote health --server https://arbiter.example.com
arbiter remote scan ./repo                      # needs ARBITER_SERVER and ARBITER_API_KEY, or --server/--key
arbiter remote gate ./repo
arbiter remote review-queue arbiter-out/report.json
arbiter mcp                                     # stdio, for one local agent
arbiter mcp --http --root /srv/work --cert cert.pem --key key.pem
```

These are documented in [hosted-api.md](hosted-api.md) and [mcp.md](mcp.md).

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
| `json` | `report.json`, the canonical record; input to `baseline`, `diff`, `verify`, `controls`, `review`, `explain` |
| `sarif` | code scanning: findings under the Security tab and inline on a pull request |
| `html` | a self-contained page, no server and no network |
| `markdown` | PR comments and reports for people |
| `pr-comment` | the compact verdict `diff` posts, rendered from one report |
| `annotations` | GitHub workflow commands, one line per finding, printed to stdout after the console output and kept as `annotations.txt` |
| `console` | terminal output, the default alongside `json` |

What a report contains, field by field, is in
[reading-a-report.md](reading-a-report.md).

`annotations` marks the findings named in `gate.failing_ids` as `::error`, findings
tagged `outside-this-change` as `::notice`, and every other active finding as
`::warning`. Each line carries the file, the line, the rule id and the title, and
nothing else: never the evidence, so a build log cannot reprint a credential.
Suppressed findings are not printed.

`--github` / `--no-github` on `scan` and `gate` turns GitHub Actions mode on or
off. The default is automatic: on when `GITHUB_ACTIONS=true`. When on, `annotations`
is added to the formats and the pull-request comment is appended to the job summary
(`$GITHUB_STEP_SUMMARY`) when that variable is set.

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

## Exit codes

| Code | Meaning |
|---|---|
| `0` | pass |
| `1` | gate failure (`gate`, `remote gate`), a claim that outruns its basis (`verify`), or an altered bundle (`bundle verify`) |
| `2` | error: bad arguments, an unreadable config, a finding id not in the report |

A gate failure is a policy decision and is reported as such. An error means
Arbiter could not complete the run — it never degrades into a pass.
