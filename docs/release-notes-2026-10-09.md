# Release notes, 2026-10-09: the Arbiter loop

What changed in OmniEngineering and in Arbiter on this date (two rounds; the
second is at the end), what it means for an adopter, and the steps to take. The mechanics are explained in
[`arbiter-integration.md`](arbiter-integration.md); the per-requirement detail
is in each repository's `CHANGELOG.md`.

## OmniEngineering (this repository)

| Requirement | Change |
|---|---|
| REQ-031 | `withdrawn` requirement status and `omni requirement archive`; doctor starts every server in `.mcp.json` for real |
| REQ-032 | the `command` gate validation type; `defect_category_pattern` configuration |
| REQ-033 | `omni adopt --with-arbiter` and `omni arbiter install` |
| REQ-034 | vendored workspaces recognised by the gate, doctor and CI |
| REQ-035 | Arbiter vendored under `arbiter/` as a git subtree with its full history; `arbiter.yaml` at the root with `arbiter/**` suppressed; the `arbiter` MCP server beside `omni`; `arbiter/` kept out of the root code graph; `NOTICE` and `LICENSES/README.md` state that the Apache-2.0 grant stops at `arbiter/` |
| REQ-036 | `omni doctor` `Posture:` line and `--json`; duplicate requirement ids across registries are an error |
| REQ-037 | `omni requirement complete` refuses on a missing, stale or red Arbiter report; `--no-arbiter-check REASON` |
| REQ-038 | a committed Arbiter baseline at install; `omni arbiter baseline [--refresh]`; doctor baseline checks |
| REQ-039 | `omni graph impact`; the gate's impact line; the `graph_impact` MCP tool |
| REQ-040 | `omni test run [--impacted]`; the recommended `completion.tests` example rule |
| REQ-041 | the adopt loop proven in CI and in a test; SARIF upload; Arbiter's Python floor read from its `pyproject.toml` |
| REQ-042 | Arbiter version recorded in `.ai/omni-version.json`; `omni arbiter update`; drift warnings |
| REQ-043 | Arbiter findings as graph nodes; `omni graph findings`; the viewer's Findings tab, colour modes, filters, source row and one-click traces; the `graph_findings` MCP tool |
| REQ-044 | requirement id aliases and `omni requirement renumber --prefix` |
| REQ-045 | the integration handbook `docs/arbiter-integration.md` and these release notes, linked from the README and the context brief |

Defects found and fixed on the way: FAIL-010 (CI YAML), FAIL-011 (schema enum),
FAIL-012 (committed Arbiter reports scanned as source), FAIL-013 (suite timeout
did not kill the process tree on Windows), FAIL-014 (a path assertion on
Windows), FAIL-015 (`omni adopt --include-cli` into an empty target exited 1).

## Arbiter

