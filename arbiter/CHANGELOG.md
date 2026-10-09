# Changelog

All notable changes to Arbiter are recorded here. Entries are grouped by date
under `[Unreleased]` (there are no release tags yet) and reference the
`REQ-###` they serve. Rationale belongs in `.ai/project-context.md`.

## [Unreleased]

### 2026-10-09

- FAIL-050 (ARB-048) | The secrets probe no longer reports an environment-variable
  NAME assigned to a credential-named symbol (`SEMANTIC_API_KEY_ENV =
  "OMNI_GRAPH_SEMANTIC_API_KEY"`): a value shaped like an upper-case identifier,
  or a symbol whose suffix says it holds a name (`_ENV`, `_VAR`, `_NAME`), is not
  a credential; a live-looking key beside it still fires. Found by code scanning
  on upstream OmniEngineering pull request #3.
- FAIL-046 (ARB-049) | Every action in `pr-check.yml` and `train.yml` is pinned to
  its tag's commit SHA, the tag kept in a trailing comment, so the workflows run
  what was reviewed rather than what the tag points at on the day.
- ARB-052 | A finding in a `--changed` gate is tagged `req:<ID>` for the requirements
  cited by the commits since the base that touched its own file, marked
  `req-scope:commits`. Before, every finding in the change carried every id the
  round's commits cited, so a five-requirement gate could not say which one
  introduced a finding and OmniEngineering's graph attributed it to all of them.
  A file no citing commit touched falls back to the round's ids marked
  `req-scope:open` (context, not attribution); a full scan carries only that
  marker; old ids resolve through the registry's `id_aliases`; one
  `git log --name-only` runs per repository however many files changed; and the
  By requirement table says under it which scope each count came from. The
  attribution lives in `incremental.attribute_requirements`, called by the
  engine after every probe has run, since a tag on every finding cannot be
  applied by one probe.