| Requirement | Change |
|---|---|
| ARB-031 to ARB-037 | re-synced to this workspace, self-gating in `pr-check`, probe precision, analyzers in CI, `review --ledger`, the Claude skill, the commercial control packs |
| ARB-038 | the `governance` probe (reads the failure ledger) and `req:` tags; reports group findings by requirement |
| ARB-039 | `--format annotations`, `--github` (annotations plus the PR comment in the job summary), SARIF upload in `pr-check`, `failing_ids` on the gate |
| ARB-040 | runs on Python 3.10 (`tomllib` with a `tomli` fallback) |
| ARB-041 | the opt-in per-file result cache with integrity invariant CI-13 and `--verify-cache` |
| ARB-042 | `history.jsonl` per run and `arbiter dashboard` |
| ARB-043 | the workspace tooling synced from this repository (posture line, baseline, impact, findings, `omni test run`, the version record, the gate rule's full run text) |
| ARB-044 | requirements renumbered from `REQ-###` to `ARB-###` with `id_aliases`, so the two registries never collide in one tree |

## What the repository now contains

Everything the two lists above put into the tree, so a reviewer can see the
whole of it in one place. Paths are relative to the repository root; the
`arbiter/` subtree is listed once and its contents are described by its own
`README.md`, `CHANGELOG.md` and `docs/`.

| Path | What it is | Landed by |
|---|---|---|
| `arbiter/` | the Arbiter repository evaluator, vendored as a git subtree with its full history: its source, tests, fixtures, CI, docs, its own `.ai/` workspace (registry on the `ARB` prefix), `LICENSE` (all rights reserved) and `NOTICE.md`. Not covered by the root Apache-2.0 grant. | REQ-035 |
| `arbiter.yaml` | this repository's Arbiter policy: profile offline, fail on critical, fail on new high; `arbiter/**` suppressed because the subtree gates itself | REQ-035 |
| `.arbiter/baseline.json` | the committed baseline: every finding id present when it was cut, the commit and the sha256 of `arbiter.yaml`; "new" means new since it | REQ-038 |
| `.mcp.json` | the `arbiter` (`arbiter mcp`) and `omni` (`omni mcp serve`) servers; `omni doctor` starts both for real | REQ-031, REQ-035 |
| `.ai/rules/completion-workflow.json` | the required rule `completion.arbiter_gate` (type `command`, runs `arbiter gate` on the change set with the baseline) and the recommended example rule `completion.tests` | REQ-032, REQ-033, REQ-038, REQ-040 |
| `.github/workflows/ci.yml` | the matrix job installs a vendored Arbiter and uploads the gate's SARIF; new jobs `vendored-arbiter` (the subtree's suite, claim-integrity and mutation checks) and `adopt-loop` (adopt, plant a defect, gate fails, fix, gate passes) | REQ-034, REQ-041 |
| `.gitignore` | `arbiter-out/` (reports) and `.arbiter/cache.json` (the per-file cache), so neither is ever scanned as source or committed | REQ-033, REQ-038 |
| `NOTICE`, `LICENSES/README.md` | the statement that the Apache-2.0 licence of this repository does not extend to `arbiter/` | REQ-035 |
| `docs/arbiter-integration.md`, `docs/release-notes-2026-10-09.md` | the handbook and these notes; `docs/` is an allowed root path | REQ-045 |
| `make_ai.py`, `omni_graph.py`, `omni_mcp.py`, `.ai/graph-viewer/viewer.html` | the CLI, graph, MCP and viewer code behind every command named above | REQ-031 to REQ-044 |
| `tests/` | thirteen test files, new or extended (adopt loop, Arbiter install and update, doctor MCP probe and posture, gate rules, graph findings and impact, requirement aliases, archive and completion, `omni test run`, the viewer) | REQ-031 to REQ-044 |
| `.ai/requirements/requirements.json`, `.ai/failures/failure-ledger.json`, `CHANGELOG.md`, `.ai/project-map.md`, `.ai/context-brief.md` | the fifteen requirements (all completed), the six defects (FAIL-010 to FAIL-015, all fixed), one changelog entry per requirement, the project map and the brief's pointer to the handbook | REQ-031 to REQ-045 |

The commands that did not exist before this date: `omni adopt
--with-arbiter`, `omni arbiter install | baseline | update`, `omni doctor
--json`, `omni requirement archive | renumber`, `omni requirement complete
--no-arbiter-check`, `omni graph impact`, `omni graph findings`, `omni test
run`; inside the subtree, `arbiter gate --format annotations | --github`,
`arbiter scan --no-cache | --verify-cache`, `arbiter dashboard`.

## Round four, later the same day

A second set of requirements landed on both sides before the day was out. The
themes were speed, signal, completeness and two bigger bets, plus three
mitigations for the limits the first round left in finding traceability.

### OmniEngineering