- ARB-045 | Tool versions are memoised. Every `arbiter` invocation ran the five
  analyzers' `--version` at registration (25 s measured here; semgrep alone
  reaches its 20 s cutoff), including the partial-scan gate that never runs an
  adapter. `Adapter.tool_version` now reads `~/.cache/arbiter/tool-versions.json`
  (`ARBITER_CACHE_DIR`), keyed on the resolved executable's path, mtime and
  size (and a script argument's, for a tool run through an interpreter); a miss
  probes once and writes the memo atomically, a missing binary is never probed,
  a corrupt or unwritable memo costs one probe. `--no-cache` leaves it alone
  (it is not a result cache); `ARBITER_NO_VERSION_MEMO=1` switches it off. A
  tool replaced in place with the same size and mtime keeps its old string
  until the file is deleted. Registration: 24.5 s cold, 0.3 s warm. The
  mutation tool proves a memo that ignores the binary's stamp is caught.
- ARB-046 | The suite runs in a fast and a slow tier and CI shards them.
  `tests/test_arbiter.py` (5,516 lines, 438 collected ids) is fourteen files by
  area, each under `arbiter.yaml`'s 900-line limit, with the helpers more than
  one file needs in `tests/helpers.py` and the tfplan fixtures in `conftest.py`
  at session scope; `pytest --collect-only` lists the same 557 ids before and
  after, and the failure ledger's regression-test references follow the tests.
  A registered `slow` marker covers the eight tests that run a real analyzer
  through the adapters or scan beyond the fixtures: `-m "not slow"` runs in
  about two minutes here and `-m slow` in about five, against eleven for the
  whole suite before. `pr-check.yml` runs the fast tier on every matrix cell
  and a parallel Linux `slow-tier` job runs the slow tier with the analyzers
  installed; `.ai/test-suites.json` names the fast tier as the gate's suite
  and the slow tier as an integration suite the gate does not run.
- FAIL-044 (ARB-047) | `run_scan(use_adapters=False)` keeps every external analyzer
  out of the run. The flag used to guard only registration, and the registry is
  module-global, so once one scan had registered the analyzers every later scan
  ran all five whatever the flag said; the fast test tier spent 28 minutes in
  semgrep. The scan loop now skips a probe marked external when the flag is off
  and no explicit `only=` names it. Fast tier: 1 minute 53 seconds.
- ARB-047 | Adapters run concurrently and replay when nothing they read
  changed. Probes registered from a manifest carry `Probe.external` and run
  after the native probes in a thread pool of `probes.adapters_parallel`
  workers (default min(4, cpu count); 1 runs them in turn), each on its own
  copy of the probe context, with outcomes and findings recorded in registry
  order so reports stay deterministic. With the result cache on, one entry per
  adapter, keyed on its name, the tool version, the rules hash and a digest
  over every inventory file plus the analyzer configuration at the root
  (`pyproject.toml`, `ruff.toml`, `.ruff.toml`, `setup.cfg`, `.bandit`,
  `bandit.yaml`, `.semgrep.yml`, `.semgrep.yaml`, `.semgrep/`, `.checkov.yaml`,
  `.checkov.yml`, `.gitleaks.toml`), replays the findings with the outcome
  reason `replayed from cache (nothing the tool reads changed)`. All-or-nothing
  by design: no adapter has been measured to be file-local, so one changed
  byte re-runs the whole tool. `--verify-cache` re-runs one memoised adapter a
  scan and reports a divergence as `arbiter/assurance.cache-divergence`;
  `--no-cache` disables the memo (CI's self-gate). Integrity invariant CI-14
  proves a changed inventory file misses the memo, and the mutation tool
  proves a memo keyed without the tree is caught.
- ARB-048 | The doc-drift probe leaves generated output, git-ignored paths and
  cross-repository references alone. Scanning Arbiter with itself, `doc_drift`
  reported every path the root `.gitignore` keeps out of a checkout
  (`.claude/settings.local.json`, `.ai/project-graph.json`, `.arbiter/cache.json`),
  a report file under an `--out` directory, and the OmniEngineering handbook
  named beside its URL. The backticked-path and markdown-link checks now skip a
  candidate the root `.gitignore` matches (comments, `dir/`, `*.ext`, `path/**`,
  a leading `/` and `!` re-inclusion are read, nothing broader), one under the
  output directory (the new optional `out:` key, this run's `--out` when it sits
  inside the tree, or any `arbiter-out` segment) and a link target carrying a
  scheme; a backticked path is also skipped when an `http(s)://` URL sits on its
  line or the adjacent line of the paragraph, since hard wrapping split the
  motivating case. A relative link is never excused that way and a plainly
  missing file still fires. The engine hands `--out` to `ProbeContext.out_dir`
  rather than the config, so cache keys do not change with the output directory.
  `fixtures/doc-drift` plants the four cases and one real one; the self-scan
  drops from 30 doc-drift findings to 20.
- FAIL-042 (ARB-048) | The stub detector counts a marker only at the function body's own
  indentation: a `pass` under `except`, `if` or `with` is a branch, not a body. Found by
  code scanning on OmniEngineering pull request #5, where `store_mcp_probe_memo`, which
  writes a file and swallows a failed write, was reported as a stub.
- ARB-049 | Arbiter's own debt is worked through the review flow and the baseline is
  re-cut. The self-scan went from 482 active findings (57 medium, 324 low, 101
  info; score 71) to 320 (39 medium, 182 low, 99 info; score 81) and the gate's
  baseline from 411 ids at 19b40f7 to 320 at 2a18ce9, one commit per finding
  class with the fast tier, `tools/integrity.py` and `tools/mutate_tests.py` green
  after each. Twenty of thirty-seven broad `except Exception` clauses name the
  exception and the seventeen that must never raise say why on the line; ruff's
  unused imports, dead locals, placeholder-free f-strings and unsorted import
  blocks are gone, and the dead local in `render_html` was the HTML report's
  "Read first" block, built and never rendered since it was added (FAIL-045);
  every suppression comment carries a reason or is removed; `build_parser` and
  `render_html` are split into named helpers with the same inputs and outputs.
  The three tests ARB-046 marked slow only because `use_adapters=False` was a
  no-op are back in the fast tier (0.9 s, 0.2 s, 0.2 s; no analyzer starts).
  `arbiter review --limit 60` drew fifty-nine findings; the marks and reasons are
  committed as a draft in `.arbiter/reviews/2026-10-09-debt-sweep.md` for a
  person to apply, and nothing is recorded in the knowledge ledger. The 55
  over-limit functions left alone are suppressed in `arbiter.yaml` per file,
  functions named, expiring 2027-04-01; `pytest.raises(Exception)` in the
  bad-plan test is `RuntimeError` (FAIL-047); the mutable action tags in the
  workflows are one open entry (FAIL-046); and two of the false positives were
  the probes' own, now fixed with regression tests: the suppression census
  counted `# noqa` inside Python strings and docstrings (FAIL-048) and the
  manifest reader never saw a requirement quoted past the first position of a
  one-line list (FAIL-049). Baseline cut last via `omni arbiter baseline
  --refresh`; README "Project status" records the numbers.
- ARB-050 | `arbiter_review_draft`: an assistant proposes marks with reasons and
  never records one. `docs/mcp.md` rules out any tool that records a verdict,
  and the rule stands. The new MCP tool (`service.review_draft`) draws the queue
  `review_queue` draws (same report, selection and knowledge, read and never
  saved), fills in the marks an assistant proposed with a reason on the line
  beneath each, and writes `review-draft.md` under `output_dir` in the exact
  format `review.parse` reads; its first line says every mark was proposed by an
  assistant and nothing has been recorded. The result carries counts, the ids
  not in the queue (reported, not raised) and, as text, the failure-ledger
  entries the `y` marks would draft (`ledger.preview_entries`, which opens no
  ledger). A `y` or `n` without a reason is refused. No new path argument, so
  HTTPS confinement is unchanged. `arbiter review --apply --reviewer` stays the
  only writer; the no-verdict test is extended to every tool in `TOOLS`, each of
  which must leave the knowledge file byte-identical. The skill describes the
  inline flow: draft with reasons, the person reads, the person applies.
- ARB-051 | The air-gapped bundle, without the analyzers, and the hosted
  limiter shared across processes. `arbiter bundle build --out DIR` writes
  Arbiter and its runtime dependencies as wheels (`pip wheel` of the checkout,
  offline when setuptools can build without isolation, with an isolated retry
  when a distribution-patched setuptools cannot; `pip download` for PyYAML and
  tomli, the one step that needs an index, skipped by `--no-deps-download`), the
  checkout's `.arbiter/knowledge.json` when present, `install.sh` /
  `install.ps1` (`pip install --no-index --find-links wheels arbiter-eval`), a
  README and a `MANIFEST.json` with the version, Python floor, build time and a
  SHA-256 per file; the packs ride inside the wheel and are not copied. The
  five analyzers are not included and the manifest says why (their
  redistribution review, ARB-005 L-6, is pending); operators install them from
  their own mirror. `arbiter bundle verify DIR` recomputes every hash and exits
  1 on a missing, altered or extra file. `api.FileRateLimiter` keeps the same
  caps in `limiter.db` beside the key file, one `BEGIN IMMEDIATE` transaction
  per decision, slots released on exit and expired after the scan ceiling when
  a process dies holding one; `--limiter memory|file` on `arbiter api serve`
  and `arbiter mcp --http` selects it through `api.configure_limiter`, both
  doors reading `api.LIMITER` at call time. Memory stays the single-process
  default. Proven by six spawned processes never exceeding a cap of two, and by
  a real bundle built, verified and installed with `--no-index` into a fresh
  virtualenv. `docs/bundle.md` is new; README, `docs/cli.md`,
  `docs/hosted-api.md`, `docs/mcp.md`, `NOTICE.md` and `docs/licensing.md` no
  longer call the bundle unbuilt or the shared limiter a gap.
- ARB-044 | Requirements use the `ARB` prefix. Two registries on `REQ` collided the
  moment Arbiter was vendored into OmniEngineering (each had reached number 035
  with a different requirement), so `omni requirement renumber --prefix ARB`
  renumbered every requirement keeping its number, wrote `id_aliases` so the
  commits and ledger entries that cite the old ids stay valid, and rewrote the
  governance text. Code, tests and git history are untouched; the gate resolves
  an old id through the aliases.
- ARB-043 | Synced the OmniEngineering workspace tooling as of 2026-10-09:
  `omni doctor` ends with a `Posture:` line and offers `--json`;
  `omni requirement complete` refuses while this branch's Arbiter report is
  missing, stale or red; `omni graph impact`, `omni graph findings` and
  `omni test run` exist here; the `completion.arbiter_gate` rule now runs
  `arbiter gate . --changed {base} --profile offline --baseline
  .arbiter/baseline.json --out arbiter-out/omni-gate --format json,sarif,pr-comment`,
  with `.arbiter/baseline.json` cut from a full scan and committed, so "new"
  means new since this baseline; the installed Arbiter version is recorded in
  `.ai/omni-version.json`; the recommended `completion.tests` example rule is
  present. The handbook for the whole loop lives in the OmniEngineering repository:
  https://github.com/paukennick/OmniEngineering/blob/main/docs/arbiter-integration.md.
- ARB-042 | Run history and the trend dashboard. Every `scan` and `gate`
  appends one line to `<out>/history.jsonl` (time, commit, system, profile,
  mode, grade, score, coverage, counts by severity, new high-or-above, gate
  result, duration, total findings; no evidence, no paths, no titles) unless
  `--no-history`; `arbiter dashboard [--history] [--out]` renders the trend as a
  self-contained HTML page (tiles, a score and coverage line chart, stacked
  severity bars per run, the runs table, an honest empty state). The README's
  "not yet built" list loses the dashboard.
- ARB-041 | A persistent per-file result cache for the file-local probes.
  `.arbiter/cache.json` (git-ignored, never scanned) stores per-file findings
  for `secrets`, `supply_chain`, `ast_metrics`, `house_rules_ast` and
  `authored`, keyed on the file bytes, the path, the configuration, Arbiter's
  own source and a per-probe context digest (the manifests, for `authored`).
  `--no-cache` bypasses it, `--cache-path` relocates it, `--verify-cache`
  re-runs a sample of hits and reports a divergence as
  `arbiter/assurance.cache-divergence` (high). Integrity invariant CI-13 proves
  every cacheable probe is file-local, the mutation tool proves a config change
  invalidates, and CI runs the self-gate with `--no-cache`. On the
  OmniEngineering tree with the adapters skipped (a machine under load from
  three parallel suites): cold 29.3 s wall (scan 4.4 s), warm 25.8 s wall
  (scan 1.1 s), identical findings.
- ARB-040 | Arbiter runs on Python 3.10. `tomllib` was the only 3.11 construct;
  one helper reads TOML with `tomllib` or the `tomli` fallback,
  `requires-python` is `>=3.10` with `tomli` as a conditional dependency, and
  pr-check gains an ubuntu 3.10 cell. OmniEngineering's CI now reads the floor
  from `pyproject.toml`, so its 3.10 cells run doctor and gate with a vendored
  Arbiter.
- ARB-039 | PR-native output. The gate names the findings that failed it
  (`failing_ids`); `--format annotations` prints GitHub workflow commands
  (`::error` for the gating findings, `::warning` for the rest, `::notice`
  for findings outside the change; titles and rule ids only, never evidence);
  `--github` (automatic under `GITHUB_ACTIONS=true`) adds the annotations and
  appends the pull-request comment to `$GITHUB_STEP_SUMMARY`; pr-check runs the
  self-gate in that mode and uploads `report.sarif` to code scanning
  (`continue-on-error`, so the gate's exit code stays the verdict); the
  composite action passes `--github`.
- ARB-038 | The `governance` probe and requirement attribution. A change-scoped
  probe (the new scope runs in partial and full mode) reads the OmniEngineering
  failure ledger: a changed file named as affected by an open or mitigated
  entry, with none of that entry's regression tests in the change, is
  `arbiter/governance.open-failure-untested`; a fixed entry whose regression
  test no longer exists is `arbiter/governance.regression-test-missing`; both
  medium, both n/a without a ledger. Findings inside the change carry
  `req:<ID>` tags from the commits since the base (the registry's own prefix),
  and the Markdown report and the pull-request comment gain a "By requirement" block.
- Closed ARB-024 with the pull-request check deliberately advisory. The
  Windows matrix has now run for real and found two defects (FAIL-035,
  FAIL-039), which was its purpose; the remaining criterion, a required
  status check on `main`, was weighed and declined: it only blocks a merge
  button a sole committer already reads, and the strict form would make
  every pull request stale after each nightly training commit. The
  decision is recorded on the requirement, to revisit when a second
  committer or automated merging arrives. (ARB-024)
- Arbiter now gates its own pull requests. `pr-check` installs the five
  external analyzers and the `api` extra on Linux, then runs `arbiter gate .`
  under this repository's `arbiter.yaml` after the suite, the integrity and
  mutation checks and the workspace gate; with no baseline every finding is
  new, so the policy is "no critical, no high". The first run was red for
  three reasons that were all Arbiter's: the `authored` probe reported the
  TLS-off patterns listed in its own module docstring as three high findings
  (`_blank_comments` blanked `#` comments but not Python docstrings; it now
  blanks both, as the import scanner always did), gitleaks reported fixture
  token shapes it found in a git-ignored graph file and in `__pycache__` as
  four critical secrets (adapter output is now held to the inventory's scope
  on the way in, `in_scope`, instead of teaching each tool its own exclusion
  flags), and semgrep's Python 3.6 compatibility rules fired on a project that
  requires 3.11 (suppressed by name, with the reason). The same job runs the
  sixteen hosted-API tests that had skipped on every pull request, and
  `omni gate` now runs `arbiter gate --changed` through OmniEngineering's new
  `command` validation type whenever `src/`, `tests/`, `tools/` or
  `arbiter.yaml` change. FAIL-036, FAIL-038. (ARB-032, ARB-034)
- Arbiter now installs alongside the OmniEngineering workspace. Upstream's
  `omni adopt --with-arbiter` and `omni arbiter install` (OmniEngineering
  ARB-033) pip-install `arbiter-eval[mcp]` from a checkout or the GitHub URL
  and write the wiring this repository had assembled by hand: the `arbiter`
  MCP server in `.mcp.json`, the `completion.arbiter_gate` command rule, a
  starter `arbiter.yaml`, and `arbiter-out/` in `.gitignore`. Rerunning it
  here reports every piece as already present, which is the check that the
  hand-made wiring and the generated one agree. `SETUP.md` and
  `RUNNING-ON-YOUR-OWN-CODE.md` document the path. (ARB-032)
- Cut the self-scan's noise at its five sources. `drift.doc-references-
  missing-file` asks git whether a path named in prose was ever tracked and
  reports a removed one at `info` as removed, which a changelog is right to
  mention, while a path that never existed stays `low` (24 findings, most in
  append-only history files). `assurance.blanket-suppression` no longer reads
  documentation, so a README explaining `# noqa` is not a blanket suppression.
  The `no-bare-except` house rule matched every `except` clause because its
  query had no predicate (89 findings, `except OSError:` among them); it now
  matches `Exception` and `BaseException` and leaves bare `except:` to ruff
  E722 (FAIL-037). OmniEngineering's `make_ai.py`, `omni_graph.py` and
  `omni_mcp.py` were scored as Arbiter's code (44 of 90 complexity findings);
  their quality and semgrep findings are suppressed with the reason recorded,
  so they stay in the report attributed to the tooling. And a probe that
  declares itself not applicable (the `interface` probe on a single
  repository) now leaves the coverage denominator and prints as `n/a`, while a
  probe that was prevented from running stays in it; claim invariant CI-12
  refuses a report that marks a prevented probe as not applicable, and
  `tools/integrity.py` proves it does. Coverage on this repository went from
  22% (grade withheld) to 99% with the analyzers installed. The Windows job,
  which has no tree-sitter, then showed the rule's one hole: a missing python
  package also read as not applicable, so the two AST probes left the
  denominator instead of lowering coverage. `Probe.prevented()` now reports a
  missing dependency separately and the engine records it as a prevented
  skip, like a missing binary; `applicable()` keeps only the cases where
  there is nothing to assess, and CI-12 names the new wording (FAIL-039).
  (ARB-033)
- `arbiter review --apply --ledger FILE` drafts an open OmniEngineering
  failure-ledger entry for every verdict recorded as a true positive: title,
  severity, rule, location and the finding id, nothing the scanner cannot
  know. Applying the same marks twice adds nothing, and the file passes
  `omni failure check`. The adjudication ledger and the failure ledger were
  parallel records of the same defects; now one feeds the other. (ARB-035)
- Added the Claude Code skill (`.claude/skills/arbiter/SKILL.md`): which
  surface to use for what, how to read a withheld grade and a not-assessed
  probe, how to adjudicate, and what the self-gate requires of a change.
  (ARB-036)
- Added the four commercial control packs README had listed as unbuilt: PCI
  DSS v4.0 (32 of 63 requirement sections), the HIPAA Security Rule (32 of
  59 standards and implementation specifications), SOC 2 TSC 2017 (29 of 61
  criteria) and CIS Controls v8 (41 of 153 safeguards). Each follows the
  government packs' convention of declaring the framework's full size and
  enumerating the scanner-relevant subset, names the human work that remains
  on every control, cites only rule ids that exist, and says that PCI DSS and
  the TSC are licensed documents whose identifiers must be verified against
  the licensed text before an audit package cites them. (ARB-037)
- Made the three setup-script tests run the bash that `PATH` resolves rather
  than the bare name `bash`. The first run of the Windows half of `pr-check`
  (it had never run: this was the workflow's first pull request) failed all
  three, because `CreateProcess` searches `System32` before `PATH` and
  `System32\bash.exe` is the WSL launcher, which prints an install prompt in
  UTF-16 and exits 1 whether or not Git Bash is installed. The one test that
  already went through `shutil.which("bash")` passed on the same runner,
  which is the whole diagnosis; the other three now do the same and skip, as
  it does, only when no bash exists at all. (ARB-024)
- Exempted Arbiter's own test material from the gate's secret check. The
  first change set to touch `tests/` tripped `data.privacy` on a planted PEM
  header: the check is right in general and wrong for a secret scanner's
  corpus, so `tests/**`, `fixtures/**` and `examples/**` are listed in the
  rulepack's ignore set; `src/` and `tools/` hold no literal key shape and
  stay scanned. (ARB-031)
- Re-synced the OmniEngineering workspace to its real upstream and adopted
  the tooling that arrived there since June. Arbiter's `omni` was a fork
  assembled from STEP-Migration copies (ARB-001) and closest to an upstream
  commit from 2026-06-23; `python omni update` now 3-way-merges against the
  ref recorded in `.ai/omni-version.json`, so later template changes are a
  command rather than a re-copy. What changed in the repository: the
  requirements registry and its archive moved to the upstream flat shape
  (`requirements-archive.json`; the categories-keyed form was arbiter-only
  and every new upstream command reads the flat one) and are now queried and
  changed only through `python omni requirement` -- never read or edited
  whole; the five non-Claude assistant shims, `.cursorignore` and the
  OmniEngineering `NOTICE`, `TRADEMARKS.md` and `LICENSES/` are installed,
  which closes the eight `omni doctor` errors this repo had carried as an
  accepted gap since ARB-001 (arbiter's own `NOTICE.md` and `LICENSE` are
  unchanged); `python omni gate` is enforced by `.githooks/pre-commit`, by the
  Claude Code Stop hook in `.claude/settings.json` (now tracked; the
  machine-specific Headroom wiring stays in `settings.local.json`) and by
  `pr-check.yml`, with `training/**` and `.arbiter/**` exempt so the nightly
  job's measurement commits are not blocked; `.ai/test-suites.json` registers
  the pytest suite and `tools/check_writeback.sh`; `.ai/graph-config.json`
  builds the code graph with `fixtures/`, `examples/`, `training/`,
  `.arbiter/` and the scan output directories kept out of the code layer, so
  a planted defect's symbols are never attributed to arbiter; `.mcp.json`
  registers `python omni mcp serve` beside `arbiter mcp`, and doctor now
  starts both servers for real. Three stale playbooks (`planning`, `testing`,
  `release`, `implementation`, `pre-implementation`) still carried
  STEP-Migration text -- `python3 app.py`, `validate_stacks.py`, "`omni map`
  is never run here" -- that contradicted this repo's own configuration;
  they are upstream's versions now. The adapters, `public-release.md`,
  `fallback-llm-rules.json` and `hci-ui-rules.json` authored for arbiter in
  ARB-028 were kept and merged. (ARB-031)
- Backfilled the failure ledger. `.ai/failures/failure-ledger.json` now
  records the 34 shipped defects that `CHANGELOG.md`, `.ai/project-context.md`
  and the requirement records already describe, FAIL-001 (the read cache
  serving stale bytes, ARB-007) through FAIL-034 (CRLF dropped when file
  reading moved to bytes, ARB-030), each with its root cause, the regression
  test that now catches it, and the rule that prevents a repeat. Six have no
  regression test and say so (`no_test_reason`); twelve have no fix commit
  because the clone is shallow and the 2026-09-12 history is not in it.
  `python omni graph why <path>` and `python omni failure search` read this
  ledger, and `python omni failure add` is how the next defect is recorded.
  (ARB-031)
- Ported three arbiter-only improvements upstream instead of keeping them as
  a fork, as OmniEngineering ARB-031: the `withdrawn` terminal status with
  `requirement archive --id` refusing live work; doctor's live check of every
  MCP server `.mcp.json` registers (it replaces the arbiter-specific
  `validate_mcp_server` from ARB-025 and checks both servers here);
  `exclude_code_globs` for the graph; and dated `###` sections under an
  `[Unreleased]` heading -- this file's format -- parsed as separate
  changelog entries. (ARB-031)

### 2026-09-16

- Stopped vendored and generated content from being scanned as first-party
  source, found by running Arbiter as a system over two real repositories
  (STEP-Migration + STEP_App) rather than the fixture corpus. Three
  compounding gaps together reported a third-party library's own source as a
  leaked private key and buried real findings under noise: `_pem_has_key_material`
  judged a PEM block real if any text after a `BEGIN` marker contained a colon
  anywhere in the next ~4000 characters — a check meant to allow encrypted-key
  preambles (`Proc-Type: 4,ENCRYPTED`) but satisfied by ordinary code and
  prose, so `ecdsa`'s own PEM-parsing source and packaging metadata were
  reported as committed keys; it now walks forward line by line and stops at
  the first line that is not base64 or a `Key: value` header, so surrounding
  text cannot manufacture a body. `inventory.classify()` hardcoded the literal
  name `cdk.out`, so a project using `CDK_OUTDIR` to redirect synth output to
  a renamed directory (`cdk.out.chk`, seen on the real system) fell through to
  `role=source`, and a branch-ordering bug meant even the unrenamed case would
  have classified a CDK stack's own synthesized `*.template.json` as
  `generated` rather than the `iac` role that exempts nothing — losing the
  ground-truth scanning `SKIP_DIRS` already says is deliberate; `IAC_HINTS` is
  now checked first, and both hint patterns accept a `cdk.out*` suffix.
  Bandit and checkov each walk `{workdir}` with their own traversal,
  independent of `SKIP_DIRS`, so both walked into a full vendored `.venv` and
  a Lambda asset bundle's dependencies regardless of role — bandit alone
  produced 129,379 raw findings and consumed 669s of its 900s budget on one
  such tree; both are now invoked with exclusions mirroring `SKIP_DIRS`, plus
  a CDK asset-bundle pattern for bandit (which gets no value from a synthesized
  template — it only reads Python) and a narrower one for checkov (which
  still needs the templates). A real system scan went from 3 critical / 6,011
  total findings to 0 critical / 1,121, with bandit's runtime dropping from
  669s to 4s. (ARB-026)
- Fixed two MCP tests reading pydantic alias names instead of the field
  names the installed SDK actually exposes (`.serverInfo`, `.isError`,
  `.inputSchema`) — those models take the camelCase alias on construction
  but expose the field back out as the snake_case Python attribute via an
  alias generator, confirmed directly from the installed SDK's own
  `model_fields`. Pre-existing on `main`, unrelated to ARB-026; found while
  running the full suite during that work and confirmed by reproducing it
  against plain `main` with ARB-026's changes stashed. (ARB-027)
- Registered Arbiter's own MCP server for this repo and made OmniEngineering
  verify it rather than take it on faith. `.mcp.json` now names an "arbiter"
  server that runs `arbiter mcp` (stdio), so an assistant working here can
  call `arbiter_scan`, `arbiter_gate` and `arbiter_review_queue` directly
  instead of shelling out to the CLI — the CLI stays the only way to run
  `arbiter review --apply`, since no MCP tool records a verdict. `omni
  doctor` gained `validate_mcp_server`, which imports `arbiter.mcp` and
  builds the server for real rather than grepping `.mcp.json`'s text for the
  right command, because the `mcp` SDK's own shape has moved under this
  project before (`23bc43e`, same day) and a static check would not have
  caught that. (ARB-025)

### 2026-09-17

- Started testing the scanner against reformulations of its own input, and
  started testing the code that measures the rules. An audit of every testing
  parameter and of the whole bug history found nineteen shipped defects
  collapsing into five recurring causes -- platform and path assumptions,
  encoding defaults, attribution propagation, heuristic proxies with edges
  nobody had hit yet, and bugs in the measurement code itself. Each had been
  fixed with a fixture for the one case that failed, which is why the missing
  `repo_id` had to be found three separate times. The new tests state the
  property instead: no finding anywhere may carry a backslash, an absolute or
  an escaping path; every finding in a multi-repo scan must name a repository
  that exists; every text read and write under `src/` must name its encoding;
  a credential must be found under every encoding and every syntactic carrier
  while placeholders are not reported; and calibrated confidence must not move
  when synthetic volume does. `tests/test_properties.py` generates inputs for
  the hand-rolled HCL reader rather than listing them, since every bug those
  functions have had was an edge of the grammar nobody thought to write down.
  (ARB-030)
- Fixed a fingerprint that was not stable across platforms. `Finding.fingerprint()`
  hashed `location.path` as written, so the same path with backslashes
  and with forward slashes were two identities for one finding.
  Adjudications are permanent and keyed by that
  fingerprint and `record()` refuses to re-adjudicate, so a verdict recorded on
  one platform could never match the same finding on another, and a baseline
  built on one reported every finding as new on the other. Normalization now
  happens at the `Location` boundary that all ~54 construction sites pass
  through, rather than at each of them -- which is the form that was tried and
  missed in ARB-014, ARB-015, ARB-016 and again in ARB-029. (ARB-030)
- Connected the nightly external-severity measurement to the scanner.
  `tools/calibrate_external.py` wrote its measured severities to
  `.arbiter/external-severity.json` while `learn.apply()` read a flat map
  inside `knowledge.json`, and no code path moved a value from one to the
  other -- so every nightly run since the tool was written had measured 818
  external checks and changed nothing, and the 178 severities in the live
  ledger came from a one-off step that no longer exists. Merging the current
  report yields 288 entries, 110 of them checks that had no measured severity
  at all. (ARB-030)
- Stopped reading every UTF-16 file as a binary blob. `inventory._is_binary`
  treated any NUL byte as binary, and UTF-16 puts one between every ASCII
  character, so a file an editor saved as "Unicode" was classified as data and
  never read -- a credential in one was invisible. A declared byte-order mark
  now means text. Headerless UTF-16 is still left as data on purpose: telling
  it from a real binary means guessing, and guessing wrong turns a binary into
  mojibake to scan. (ARB-030)
- Gave seven configuration reads an explicit encoding. `policy.py`, `ab.py`,
  `controls.py`, `probes.py` and `adapters.py` read YAML and TOML under the
  platform default codec, which on Windows is cp1252 and mis-decodes silently
  rather than raising. This is the same defect as ARB-009 and ARB-012, in
  seven call sites those fixes did not touch; the new test checks the property
  across `src/` rather than the two sites that were known. (ARB-030)
- Kept line endings translated when file reading moved to bytes. Reading a
  file as bytes rather than as text is what made the byte-order-mark check
  possible, and it silently dropped Python's newline translation with it: six
  secret formats stopped being detected on any CRLF file, because the rule
  that reads `name = value` ends at the end of the line and was seeing a
  trailing carriage return. The existing suite caught it. The first test
  written to cover it did not -- it used an AWS key, which is recognised by
  its own shape wherever it appears and so was found with or without the
  carriage return; `tools/mutate_tests.py` reported that test as passing with
  the defect present, which is the whole reason that tool exists. (ARB-030)
- Added `tools/mutate_tests.py`, which puts each of those defects back and
  requires the test named after it to fail. A test that has never failed is
  not evidence, and this is the same discipline `tools/integrity.py` already
  applies to the claim invariants. It runs in the pull-request check, so a
  regression test that stops testing anything fails the build. (ARB-030)
- Wrote `docs/testing-parameters.md`: every constant that governs a verdict,
  what it decides, and how firmly it is held -- derived, reasoned or
  arbitrary. Most are arbitrary, which is the honest grade for a number
  nobody has tested an alternative against. It also records two constants
  sharing the name `MIN_OBSERVATIONS` while meaning unrelated things, and
  three unreconciled ratio thresholds deciding closely related questions.
  Nothing was retuned: changing any of them changes published evidence.
  (ARB-030)


- Fixed four related gaps in how adapters read tool output and what they
  scan, found while continuing ARB-026's cross-repo scanning work. gitleaks
  has no write-JSON-to-stdout mode; `--report-path` took the conventional
  `-` literally, creating a file named `-` inside the repo being scanned (the
  adapter never set `cwd`, so `{workdir}` was the default) full of gitleaks'
  own JSON output for the next scan of any repo to read as content ---
  `Adapter.invoke()` now substitutes a private temp path for a
  `{report_file}` placeholder, reads it back once the process exits, and
  deletes it in a `finally` block. checkov and bandit hand back
  backslash-separated relative paths on Windows --- checkov's own output even
  starts with a bare leading backslash --- but every suppress-rule glob and
  `Location.path` in this codebase assumes `/`, so path-based rules silently
  matched zero Windows findings from either tool; adapter output is now
  normalized to forward slashes before that matching. ARB-026's own risk
  notes flagged that its bandit/checkov exclusions, mirroring
  `inventory.SKIP_DIRS`, left semgrep uncovered even though it walks
  `{workdir}` with the same independent traversal; semgrep now carries the
  same exclusion list plus a `cdk.out*` pattern, since none of its configured
  rulesets understand CloudFormation templates either. Finally, `SKIP_DIRS`
  is a fixed name list that can never cover a fresh `arbiter-out/` (or any
  other untracked, `.gitignore`-matched directory) left over from a previous
  run under whatever `--out` name was given that time; `walk_repo` now also
  excludes whatever `git ls-files --others --ignored --exclude-standard
  --directory` reports, the same plumbing `incremental.py` already uses for
  changed-file detection. (ARB-029)

### 2026-09-14

- Ran the test suite on Windows in CI, and made the Windows-only code paths
  testable everywhere. No automation had ever run on Windows: `pr-check.yml`
  was ubuntu-only and `train.yml` is a single ubuntu job. That is how ARB-006
  survived to be found by hand — a crash on *every* adapter timeout on Windows,
  because `os.killpg` does not exist there and the process-group path raised
  `AttributeError` while the analyzer ran on past the timeout meant to stop it.
  The pull-request check is now a matrix over ubuntu-latest and windows-latest
  with `fail-fast: false`, so a Linux failure cannot cancel the Windows job and
  hide the result the matrix was added for, and both jobs run `pytest -rs` so
  each platform's skips appear in the log with reasons rather than silently
  absent. The matrix went on the one-minute pull-request workflow rather than
  the two-hour nightly one, which would have cost four hours to learn the same
  thing. Separately, `_kill_tree` is unreachable on a machine that has process
  groups, so four of the seven new tests take the platform away instead of
  waiting for one: they delete `os.killpg` and assert the fallback kills a real
  process, uses `taskkill /T` rather than killing only the direct child and
  leaving the fanned-out workers alive, and still kills the process when
  `taskkill` is absent or refuses. `tools/integrity.py` was verified on Windows
  before CI was made to depend on it — 354,294 reports enumerated, 0 integrity
  failures. 410 tests to 417. (ARB-024)

- Gave the nightly training job an owned contract and a preflight. The job
  produces the project's only accumulating evidence and had never been covered
  by a requirement; four defects were repaired in it on 2026-09-13 as unplanned
  work, including one where `git add -A .arbiter training` matched a gitignore
  entry, exited 1, and failed the job two seconds after a fifty-five-minute
  cycle had finished — so run 1 measured everything and committed nothing. The
  five accumulating files (`.arbiter/knowledge.json`,
  `.arbiter/external-severity.json`, `training/WORKLIST.md`,
  `training/fix-pairs.json`, `training/disagreements.json`) are now named in one
  place, `tools/check_writeback.sh` asserts none is ignored and dry-runs the
  exact `git add` the job ends with, and both CI and a laptop run it *before*
  the cycle rather than discovering the problem after the work. The push now
  rebases first, because losing a race to another commit is the same lost night
  by a different route. Four tests cover it, including one that reproduces the
  original gitignore arrangement in a temporary repository and asserts the check
  refuses it. (ARB-023)

- Made adjudication require a terminal. `arbiter feedback` and
  `arbiter review --interactive` now check `stdin.isatty()` and refuse when
  nothing is typing, so a piped script, a CI step or an agent shelling out
  cannot write a permanent verdict by accident into a ledger that refuses to
  re-adjudicate it. A deliberate batch import stays possible: `feedback --batch`
  records the entry point `feedback-batch`, and `review --apply` already
  recorded `review-apply`. Both join `import` in a new
  `NON_INTERACTIVE_ENTRY_POINTS`, which is the set worth filtering on later.
  `review --interactive` has no override, because a keypress interface driven
  by something that is not a keyboard is a batch import under another name and
  `--apply` already is one. This is a guard against accident and not proof of
  personhood — anything determined allocates a pseudo-terminal — and
  `docs/calibration.md` says so in those words. (ARB-021)

- Made adapter scope a declaration instead of an inheritance, and said out loud
  what a partial scan does not run. All five external analyzers (ruff, bandit,
  checkov, semgrep, gitleaks) were constructed without a `scope` argument, so
  they took `Probe`'s `repo` default. The value was right; nobody had chosen
  it. Each manifest now carries `scope = "repo"` with the reason, `load_adapter`
  refuses a scope that is neither `file` nor `repo`, and a test fails if a
  manifest omits the key or claims `file` — which would assert a subset-exactness
  nobody has measured. The skip reason was also wrong: every repo-scoped probe
  printed "this check reads relationships between files", true of the native
  probes and false of an external analyzer, which is held back only because it
  is unmeasured. `Probe.scope_reason` now carries the real one. `docs/ci.md` and
  `RUNNING-ON-YOUR-OWN-CODE.md` state plainly that a pull-request gate runs
  Arbiter's own probes and no third-party analyzer at all. Nothing about what
  runs changed. (ARB-022)

- Gave every adjudication verdict a name. `learn.record()` stored a bare string
  — `"true_positive"` or `"false_positive:note"` — with no reviewer, no
  per-verdict timestamp and no record of which command it arrived through; only
  the rule carried `first_seen`/`last_seen`. Because the ledger refuses to
  re-adjudicate a fingerprint, a wrong mark was permanent *and* anonymous, so
  there was no way to work out whose marks to distrust. Verdicts are now a
  `Verdict` record carrying verdict, reviewer, timestamp and entry point.
  `reviewer` is keyword-only with no default, so no caller can record a verdict
  without saying who is answerable for it, and the CLI resolves it from
  `--reviewer` or `git config user.email` and refuses when it has neither
  rather than inventing one. This is attribution, not authentication — it makes
  a bad batch findable and is not evidence that a person produced the mark.
  Schema 1 ledgers migrate with the reviewer `unattributed`; the verdicts and
  counts survive and no plausible name is invented for them. `KNOWLEDGE_VERSION`
  is now 2, and `Knowledge.from_dict` returns a current-schema object rather
  than carrying the file's old version number forward — a migrated ledger was
  otherwise saved still declaring schema 1. The committed
  `.arbiter/knowledge.json` moved from `k:0f258c4ce717` to `k:a37f38560140`
  with no evidence change, so any scan pinning the old hash must be re-pinned.
  Merging alongside training run 2 moved it again, to `k:2e88d7a07351`; that
  second move carries the run's evidence and this one does not. (ARB-020)

### 2026-09-13

- Retired the stale one-session instructions in `HANDOFF.md`. It now routes a
  fresh session to the maintained context and requirements registry, records
  ARB-018 as the dependency root for ARB-019 and ARB-010, and leaves ARB-005
  explicitly with counsel instead of claiming adjudication is the project's
  only blocker. After the rebased implementation passed all 399 Linux tests
  with the `api` and `mcp` extras installed (396 passed, 3 skipped), the three
  requirements were archived in dependency order. (ARB-010, ARB-018, ARB-019)
- Removed the plaintext listener entirely. `--behind-proxy` bound a bare socket
  on loopback and treated `X-Forwarded-Proto: https` as proof the request had
  been secure earlier in its life; that is a header any client can invent and,
  more to the point, a socket carrying API keys and somebody's source in clear.
  Both surfaces now require `--cert` and `--key` — there is no arrangement in
  which a proxy removes that, because the hop from the proxy is a socket too.
  `require_tls` takes only the scheme the socket actually spoke, nothing reads
  the forwarded header, and a test tokenizes `api.py` and `mcp.py` to fail if
  either name comes back. `deploy/` follows: a one-shot `certs` service issues
  the backend a self-signed certificate for `arbiter.internal`, Caddy dials
  `https://127.0.0.1:8443` and verifies against exactly that certificate rather
  than skipping the check, and the container health check does the same instead
  of faking a forwarded header. (ARB-010, ARB-018)
- Fixed the nightly training job, which had never once written its results back.
  `git add -A .arbiter training` matched `.gitignore`'s `training/`, git exited 1,
  and the step's `bash -e` failed the job two seconds after a 55-minute cycle
  finished successfully — so run 1 measured everything and committed nothing.
  `training/` now ignores its contents rather than itself, and the three files
  that accumulate (`WORKLIST.md`, `fix-pairs.json`, `disagreements.json`) are
  tracked while the per-run stamped logs stay out. (No requirement covers the
  nightly training job — the four entries below are unplanned repair, and the
  gap is itself worth a requirement.)
- Fixed the same job silently degrading the handoff it exists to produce. Its
  final step read `/tmp/corpus-out/summary.json` and `/tmp/discriminate.json`,
  which nothing writes, so every night it overwrote the complete `WORKLIST.md`
  that `train_cycle.sh` had just written with one headed "Incomplete: no results
  found for corpus, discrimination". It now reads the newest stamped files under
  `training/`, and still runs on failure so a cycle that dies early leaves a
  handoff from what did finish.
- Clone the practice repositories 300 commits deep instead of 1, and deepen the
  ones already on disk. `fixpairs.py` skips a shallow clone, so fix-pair mining
  had been reporting "0 candidate fix pairs from 41 repositories in 0s" — every
  repository skipped, every night. Those pairs are the only ground truth in the
  system Arbiter did not generate itself, so the cost in clone size is worth
  paying. `train_cycle.sh` now runs `fetch_corpus.sh` every cycle rather than
  only when the corpus is missing, because that is what catches up a cached
  clone; the script is idempotent and records the depth it reached.
- Restored `.arbiter/knowledge.json` from the artifact of the failed run: 27
  rules with measurements where the committed copy had 15, 55,075 planted faults
  against 44,000, and the 12 additional rules all previously unmeasured. Human
  adjudications are 0 in both copies, so nothing a person decided was touched.
  `training/disagreements.json` came back with it — 317 contested findings where
  exactly one of two analyzers is wrong.
- Installed the `api` extra and put the assembled application under test for the
  first time: thirteen tests now go through a real request, twelve of them new —
  missing, unknown and revoked keys, the plaintext refusal and the forwarded
  header that cannot override it, the HSTS header on refusals as well as successes, an oversized upload, a networked
  profile, the hourly cap and its `Retry-After`, an end-to-end scan, and the
  audit line for a served request and a refused one. The client dependency
  (`httpx2`, which starlette's `TestClient` now requires) is recorded in the
  `dev` extra so these run rather than skip. (ARB-018)
- Fixed two defects those tests found, both of which would have met the first
  tester. `from __future__ import annotations` makes every annotation in
  `api.py` a string, and FastAPI resolves them against the module's globals —
  where neither `UploadFile` nor the `ReviewRequest` body model could be found,
  because both are local to `create_app` by design. Every upload request failed
  inside body validation, and `/v1/review-queue` read its body as a query
  parameter and rejected all JSON. Both names are now published to the module
  when the app is built. (ARB-018)
- A report with nothing left to review now comes back as an empty queue instead
  of an error. `arbiter review` exits 0 and writes no file when a report holds
  no findings or every finding has already been adjudicated; `service.review_queue`
  treated the missing file as a failure, so a caller with a clean report — the
  good outcome — got a 400 naming a server temporary directory. (ARB-018)
- Gave the hosted API a client, in `src/arbiter/client.py` and the new `arbiter
  remote` command group. Until now the service had no caller half: every CLI
  command either ran the scanner locally or ran it on the server, and the
  documented way to reach a hosted instance was to assemble multipart uploads by
  hand. `arbiter remote scan .` packs the directory, sends it, and renders the
  answer through the same functions `arbiter scan` uses, so `--out`, `--format`
  and `--limit` mean what they always meant — with none of the five analyzers,
  the grammar pack or the rule engine installed on the caller's machine.
  `remote gate` exits non-zero on a failed gate and deliberately takes no
  `--out`, because `/v1/gate` answers with the verdict and the claim ledger
  rather than a report. `remote health` needs no key, so somebody can see what a
  server offers before asking for access to it. Settings resolve flags first,
  then `ARBITER_SERVER` and `ARBITER_API_KEY`, then `~/.arbiter/client.json`, so
  a one-off `--server` cannot lose to a stale file. The transport is `urllib`
  from the standard library rather than `requests` or `httpx`: a plain install
  still depends on PyYAML alone. (ARB-019)
- Three of the client's refusals happen before anything leaves the machine. An
  `http://` address is rejected outright — the server refuses plaintext too, but
  its refusal arrives after the key has already crossed the network in the
  clear. Certificate verification cannot be switched off: `--cacert` adds a root
  to trust, nothing subtracts one, and a test asserts `CERT_NONE`,
  `check_hostname = False` and `_create_unverified` appear nowhere in the module.
  An oversized target is refused against the cap `/v1/health` publishes, instead
  of uploading for two minutes to earn a 413. The archive also skips
  `inventory.SKIP_DIRS`, so `.git`, `node_modules` and `.venv` never leave the
  caller's disk — source that never left is source nobody has to be trusted
  with. No client call records an adjudication verdict, because no endpoint
  does. 11 tests cover this, and the end-to-end ones route the client's own
  `urllib` through the real application, so the multipart field name, the query
  string and the `X-API-Key` header are proven against the endpoints rather than
  assumed to match. Suite: 381 passed, 3 skipped. (ARB-019)
- Amended ARB-010 and added ARB-019. ARB-010 had justified the MCP server as
  having been "chosen over a hosted API"; that rationale is withdrawn, since
  both now exist, and the requirement gains a multi-user obligation — an HTTP
  transport that authenticates every call against the same key store as the
  hosted API, with the resolved caller reaching `dispatch` so the rate limiter
  and the audit line apply per key, while stdio keeps working unauthenticated
  for the single-user local case. ARB-019 is the client: a CLI that reaches a
  hosted Arbiter, adding no runtime dependency beyond PyYAML. (ARB-010, ARB-019)
- Built the MCP server's second transport, so more than one person can use it:
  `arbiter mcp --http` serves the same three tools over HTTPS, where `arbiter
  mcp` still serves one local agent over stdio. Every HTTP call carries a key
  from the same file `arbiter api key add` writes and `/v1/scan` reads —
  `Authorization: Bearer` or `X-API-Key`, both accepted — so access is granted
  and revoked in one place for both front doors, and a revoked key stops working
  on the next request to either. The resolved caller reaches `dispatch`, which
  takes the same per-key concurrency slot the API takes and writes the same
  audit line (`mcp_scan`, `mcp_gate`, `mcp_review_queue`, with the key id, the
  user, the status and the duration), so a key's budget is one budget rather
  than one per surface. Plaintext is refused with `426` and every response
  carries HSTS, exactly as on the API. Stdio stays unauthenticated, because
  whoever started that subprocess already holds the privileges it runs with.
  (ARB-010)
- Confined every path argument on that transport, which is the thing that made
  it safe to expose at all. These tools take `target` and `output_dir` as paths
  **on the server**: right for a local agent, and for a remote caller an
  arbitrary file read with a scanner attached, since `target: "/etc"` would come
  back as findings quoting what is in there. So `--http` refuses to start
  without `--root`, and rewrites every path to sit beneath `root/<key id>` —
  one directory per key, so callers are separated from each other and not merely
  from the rest of the disk. Relative paths are joined onto that directory,
  absolute and `..` escapes are refused by `service.resolve_within`, which
  resolves symlinks before comparing. The arguments treated this way are listed
  in `mcp.PATH_ARGUMENTS`, and a test fails if a tool grows a path argument that
  is not in the list, because that one would be unconfined and silently so.
  (ARB-010)
- Raised the SDK pin from `mcp>=1.0` to `mcp>=2.2`, and found while doing it
  that `arbiter mcp` had been broken against anything 2.x: the server API moved
  handlers from decorators (`@server.list_tools()`) to constructor arguments, so
  the stdio server raised `AttributeError` at startup under the old pin's own
  range. `_build_server` now builds both transports the one way, so they cannot
  drift in what they expose, and a test constructs every entry of `TOOLS` as an
  SDK `Tool` — the schemas are plain data precisely so tests can read them
  without the SDK, which is also why nothing noticed when the SDK renamed the
  field they map to. A tool that refuses is now returned with `is_error` set
  rather than as ordinary text an agent would read as a result. 14 tests cover
  the transport. Suite: 395 passed, 3 skipped. (ARB-010)
- Wrote `docs/mcp.md`, which ARB-010 has required all along and which did not
  exist: the tool surface, which transport needs a key, the path confinement and
  why, and the one flag the proxy arrangement needs. The transport's
  DNS-rebinding guard allows loopback names only until it is told otherwise,
  while a proxy forwards the public hostname — so every real request comes back
  `421 Invalid Host header` unless `--allowed-host` names it. That cannot be
  detected from inside, so it is documented as the first thing to check when
  every call fails and none of them reaches the audit log. The README's documentation
  table was also missing `hosted-api.md`, `mcp.md`, `pilot-runbook.md`,
  `pilot-terms.md` and `licensing.md`; all five are listed now. (ARB-010)

### 2026-09-12

- Built the hosted API over that service layer, in `src/arbiter/api.py`. Access
  is distributed by hand: `arbiter api key add --user "..."` mints one key for
  one user, prints it once and stores only its SHA-256 hash, so the key file is
  not a credential store; `key list` and `key revoke` complete the set. A key is
  scoped to a user and nothing else, and one user holds at most one live key —
  minting over a live key is refused unless `--replace` is passed, which revokes
  the old one in the same command, so a shared or half-rotated key cannot arise
  quietly. `POST /v1/scan`, `/v1/gate` and `/v1/review-queue` take an uploaded
  archive — never a repository credential — and `GET /v1/health` needs no key.
  Nothing is retained: the workspace is deleted when the request ends and server
  paths are withheld from responses. Uploads are capped at 100 MB and refused
  before extraction. FastAPI and uvicorn are an optional extra imported only
  inside `create_app`, so a plain install still depends on PyYAML alone.
  `arbiter mcp` now runs the MCP server. (ARB-018)
- Raised the per-key request limit from 30 an hour to 120, and published every
  limit on `GET /v1/health` alongside `"free": true`. Nobody is charged and the
  service is for people testing it, so a limit anybody can feel during normal
  use is a restriction dressed up as capacity. The concurrency caps are
  unchanged, because those are what decide whether the machine stays up.
  Reworded the access and terms documents to say plainly that it is free, that
  there is nothing to apply for, and that nobody is asked what they intend to
  scan; issuing keys by hand is how access works in the absence of an identity
  system, not a vetting step. (ARB-018)
- Added the pilot deployment in `deploy/`: a Dockerfile that installs Arbiter
  with the `api` and `tools` extras into a virtualenv and copies it into a clean
  image running as uid 10001, a `compose.yaml` pairing it with Caddy, and a
  `Caddyfile` carrying the hostname, the ACME contact and a 110 MB body limit.
  A one-shot `certs` service gives Arbiter its own certificate so the hop from
  Caddy is HTTPS as well, and Caddy verifies against that certificate rather
  than skipping the check; Arbiter also shares Caddy's network namespace, so
  only Caddy can reach the port at all. The container is read-only, capability-free, `no-new-privileges`, and
  capped at 3 GB, 2 CPUs and 512 processes, because the analyzers parse
  attacker-chosen files even though nothing from an upload is executed. The
  image must stay private: running semgrep conveys no copy, but publishing the
  image would. (ARB-018, ARB-005)
- Capped concurrent scans for the whole server at 4, not just 2 per key, since
  the per-key limit multiplies by the number of testers and five of them at once
  would be ten analyzer runs on one machine. A caller over their own share is
  told that rather than told the service is busy. (ARB-018)
- Added a request log: one JSON line per request to `~/.arbiter/audit.log`
  (`--audit`, `ARBITER_AUDIT`, or `--no-audit` to keep nothing), holding the key
  id, the user, the operation, the status, the bytes uploaded and the elapsed
  time — and nothing about the code, because a log that quoted findings would
  rebuild on disk what the request path deletes. Failures and refused keys are
  logged too, the latter without writing the rejected key down. A log write that
  fails complains on stderr rather than failing the scan. (ARB-018)
- Documented how to run the pilot in `docs/pilot-runbook.md` — container with a
  memory limit, TLS-terminating proxy with a body limit, one key per tester,
  what to read in the log — and what to tell a tester about their code in
  `docs/pilot-terms.md`, which is a draft pending counsel. (ARB-018, ARB-005)
- Made TLS mandatory on the hosted API, with no plaintext mode. Requests carry
  an API key and a copy of somebody's source, so `serve` refuses to start
  without `--cert`/`--key`, and refuses individual plaintext requests with
  `426 Upgrade Required`. Direct TLS
  offers forward-secret AEAD ciphers only, which leaves nothing a TLS 1.0 or 1.1
  client can negotiate — a hard version floor is not assertable from here,
  because uvicorn builds its own SSL context, so it comes from the platform
  policy or from a terminating proxy. Every response carries a two-year
  `Strict-Transport-Security` header, and the default port is now 8443.
  (ARB-018)
- Made an API key a limited grant rather than a permanent one, before any key is
  handed out. Keys now expire after 90 days by default (`--expires-days`, or
  `--no-expiry` for a deliberate permanent one), a single key is capped at 30
  requests an hour and 2 concurrent scans, and exceeding either returns `429`
  with `Retry-After`. Limits are per key, so one recipient cannot exhaust
  another's. Expired, revoked and unknown keys share one `401` message, because
  distinguishing them would confirm that a guessed key had once existed. The
  counts live in one process's memory, which bounds this to a single-machine
  deployment — recorded in `docs/hosted-api.md` rather than implied. (ARB-018)
- Began the move to a hosted API, reversing the decision to expose Arbiter only
  over MCP. MCP relocates the installation rather than removing it, and "no
  local install" was the requirement. Both surfaces now call one
  transport-neutral service layer, `src/arbiter/service.py`, which owns the
  containment: workspaces created outside the server tree with `source/` and
  `output/` as siblings and removed when the scan ends; uploaded archives
  refused whole if any member is absolute, traverses upward, is a link or a
  device node, or breaches the size and count bounds; `connected` and `audit`
  refused unless an operator explicitly allows the network; and no operation
  that records an adjudication verdict, asserted by test rather than convention.
  `docs/hosted-api.md` holds the design; the HTTP surface is not built and the
  framework is deliberately unchosen. The licensing objection recorded against
  hosting was wrong and is corrected in `docs/licensing.md` — LGPL-2.1
  obligations attach to conveying a copy, so L-6 does not gate a hosted service.
  (ARB-018, ARB-010)
- Restructured the documentation. `README.md` is now an overview — what Arbiter
  is, its capabilities, administration, versioning and licensing — and the
  granular material moved into `docs/` split by category: `architecture.md`,
  `cli.md`, `configuration.md`, `probes.md`, `systems.md`, `compliance.md`,
  `evidence.md`, `calibration.md`, `ab-testing.md` and `ci.md`. `SETUP.md` is
  now an installation guide; its training-operations content moved to
  `docs/ci.md`. (ARB-002)
- Settled the corpus composition figures. The docs quoted 42 repositories in one
  place and 39 in another, and the population table mixed a tuned-only count for
  the vulnerable population with full counts for the other two. `tools/corpus.py`
  is authoritative — 41 repositories, 5 held out, 36 tuned — and a new
  `--counts` flag recomputes that without the corpus cloned, so the numbers stop
  drifting. Stack gaps are now reported in both directions. (ARB-003)
- Bounded requirement registry growth. Completed and withdrawn requirements are
  swept into `.ai/requirements/archive.json`, which no context profile loads, by
  `python omni requirement archive`. IDs are allocated across both files and are
  never reused; `omni doctor` errors if they ever collide. The active registry is
  read into every session, so it is a working set, not a history. (ARB-004)
- Wrote up the licensing requirements in `docs/licensing.md`: eight requirements
  covering the operative grant, copyright ownership, ownership of scan output and
  the redistribution review that blocks the air-gapped bundle. `NOTICE.md` now
  records every third-party component and whether it is redistributed. The
  `LICENSE` file itself remains outstanding and needs counsel. (ARB-005)
- Made adapter timeouts stop the analyzer on Windows. `Adapter._kill_group`
  named `signal.SIGKILL`, which does not exist there, so every timeout raised
  `AttributeError` out of the cleanup path. Windows also has no `os.killpg`, so
  signalling the direct child alone left the fanned-out workers running — the
  exact failure the process-group path exists to prevent. Timeouts now fall back
  to `taskkill /T /F`, which walks the child tree from the parent PID. POSIX
  behaviour is unchanged. (ARB-006)
- Fixed the drift rule that reports a file named in prose as missing. It
  normalized the path with `lstrip("./")`, which strips a character set rather
  than a prefix, so `` `.ai/context-brief.md` `` collapsed to
  `ai/context-brief.md` and matched nothing on disk — every dotted path in the
  repository read as missing. It now uses `_normalize_relative()`, the helper
  the sibling link rule already used, and skips paths that escape the
  repository root. Measured by scanning Arbiter with itself before and after,
  the rule goes from 187 findings to 28. (ARB-008)
- Made Arbiter write and read its own artifacts as UTF-8 rather than the
  platform default. Thirteen `write_text()` calls omitted `encoding=`, so on
  Windows the HTML, markdown, PR-comment and review-queue renderings were
  written in the console codepage; a generated `review-queue.md` here was
  undecodable as UTF-8. Reports are meant to travel into accreditation packages
  and pull requests, so they cross machines. Files belonging to the scanned
  repository are untouched — those are decoded with `errors="replace"` by
  design. (ARB-009)
- Put the licensing position in writing as far as it can go without counsel.
  `LICENSE` now exists, asserting copyright to Nicholas J Pauken, reserving all
  rights, assigning ownership of scan output to the user, and disclaiming any
  grant over the adapted third-party analyzers. It says plainly that it is an
  interim notice and not the operative grant. `pyproject.toml` references it by
  file rather than declaring the bare string `Proprietary`, and names the
  author. `CONTRIBUTING.md` states that external contributions are not
  accepted, which L-8 requires be settled before a patch is taken rather than
  after. This closes L-2 and L-8, and L-1 provisionally; ARB-005 stays open
  because L-3 follows from decisions only the owner can make and the L-6
  redistribution review is untouched. (ARB-005)
- Stopped a scan from reading its own output. `--out` defaults to
  `arbiter-out`, a relative path inside the tree being scanned, and the walk
  knew nothing about it, so every run after the first reported on the previous
  run's rendering. Measured here: 28 of 101 unsuppressed findings were located
  inside `arbiter-out` — 27.7% — and 24 of those were
  `assurance.blanket-suppression`, the rule that was about to be adjudicated,
  inflated by a report that is naturally full of the term it searches for.
  `run_scan` now takes `out_dir` and `walk_repo` skips it. Two consecutive
  scans into the default path produce identical findings, none of them inside
  the output directory. (ARB-011)
- Read files belonging to the scanned repository as UTF-8. Nine call sites used
  `read_text(errors="replace")` with no encoding, so the codec was the platform
  default — cp1252 on Windows. `errors=` governs what happens on failure, and
  cp1252 decodes almost every byte without failing, so it never errored, it
  silently produced wrong characters: an em dash in a scanned file reached a
  generated review queue as `â€”`. ARB-009 fixed Arbiter's own artifacts and
  deliberately excluded these sites, reasoning that `errors="replace"` was the
  design intent. That reasoning was wrong — the intent is never crashing on a
  target file, which `encoding="utf-8"` preserves. (ARB-012)
- Checked documented paths against disk rather than only against the walked
  inventory. Directories in `SKIP_DIRS` never enter the inventory, so tracked
  files under `.arbiter` read as missing: 8 of 31 doc-drift findings here.
  Whether a documented file exists is a question about disk, not about what the
  probes were shown. The sibling broken-link rule had the identical defect
  fifteen lines away and got the same fix. `.arbiter/baseline.json` still
  reports, correctly — it is documented but genuinely absent. The other 22
  findings are untouched, and `omni doctor` independently confirms them.
  (ARB-013)
- Gave doc-drift findings a repository in their `Location`. `Location.short()`
  builds its `repo:path` prefix from the Location rather than the Finding, and
  `doc_drift` set the id on the Finding only, so all 330 drift findings in a
  36-repository corpus scan rendered as bare paths. Two of those repositories
  each contain a python/ tree, so a line naming a README under it identified no
  repository a reader could open. (Those paths are deliberately not backticked:
  they belong to another repository, and backticking them here manufactures the
  very drift finding this entry is about.)
  (ARB-014)
- Made the review queue name the repository for every probe, not just for
  doc-drift. The same defect ran wider than one probe: 3,227 of 3,564 findings
  in that scan carried a blank `Location.repo_id` — `supply_chain` 0 of 1,817,
  `secrets` 0 of 377, `resource_policy` 7 of 1,040 — while `assurance` sets it
  at every construction site. The `Finding` carries the id in all 3,564 cases,
  so `review.where()` qualifies the path from there and both the markdown and
  HTML front ends use it, rather than adjudication waiting on some twenty
  construction sites across eight probes, several of them repo-level or
  cross-repo and needing judgement rather than a mechanical pass. Those sites
  are still wrong, so SARIF, HTML and console output remain unqualified.
  (ARB-015)
- Named the repository at every probe construction site, closing the residual
  risk ARB-015 recorded rather than fixed. Eighteen of the twenty sites took the
  id already in scope on the enclosing finding. Two did not, and they are why
  the renderer went first. The `interface` rule for unused IAM grants collected
  service names into a set and discarded where each grant was written, so it
  could cite only a synthetic `iam:` string and attributed the finding to the
  alphabetically first infrastructure repository — the wrong one whenever the
  grant was not in it; it now keeps the grant's location the way the
  neighbouring collections already did. The two `house_rules` path rules matched
  against every path in the scan flattened into one set, which left them the
  only sites in the tree setting no repository on the finding at all, and let
  one repository's LICENSE answer the rule for every repository; both now ask
  per repository. Re-measured on the same 36-repository corpus: 3,564 findings
  before and after, of which 3,227 were unattributed before and none after, and
  no finding changed repository. (ARB-016)
- Made the review sampler spread across repositories, not only across files.
  Within a rule it ordered findings so that distinct files came first, which one
  repository satisfies on its own: of six queues drawn from the 36-repository
  corpus, `resource.k8s-no-security-context` took 19 of 20 from a single
  repository and all 20 from teaching material, which the corpus tooling states
  is useless as a false-positive measure. Twenty findings from one repository
  are largely one author, one generator and one set of conventions, so they
  answer whether the rule is right about that repository while the ledger
  records the answer as though it were about the rule. Selection now
  round-robins across repositories and keeps file spread as the secondary axis
  inside each. Re-measured on the same corpus: average distinct repositories per
  queue 6.0 to 12.2, largest single-repository share 64.2% to 30.0%, and the
  k8s-no-security-context queue from one population to all three. Population is
  deliberately not an input — that label lives in the corpus tooling, and a
  library that ranks findings must not import the test harness — so repository
  spread is the proxy. Spread also cannot exceed the pool:
  `resource.unencrypted-database` still draws 18 of 20 from terragoat, because
  only three repositories in the corpus produce that finding at all. (ARB-017)

Six commits (`8775017`…`54f9a13`) landed from an offline bundle without
changelog entries. Recorded here after the fact, written from their diffs.
(ARB-007)

- Added incremental scanning. `arbiter scan|gate --changed REF` reads only the
  files that differ from a ref plus uncommitted work, and `--only-files` takes
  an explicit list; both run through the new `src/arbiter/incremental.py`.
  Every probe now declares a `scope`. Seven are file-scoped — `secrets`,
  `resource_policy`, `ast_metrics`, `supply_chain`, `house_rules`,
  `house_rules_ast` and `authored` — and everything else keeps the conservative
  `repo` default and is recorded as not-assessed, with the partial scan named
  as the reason, rather than being run against a subset it cannot answer from.
  Selection always retains dependency manifests, lockfiles, CI workflows and
  Terraform, because those are the files a probe reasons about but does not
  report on. (ARB-007)
- Made a partial scan unable to describe the repository. Reports carry
  `scan_scope`; a partial one contributes an abstention naming the unread
  files, which flows into the gate claim, the grade and every dimension. The
  overall grade is withheld outright, "probe ran and found nothing" narrows to
  "found nothing in the files it was given", and finding density is measured
  against lines read rather than repository size. Invariant **CI-11** — a
  partial scan may make no complete-scope claim about the repository — is
  machine-checked alongside the other ten, with the coverage measurement and a
  failing gate as the two reasoned exceptions. A ref that does not resolve
  refuses the scan instead of quietly reading nothing. (ARB-007)
- Added nine provider-issued token rules: Stripe, OpenAI, Anthropic, Google,
  GitLab, npm, SendGrid and PyPI at critical, Slack incoming webhooks at high.
  The issuer assigns these prefixes, so the value is its own evidence and the
  symbol name is irrelevant — which is why `STRIPE_KEY = "sk_live_…"` passed
  the name-based heuristic untouched. Six patterns terminate in an explicit
  lookahead rather than `\b`, because the charset is base64url and a token
  ending in `-` has no word boundary after it. The injection harness gained
  matching positive and control generators, the controls covering test-mode
  keys, wrong lengths, placeholders, interpolations and `sk-` used as a slug.
  (ARB-007)
- Shipped the pull-request gate as a worked example: `examples/pull-request-gate/`
  holds a two-job workflow and its config, `RUNNING-ON-YOUR-OWN-CODE.md` walks
  through a first scan, a baseline, adjudication and then the gate, and
  `docs/ci.md` gained "Scanning only what changed" and "A ready-made workflow".
  Only the nightly full-scan job may refresh the baseline; refreshing it from a
  partial scan would forgive every finding in the files that scan did not read.
  (ARB-007)
- Tagged findings a change did not cause. In a partial scan, findings landing
  in a context file the diff never touched are tagged `outside-this-change` and
  counted on the console. They stay in the report — the baseline, not deletion,
  is what keeps them out of the gate. (ARB-007)
- Added three provider-neutral TLS rules: `database-allows-plaintext-connections`,
  `weak-tls-version` and `no-https-redirect`. Resource rules also gained
  `exclude_native` beside `exclude_providers`, because a control can be
  expressible on one resource of a provider and absent on another — Azure SQL
  enforces TLS with no property saying so, and a provider-wide exclusion is too
  coarse to express that. `aws_elb`/`aws_alb`, Azure app services and GCP HTTP
  proxies and App Engine versions are normalized into the resource graph so the
  new rules have something to match. (ARB-007)
- Fixed a read cache that could serve one file's bytes for another. `_READ_CACHE`
  keyed on path alone and depended on every caller clearing it between scans;
  the injection harness invokes probes directly and writes every generated case
  to the same path, so it was served stale text. The key is now
  `(path, st_mtime_ns, st_size)` with a bounded size, so the cache cannot be
  wrong regardless of who calls it. (ARB-007)
- Taught `tools/corpus.py` to weight what it counts. Per-repository output now
  carries severity-and-confidence weighted totals, weight by language and lines
  by language, and the held-out comparison is made per KLOC of the language the
  rules actually fire on rather than per repository line counted flat. (ARB-007)
- Restored the shebang and executable bits on the four `tools/*.sh` scripts; an
  earlier commit had indented `#!/usr/bin/env bash` by two spaces, which stops
  the kernel recognising the file as a script whatever the mode bit says.
  Added `HANDOFF.md`, a cold-read briefing for a session arriving without the
  history. (ARB-007)

- Adopted the OmniEngineering workspace: `.ai/` source-of-truth scaffold
  (rules, schemas, playbooks, checklists, SWEBOK knowledge pack), the
  repo-local `./omni` CLI (`make_ai.py`), a `CLAUDE.md` routing shim, and a
  requirements registry. (ARB-001)
- Installed Headroom for Claude Code at local scope: `.claude/settings.local.json`
  routes traffic through the Headroom proxy (`127.0.0.1:8787`) and registers
  its session hooks. The file is machine-specific and git-ignored. (ARB-001)