| Requirement | Change |
|---|---|
| REQ-046 | the `Posture:` line reads the newest full scan and the newest gate report separately (`arbiter score 86.5 (coverage 99%, full scan …) · gate passed (new high+ 0, fresh)`); `omni doctor --json` is schema version 2 with `posture.arbiter.full` and `.gate` |
| REQ-047 | `completion.tests` is required: `omni gate` runs `omni test run --impacted`; a command rule that passed is memoised on its scoped change set and not re-run until a scoped file changes (`omni gate --no-memo` forces); durations are printed |
| REQ-048 | `tests/test_viewer_browser.py` drives the built viewer in headless Chromium (Findings tab, search, source row, breadcrumb, traces, colour mode, no console errors); the `viewer-browser` CI job runs it |
| REQ-049 | `omni arbiter sync` levels the subtree, the installed package, the recorded version and the baseline in one command; doctor warns on a commit whose author or committer email is not the configured identity |
| REQ-050 | doctor's live MCP probe is memoised for a day in the git directory; `omni doctor --probe` forces it; a warm doctor run takes 0.35 s instead of 2.2 s |
| REQ-051 | a finding traces to the requirement that introduced its line: `git blame` at the scanned commit gives an `introduced_by` edge to the commit and, through the history layer, to the one requirement; `omni graph why` prints it and the viewer gains an "Introduced by" row and trace |
| REQ-052 | vendored workspaces' ledgers and registries join the graph, namespaced (`arbiter/FAIL-042`), so a finding traces to the entry that fixed it wherever it lives; a ledger entry names the finding id in `how_detected` |

### Arbiter (ARB-045 to ARB-052)

| Requirement | Change |
|---|---|
| ARB-045 | tool versions are memoised: adapter registration drops from 24.5 s to 0.3 s on every `arbiter` invocation, the gate included |
| ARB-046 | the suite runs in a fast and a slow tier (`tests/test_arbiter.py` split into fourteen files, same collected ids); pr-check runs the fast tier on every cell and the slow tier in a parallel job |
| ARB-047 | adapters run concurrently and replay from the result cache when nothing they read changed (all-or-nothing); `--verify-cache` re-runs one; integrity invariant CI-14 |
| ARB-048 | the doc-drift probe leaves generated output, git-ignored paths and cross-repository references alone |
| ARB-049 | the debt sweep: the self-scan went from 482 active findings (score 71) to 320 (score 81), the baseline from 411 ids to 320; the review marks are a committed draft for a person to apply |
| ARB-050 | `arbiter_review_draft`: an assistant proposes marks with reasons and never records one |
| ARB-051 | `arbiter bundle build | verify`, the air-gapped bundle without the analyzers; `--limiter file` shares the hosted API's limits across processes |
| ARB-052 | a finding in a gate is tagged with the requirements cited by the commits that touched its file, not every open one (`req-scope:commits` / `req-scope:open`) |

Defects found and fixed on the way: FAIL-042 (the stub detector matched a
`pass` nested under `except`; found by code scanning on this repository's
pull request #5), FAIL-043 (a Windows path assertion), FAIL-044
(`use_adapters=False` only skipped registration, so the fast tier spent 28
minutes in semgrep; now under two), FAIL-045 (the HTML report's "Read first"
block was built and never rendered), FAIL-047, FAIL-048 and FAIL-049 (two
probe false positives found by the sweep's own review). Open: FAIL-046, the
workflows' actions are pinned by mutable major tag.

### For an adopter, this round

1. `./omni update --source <OmniEngineering checkout>` brings the tooling;
   `completion.tests` arrives as `required`, so register your suites with
   `omni test detect --write` or set its severity to `recommended`.
2. `./omni arbiter sync` (subtree mode) or `./omni arbiter update` then
   `./omni arbiter baseline --refresh` (pip mode) levels Arbiter; commit
   `.arbiter/baseline.json`.
3. `./omni doctor --probe` once, so the MCP memo starts from a live probe.
4. In CI, add the `viewer-browser` job if you ship the viewer, and the
   `slow-tier` job if you vendor Arbiter.

## For an adopter

1. `./omni update --source <OmniEngineering checkout>` brings the new tooling.
2. `./omni arbiter install --source <Arbiter checkout or URL>` (or `omni arbiter
   update` if already installed) rewrites the gate rule to pass the baseline and
   the three output formats, records the version, and cuts the baseline.
3. Commit `.arbiter/baseline.json`.
4. `./omni gate`, then `./omni graph build`; `./omni doctor` now ends with the
   posture line.
5. In CI, add the SARIF upload step and `permissions: security-events: write`
   (see `.github/workflows/ci.yml`).
