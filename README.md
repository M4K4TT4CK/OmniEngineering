# OmniEngineering Workspace

![omni repository banner](assets/brand/omni-banner.svg)

[![Python](https://img.shields.io/badge/Python-3.10%2B-2F6DB3?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Apache--2.0-3C7D5A)](LICENSE)
[![Codex](https://img.shields.io/badge/Codex-AGENTS.md-111827)](AGENTS.md)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-CLAUDE.md-D97706)](CLAUDE.md)
[![Cursor](https://img.shields.io/badge/Cursor-.cursorrules-2563EB)](.cursorrules)
[![Copilot](https://img.shields.io/badge/GitHub%20Copilot-instructions-24292F?logo=github&logoColor=white)](.github/copilot-instructions.md)
[![Kiro](https://img.shields.io/badge/Kiro-steering-6D28D9)](.kiro/steering/omnicontext.md)
[![No MCP Required](https://img.shields.io/badge/MCP-not%20required-0F766E)](.ai/)

| Runtime | Assistants | Model Routes | Governance |
| --- | --- | --- | --- |
| Python 3.10+ | Codex, Claude Code, Cursor, GitHub Copilot, Kiro | Local models, DeepSeek workflows, OpenRouter-style gateways | Requirements, rulepacks, playbooks, checklists, SWEBOK knowledge |

OmniEngineering is a repo-local software engineering workspace for human and AI
engineering teams. Its goal is bigger than prompt sharing: it gives a project a
durable way to carry requirements, engineering rules, delivery workflows,
quality gates, design knowledge, and assistant context together.

OmniContext is the context-governance layer inside this workspace. Its job is to
prevent AI context drift when a team uses more than one assistant or model in
the same repository. Codex, Claude Code, Cursor, GitHub Copilot, Kiro-style
tools, local models, DeepSeek-based workflows, model routers, and future tools
can all read different entry files or prompts, but OmniContext routes them to
one shared `.ai/` source of truth.

The result is one practical engineering operating model: project rules,
architecture guidelines, security boundaries, requirement IDs, validation
expectations, SWEBOK-aligned knowledge, playbooks, checklists, handoff rules,
and completion workflows across every assistant.

![OmniEngineering architecture](assets/omni-context.svg)

## Quick Start

In short: a `.ai/` folder of rules, playbooks, and checklists that every AI
coding assistant in your repo reads from instead of its own separate config
file, plus a small dependency-free CLI (`omni`) to keep it healthy.

**1. Get a checkout of this repo** (you'll adopt *from* it, so keep it around):

```bash
git clone <this-repo-url> ../OmniEngineering
```

**2. Add it to your project** (run from inside the `OmniEngineering` checkout):

```bash
cd ../OmniEngineering
./omni adopt --target ../your-project --dry-run       # preview first
./omni adopt --target ../your-project --include-cli    # then actually copy
```

**3. Check it's healthy** (run from inside your project):

```bash
cd ../your-project
./omni doctor
```

**Later, pull in template improvements** (new rulepacks, new `doctor` checks,
etc.) without losing anything you've customized:

```bash
./omni update --source ../OmniEngineering --dry-run   # preview
./omni update --source ../OmniEngineering              # apply
```

`omni update` never touches your `requirements.json`, `project-map.md`,
`project-configuration.md`, or `CHANGELOG.md` -- see
[Updating an adopted workspace](#updating-an-adopted-workspace) for exactly
how it decides what's safe to change, and what to do if your project adopted
OmniEngineering before this command existed.

That's the whole loop: adopt once, `doctor` to check health, `update` to stay
current. Everything below explains *why* it's built this way and covers less
common setups (bare `.ai/` copy, symlinks, CI wiring, multi-tool projects).

## Arbiter: the evaluator this workspace runs with

OmniEngineering governs a change (requirement, changelog, ledger, rules).
[Arbiter](https://github.com/paukennick/arbiter) evaluates the code (probes,
analyzers, a scorecard that refuses to grade what it did not inspect). Since
2026-10-09 the two run as one loop:

- **`omni gate` runs `arbiter gate`.** A required rule, `completion.arbiter_gate`,
  runs Arbiter on the change set with the committed baseline, so a commit is
  blocked by a new high finding exactly as it is blocked by a missing changelog
  entry. The pre-commit hook, the Claude Stop hook and CI all go through it.
- **Arbiter reads the workspace.** Its `governance` probe reads the failure
  ledger (a changed file named by an open failure, without its regression test,
  is a finding) and tags every finding with the requirement the commits cite,
  so reports group by `REQ-###`.
- **Doctor and completion see the result.** `omni doctor` ends with a
  `Posture:` line (Arbiter score, coverage, new highs, gate result, fresh or
  stale; open failures; open requirements), and `omni requirement complete`
  refuses while the branch's Arbiter report is missing, stale or red.
- **Findings become ledger entries and graph nodes.** `arbiter review --ledger`
  turns adjudicated findings into `FAIL-###` entries; `omni graph build` adds
  every finding as a node linked to its file, symbol, requirement and failure;
  `omni graph findings`, `omni graph why f:<id>` and the viewer's Findings tab
  trace one by id, category, severity, depth and directory, down to `path:line`.
- **Pull requests see it too.** Inline annotations, the PR comment in the job
  summary and SARIF in code scanning, with the gate's exit code as the verdict.

Three ways to have it: installed beside an adopted workspace
(`./omni adopt --with-arbiter` or `./omni arbiter install`), vendored as a
subtree (this repository's `main` keeps the whole Arbiter checkout
under `arbiter/` with its own workspace, recognised by the gate, doctor and
CI), or Arbiter governing itself with this workspace at its own root. Keep the
two level with `./omni arbiter sync` (subtree pull or pip upgrade, reinstall,
version record, baseline refresh, doctor, stopping at the first failing step)
and `./omni update` for the workspace template; `./omni arbiter baseline
--refresh` alone re-cuts the baseline on a green gate.

What that adds to this repository, in one list (the per-path table is in the
release notes):

- `arbiter/`: the whole Arbiter checkout as a git subtree with its full
  history, its own `.ai/` workspace on the `ARB-###` prefix, its own
  `LICENSE` (all rights reserved, not Apache-2.0) and `NOTICE.md`.
- `arbiter.yaml`, `.arbiter/baseline.json`, the `arbiter` server in
  `.mcp.json`, the required rule `completion.arbiter_gate` and the recommended
  example `completion.tests` in the completion rulepack, `arbiter-out/` and
  `.arbiter/cache.json` in `.gitignore`.
- CI: the matrix job installs the vendored Arbiter and uploads the gate's
  SARIF; two new jobs run the subtree's own suite, claim-integrity and mutation
  checks, and prove the adopt loop end to end.
- Commands: `omni adopt --with-arbiter`, `omni arbiter install | baseline |
  update`, `omni doctor --json`, `omni requirement archive | renumber`,
  `omni graph impact | findings`, `omni test run`.
- Records: requirements REQ-031 to REQ-045 (and ARB-038 to ARB-044 inside the
  subtree), defects FAIL-010 to FAIL-015, one `CHANGELOG.md` entry per
  requirement, and the statement in `NOTICE` and `LICENSES/README.md` that
  the Apache-2.0 grant stops at `arbiter/`.

The full mechanics, the CI jobs and what each proves, the off switches and a
finding-tracing walkthrough are in
[docs/arbiter-integration.md](docs/arbiter-integration.md); the dated list of
every change on both sides, and the table of every path it added, is in
[docs/release-notes-2026-10-09.md](docs/release-notes-2026-10-09.md).

## Design Documents

The project design source lives in `design/`. It includes product design,
system architecture, data model, assistant workflow, CLI behavior, validation
and operations, decision records, and supporting diagrams.

Start with:

- [Arbiter and OmniEngineering: how the two run as one loop](docs/arbiter-integration.md)
  -- the integration handbook: the wiring, the edit-to-adjudication loop, tracing a
  finding through the graph, CI, keeping both level, the off switches
- [Release notes, 2026-10-09](docs/release-notes-2026-10-09.md)
- [Design overview](design/README.md)
- [System architecture](design/system-architecture.md)
- [Context routing diagram](design/diagrams/context-routing.svg)
- [Assistant lifecycle diagram](design/diagrams/assistant-lifecycle.svg)
- [Data model diagram](design/diagrams/data-model.svg)
- [Code graph](design/diagrams/omni-code-graph.svg) -- a real force-directed
  node-link graph of every symbol and resolved call/containment edge in
  this repo, laid out from `omni graph build`'s own output against itself,
  not hand-drawn

## The Problem

Modern software teams rarely use only one AI assistant.

Codex may read `AGENTS.md`, Cursor may read `.cursorrules`, Claude Code may
read `CLAUDE.md`, and GitHub Copilot may read
`.github/copilot-instructions.md`. If each file contains a different version of
the project instructions, assistant behavior drifts.

Local models, hosted chat models, model-router gateways, and model APIs may not
read any repository entrypoint automatically. For those tools, OmniContext
provides `LLM_CONTEXT.md`, `.ai/context-manifest.json`, and copy-paste adapter
prompts in `.ai/adapters/`.

OmniContext solves that drift by making the root assistant files lightweight
routing shims. They point to `.ai/entrypoints/`, which points back to the
shared `.ai/` engineering workspace.

The unique case this solves is not prompt storage. It is governance for
AI-assisted development across multiple tools:

- One assistant should not skip requirement IDs while another uses them.
- One assistant should not update code without the changelog rule another tool
  follows.
- One assistant should not ignore security boundaries because it reads a
  different config file.
- One assistant should not claim completion without the validation workflow the
  rest of the team expects.

OmniEngineering gives the repository a shared engineering operating model
without locking the team into one vendor or editor.

## The Pattern

```text
[project-root]/
├── .ai/
│   ├── core-context.md
│   ├── project-configuration.md
│   ├── .ignore
│   ├── context-manifest.json
│   ├── project-map.md
│   ├── entrypoints/
│   │   ├── universal.md
│   │   ├── codex.md
│   │   ├── claude.md
│   │   ├── cursor.md
│   │   ├── copilot.md
│   │   ├── kiro.md
│   │   └── fallback-contract.md
│   ├── adapters/
│   │   ├── generic-llm.md
│   │   ├── local-model.md
│   │   ├── model-router.md
│   │   ├── openrouter.md
│   │   ├── deepseek.md
│   │   └── kiro.md
│   ├── playbooks/
│   │   ├── planning.md
│   │   ├── implementation.md
│   │   ├── review.md
│   │   ├── testing.md
│   │   ├── debugging.md
│   │   ├── refactoring.md
│   │   ├── migration.md
│   │   ├── release.md
│   │   └── handoff.md
│   ├── checklists/
│   │   ├── pre-implementation.md
│   │   ├── pre-completion.md
│   │   ├── public-release.md
│   │   └── security.md
│   ├── knowledge/
│   │   └── swebok/
│   │       ├── software-requirements.md
│   │       ├── software-design.md
│   │       ├── software-construction.md
│   │       ├── software-testing.md
│   │       ├── software-maintenance.md
│   │       ├── software-configuration-management.md
│   │       ├── software-engineering-management.md
│   │       ├── software-engineering-process.md
│   │       ├── software-engineering-models-and-methods.md
│   │       ├── software-quality.md
│   │       ├── software-engineering-professional-practice.md
│   │       ├── software-engineering-economics.md
│   │       ├── computing-foundations.md
│   │       ├── mathematical-foundations.md
│   │       ├── engineering-foundations.md
│   │       ├── requirements-quality-checklist.md
│   │       ├── design-quality-checklist.md
│   │       ├── construction-quality-checklist.md
│   │       ├── testing-quality-checklist.md
│   │       ├── maintenance-impact-checklist.md
│   │       ├── scm-checklist.md
│   │       ├── engineering-management-checklist.md
│   │       ├── process-tailoring-checklist.md
│   │       ├── model-method-selection-checklist.md
│   │       ├── quality-attribute-checklist.md
│   │       ├── professional-practice-checklist.md
│   │       ├── economics-decision-checklist.md
│   │       ├── computing-foundations-checklist.md
│   │       ├── mathematical-reasoning-checklist.md
│   │       ├── engineering-foundations-checklist.md
│   │       ├── srs-template.md
│   │       ├── design-brief-template.md
│   │       ├── test-plan-template.md
│   │       ├── maintenance-plan-template.md
│   │       ├── engineering-plan-template.md
│   │       ├── process-improvement-template.md
│   │       ├── quality-plan-template.md
│   │       └── tradeoff-analysis-template.md
│   ├── requirements/
│   │   └── requirements.json
│   ├── rules/
│   │   ├── universal-engineering-ruleset.json
│   │   ├── controlled-implementation.json
│   │   ├── completion-workflow.json
│   │   ├── data-governance.json
│   │   ├── fallback-llm-rules.json
│   │   ├── hci-ui-rules.json
│   │   └── oop-design.json
│   └── schemas/
│       ├── rulepack.schema.json
│       ├── requirements.schema.json
│       └── universal-engineering-ruleset.schema.json
│
├── LLM_CONTEXT.md
├── AGENTS.md
├── CLAUDE.md
├── .cursorrules
├── .kiro/
│   └── steering/
│       └── omnicontext.md
├── .github/
│   └── copilot-instructions.md
├── CHANGELOG.md
├── omni
├── pyproject.toml
└── make_ai.py
```

The `.ai/` directory is the master context directory. Root files are thin
compatibility shims for tools that require fixed filenames; the full
tool-specific instructions live in `.ai/entrypoints/`.

| Tool | Required shim | Full source |
| --- | --- | --- |
| Universal LLMs | `LLM_CONTEXT.md` | `.ai/entrypoints/universal.md` |
| Codex | `AGENTS.md` | `.ai/entrypoints/codex.md` |
| Claude Code | `CLAUDE.md` | `.ai/entrypoints/claude.md` |
| Cursor | `.cursorrules` | `.ai/entrypoints/cursor.md` |
| GitHub Copilot | `.github/copilot-instructions.md` | `.ai/entrypoints/copilot.md` |
| Kiro-style workflows | `.kiro/steering/omnicontext.md` | `.ai/entrypoints/kiro.md` |
| DeepSeek or local model wrappers | none | `.ai/adapters/deepseek.md`, `.ai/adapters/local-model.md` |
| OpenRouter or model gateways | none | `.ai/adapters/openrouter.md`, `.ai/adapters/model-router.md` |

## What This Workspace Enforces

This implementation goes beyond prompt sharing and context drift prevention. It
includes a strict file-based engineering workflow that can be reused across
projects without an MCP server:

- Targeted file access before any implementation work.
- Requirement IDs for every task and code change.
- Planning, implementation, review, testing, debugging, refactoring, migration,
  release, and handoff playbooks.
- Pre-implementation, pre-completion, public release, and security checklists.
- SWEBOK-aligned software requirements, design, construction, testing,
  maintenance, configuration management, engineering management, process,
  models and methods, quality, professional practice, economics, computing
  foundations, mathematical foundations, and engineering foundations guidance
  with quality checklists and lightweight templates.
- Small, isolated changes instead of broad rewrites.
- Modular, maintainable design standards.
- Security guardrails for secrets and sensitive files.
- Documentation and changelog updates as completion gates.
- Test, lint, typecheck, and build validation before claiming completion.
- Required final reporting with commit and pull request information.
- A requirement registry for tracking `REQ-###` work across assistants.
- A dependency-free doctor command for detecting workspace drift.
- A centralized fallback contract in `.ai/entrypoints/fallback-contract.md` in
  case an LLM skips the primary `.ai/` configuration.

The controlling global ruleset is:

```text
.ai/rules/universal-engineering-ruleset.json
```

Enforceable companion rules live beside it as structured JSON rulepacks in
`.ai/rules/`. The JSON format gives each rule a stable ID, severity, scope, and
validation hints so tools can inspect more than file existence.

## Context And Model Discipline

OmniEngineering is designed to reduce token waste and context drift.

- **Load the brief first:** `.ai/context-brief.md` is the low-token starting
  layer. It tells assistants which small profile to load for the current task
  instead of reading every rule, playbook, checklist, and knowledge pack.
- **Use context profiles:** `.ai/context-manifest.json` defines minimum,
  implementation, review, and deep-policy loading profiles. Start with the
  smallest profile and escalate only when the task needs it.
- **Control the context window:** Keep `.ai/.ignore`, `.cursorignore`,
  `.gitignore`, and equivalent tool ignore files aggressive. Exclude logs,
  compiled artifacts, local environment files, dependency folders, generated
  output, caches, and large binaries.
- **Use the generated project map:** Run `./omni map` after adopting the
  workspace or changing project structure. Assistants should read
  `.ai/project-map.md` before broad traversal, then inspect only the smallest
  relevant path set.
- **Attach context manually:** Prefer attaching or naming the exact files and
  folders needed for the current requirement instead of allowing automatic
  whole-workspace scans.
- **Reset stale history:** Start a new session or compact the current one when
  switching tasks so old conversation history does not consume tokens or steer
  unrelated work.
- **Prompt specifically:** Include the requirement ID, framework, language,
  relevant patterns, constraints, and desired outcome in the first prompt when
  known.
- **Ask for outlines first:** For broad or risky work, ask for a plan or
  pseudo-code outline before generating or applying a large patch.
- **Cascade models:** Use cheaper or lower-effort models for boilerplate,
  formatting, simple docs, and mechanical edits. Save flagship or high-effort
  models for architecture, complex debugging, migrations, security-sensitive
  work, and high-risk design decisions.

The goal is not to make every prompt larger. The default path should be:

```text
context brief -> project map -> active requirement -> one relevant playbook/checklist -> target files
```

Load full rulepacks, SWEBOK knowledge packs, templates, and design docs only
when the task specifically needs that depth.

## If You Clone a Repo That Already Uses This Workspace

You do not need to run `make_ai.py` just to benefit from the workspace.

If the repository already contains `.ai/`, `AGENTS.md`, `CLAUDE.md`,
`.cursorrules`, `.github/copilot-instructions.md`, `LLM_CONTEXT.md`, and
`.ai/context-manifest.json`, the workspace is already usable. Open the repo in
your assistant of choice and the shim files should route that assistant to the
matching source file in `.ai/entrypoints/`.

For a local model, DeepSeek chat/API wrapper, OpenRouter-style model gateway, or
any tool that does not read repository files automatically, paste the relevant
prompt from `.ai/adapters/` into the model's system, developer, or project
instruction field.

For model routers, include the adapter prompt in each new routed session. The
router can switch model slugs or providers, so context should be treated as part
of the request payload.

Use `make_ai.py` or `./omni` when you want to verify, sync, or adapt the
engineering workspace inside that repository:

- Run `./omni doctor` to check whether the workspace is healthy.
- Run `./omni sync` after editing entrypoint routing, fallback behavior, or
  assistant source content.
- Run `./omni validate` as an alias for `doctor`.

In other words: `.ai/` is the delivery workspace. `make_ai.py` and `omni` are
maintenance tools for checking and synchronizing it.

## Add The Workspace To A Project

Do not recreate the directory tree by hand. Bring the workspace files into the
target repository, then configure them for that project.

Use one of these adoption paths:

| Path | Best For | What To Do |
| --- | --- | --- |
| Template copy | New repositories | Create the project from an OmniEngineering template or copy this repository, then replace project-specific placeholders. |
| Drop-in copy | Existing repositories | Copy `.ai/` and only the needed tool shims into the target repo root, review conflicts, then run `./omni doctor`. |
| Vendored source | Teams that want upstream updates | Add OmniEngineering as a tracked subtree, submodule, or vendor directory, then copy or safely sync the root shims into the project. |
| Internal baseline | Organizations | Keep an approved internal fork and periodically merge upstream OmniEngineering improvements. |

For a normal existing repository, copy `.ai/` into the target repo root first:

```text
.ai/
```

Then add only the shims for tools the team actually uses:

```text
LLM_CONTEXT.md
AGENTS.md
CLAUDE.md
.cursorrules
.cursorignore
.github/copilot-instructions.md
.kiro/steering/omnicontext.md
```

Add the maintenance CLI if the project wants local validation and map commands:

```text
omni
make_ai.py
```

Do not overwrite an existing `pyproject.toml`. The installable `omni` console
script is optional; the repo-local `./omni` command is enough for validation.

If the target already has `AGENTS.md`, `CLAUDE.md`, `.cursorrules`,
`.cursorignore`, `.github/copilot-instructions.md`, `.kiro/`, `omni`, or
`make_ai.py`, merge manually instead of replacing the file. `./omni sync` will
skip existing non-Omni files by default. Use `./omni sync --force` only when
replacement is intentional.

You can generate a safe adoption plan before copying:

```bash
./omni adopt --target ../target-project --dry-run
./omni adopt --target ../target-project --tools codex,cursor,universal --include-cli
```

`adopt` copies `.ai/` and selected shims, skips existing target files by
default, and requires `--force` before replacing anything. A successful
(non-dry-run) adoption also writes `.ai/omni-version.json`, recording the
source path and git commit adopted from -- this is what `omni update` (below)
diffs against later.

### Arbiter alongside the workspace

[Arbiter](https://github.com/paukennick/arbiter) is the repository evaluator
this workspace pairs with: it scans the code and refuses to grade what it did
not inspect, and its gate is the product-side half of `omni gate`. The whole
loop is written up in [docs/arbiter-integration.md](docs/arbiter-integration.md).
Bring it in with the adoption, or add it to a repository adopted earlier:

```bash
./omni adopt --target ../your-project --include-cli --with-arbiter ../arbiter   # a checkout
./omni adopt --target ../your-project --include-cli --with-arbiter             # the GitHub URL
cd ../your-project && ./omni arbiter install --source ../arbiter               # already adopted
```

Four things land, each skipped when already present and never overwritten:
the `arbiter-eval[mcp]` package (editable from a checkout, or from the git
URL), an `arbiter` entry in `.mcp.json` so assistants reach `arbiter_scan`,
`arbiter_gate` and `arbiter_review_queue` as tools, a `completion.arbiter_gate`
rule of type `command` in the completion rulepack so `omni gate` runs
`arbiter gate --changed <base>` whenever source changes, and a starter
`arbiter.yaml` (fail on critical, fail on new high), plus `arbiter-out/` in
`.gitignore`: Arbiter reads tracked files as source, so a committed report of
its own would be scanned and gated on the next run (FAIL-012). A workspace
wired by hand needs that ignore entry before its first run. `--skip-pip`
writes the wiring only; `--dry-run` shows it. `omni doctor` then starts the registered
server for real. Arbiter is a separate, proprietary product; this command
installs and wires it, it does not vendor it.

Installation also cuts the **baseline**: a full offline scan of the project
(`arbiter scan . --profile offline`), written by `arbiter baseline` to
`.arbiter/baseline.json` as the list of finding ids the project accepts as
known, stamped with the commit it was cut at and a sha256 of `arbiter.yaml`.
The gate rule runs `arbiter gate . --changed <base> --baseline
.arbiter/baseline.json --format json,sarif,pr-comment` (json is what omni
reads back, sarif feeds code-scanning upload, pr-comment is the markdown a CI
job posts), so a finding in the baseline is `existing` and only what is new
since it can fail the gate. Without the file the gate treats every finding as
new. Commit `.arbiter/baseline.json`; only `arbiter-out/` and
`.arbiter/cache.json` are ignored. When `arbiter` was not on PATH at install
time, or to re-cut the baseline later:

```bash
./omni arbiter baseline              # first cut; a no-op when the file exists
./omni arbiter baseline --refresh    # re-cut on a green gate; --force overrides
```

A refresh is only allowed once the newest gate report under
`arbiter-out/omni-gate/` passed, because it absorbs every open finding as
known. It prunes: the new file holds only ids the fresh full scan still
reports, so a baseline never carries fixed findings forward. A `--changed`
(partial) report is refused as a baseline. `omni doctor` warns when the gate
rule is wired but the baseline is missing, when the baseline predates the
newest `fixed` failure-ledger entry (it may list findings that no longer
exist), and when `arbiter.yaml` changed since it was cut (`config_hash`); all
three clear with `omni arbiter baseline --refresh`.

### The vendored Arbiter subtree

Since 2026-10-09 this repository's `main` vendors Arbiter's own source tree
under `arbiter/` as a git subtree with its full history, so the workspace and
the evaluator can be read, cloned and run from one place:

```bash
pip install -e "arbiter[mcp]"                                   # install from the subtree
git subtree pull --prefix=arbiter https://github.com/paukennick/arbiter main   # take later arbiter changes
```

Here the two run together: `omni gate` runs `arbiter gate . --changed`
over this repository (with `arbiter/**` suppressed, because the subtree is
gated by its own `arbiter.yaml`), `.mcp.json` registers both the `arbiter` and
`omni` servers, `omni doctor` checks that the subtree runs the same CLI files
as the root and names `cd arbiter && python omni update --source ..` when it
does not, the code graph leaves `arbiter/` to the subtree's own graph, and the
`vendored-arbiter` CI job runs Arbiter's suite whenever the subtree exists.
The subtree's commits cite Arbiter's requirement ids, which the gate reads
from `arbiter/.ai/requirements/` rather than as typos here.

Arbiter keeps its own `LICENSE` (all rights reserved) and `NOTICE.md` under
`arbiter/`; the Apache-2.0 licence of this repository does not extend to it,
and `NOTICE` and `LICENSES/README.md` at the root say so. A project adopting
this workspace never receives the subtree: `omni adopt --with-arbiter` installs
Arbiter from its own repository.

### Updating an adopted workspace

Once a project has adopted OmniEngineering and customized its rules,
playbooks, or checklists, pulling in later template improvements is a single
command rather than a manual re-copy:

```bash
./omni update --source ../OmniEngineering --dry-run
./omni update --source ../OmniEngineering
```

`update` 3-way-merges every template-managed file (rulepacks, playbooks,
checklists, SWEBOK knowledge, schemas, entrypoints, `make_ai.py`/`omni`)
using `git merge-file` against the ref recorded in `.ai/omni-version.json`:

- Untouched-by-you files that changed upstream are updated automatically.
- Files you customized that the template didn't touch are left alone.
- Files both sides changed are merged; a genuine conflict is left with
  `<<<<<<<`/`>>>>>>>` markers for you to resolve by hand, same as a git merge.
- `.ai/project-configuration.md`, `.ai/project-map.md`,
  `.ai/requirements/requirements.json`, and `CHANGELOG.md` are never touched
  -- those are yours, not the template's.

`--source` must point to a git checkout of OmniEngineering (not a plain
folder copy) since reconstructing the merge base requires its commit
history. Pass `--include-legal` or `--include-presentation` to also merge
those optional bundles.

**Already adopted OmniEngineering before `omni update` existed?** There's no
`.ai/omni-version.json` yet, so a plain `omni update` will refuse (it has
nothing to compare against). Fix that once, from the adopted project:

```bash
./omni update --source ../OmniEngineering --bootstrap
```

This merges nothing -- it just records today as the starting point. Every
`omni update` after that works normally.

**Keeping Arbiter level.** `omni arbiter install` records the installed Arbiter
version and its source under `arbiter` in `.ai/omni-version.json`, next to the
template ref (`omni update` keeps that key). `omni doctor` warns when the
installed `arbiter` no longer matches the record, and `omni update` warns when
your `completion.arbiter_gate` rule runs a different command than the current
template; the rule is yours, so update never rewrites it. One command brings
all of it level, and a narrower one does the package-and-rule part:

```bash
./omni arbiter sync --dry-run       # prints the steps and their commands, runs nothing
./omni arbiter sync                 # subtree pull (or pip --upgrade), pip install -e, the record, baseline, doctor
./omni arbiter update --dry-run     # prints every command, writes nothing
./omni arbiter update               # pip --upgrade, the rule, a vendored subtree, the record
```

`arbiter sync` runs the steps in order, prints each before it runs and stops
at the first non-zero exit naming the step and what to do (a subtree conflict:
resolve it in `arbiter/`, commit, rerun); `--source`, `--branch`,
`--skip-baseline` and `--dry-run` are its switches.

It upgrades the package from the recorded source (or `--source`, with
`--skip-pip` to leave pip alone), rewrites the rule's `validation` to the
template unless `--keep-rule`, and, when `arbiter/` is a vendored subtree (it
carries `pyproject.toml` and `.ai/omni-version.json`), runs
`git subtree pull --prefix arbiter <source> main` in the mode the subtree was
added with: it reads the newest commit carrying `git-subtree-dir: arbiter`,
treats a `git-subtree-mainline` trailer as unsquashed history and its absence
(a "Squashed 'arbiter/' ..." commit) as squashed, and refuses to mix the two
unless `--force` (`--squash` asks for a squashed pull). It re-records the
version last. Afterwards run `omni arbiter baseline --refresh` once the gate is
green, so the baseline matches what the upgraded Arbiter reports.

Optional presentation assets:

```text
assets/brand/
assets/omni-context.svg
design/
```

Keep the OmniEngineering license files when you distribute copied or modified
OmniEngineering workspace files:

```text
LICENSE
NOTICE
TRADEMARKS.md
CONTRIBUTING.md
LICENSES/
```

Then add or keep the target project's own license for its application code,
product code, docs, and data.

After copying, run:

```bash
./omni doctor
./omni map
```

If you edited fallback text or assistant entrypoints while adapting the
workspace, run:

```bash
./omni sync
./omni map
./omni doctor
```

`./omni sync` is collision-safe by default: it skips existing non-Omni files
instead of overwriting project-owned assistant configuration. Use
`./omni sync --force` only when replacement is intentional.

The root assistant files are already written as shims for Codex, Claude Code,
Cursor, GitHub Copilot, Kiro-style workflows, and generic/local/model-router
workflows. Edit `.ai/entrypoints/` for tool-specific behavior, then run
`./omni sync`.

## Configure a Project

Before using the workspace on a real project, fill in:

```text
.ai/project-configuration.md
```

At minimum, define:

- Project name.
- Repository type.
- Primary language or stack.
- Package manager.
- Build command.
- Test command.
- Lint command.
- Typecheck command.
- Changelog location.
- Documentation locations.
- Branching or pull request standard.
- Comment style.

Then customize:

```text
.ai/rules/universal-engineering-ruleset.json
```

Replace the angle-bracket placeholders with project-specific values and add
project requirements using the included `REQ-###` template.

## Daily Use

Once the workspace is initialized, keep the root assistant files boring. Most
changes should happen inside `.ai/`.

| Need | Edit |
| --- | --- |
| Low-token loading contract | `.ai/context-brief.md` |
| Global assistant behavior | `.ai/core-context.md` |
| Project commands and placeholders | `.ai/project-configuration.md` |
| Portable model loading order | `.ai/context-manifest.json` |
| Generated project structure map | `.ai/project-map.md` |
| Tool-specific entrypoint sources | `.ai/entrypoints/` |
| Local, DeepSeek, or generic model prompts | `.ai/adapters/` |
| OpenRouter or model-gateway prompts | `.ai/adapters/openrouter.md`, `.ai/adapters/model-router.md` |
| Task execution guidance | `.ai/playbooks/` |
| Completion gates | `.ai/checklists/` |
| Body-of-knowledge guidance | `.ai/knowledge/` |
| Strict operating rules | `.ai/rules/universal-engineering-ruleset.json` |
| Requirement registry | `.ai/requirements/requirements.json` |
| Machine-readable contracts | `.ai/schemas/` |
| Implementation workflow | `.ai/rules/controlled-implementation.json` |
| Completion and reporting workflow | `.ai/rules/completion-workflow.json` |
| Data contracts and transformations | `.ai/rules/data-governance.json` |
| Fallback assistant behavior | `.ai/entrypoints/fallback-contract.md`, `.ai/rules/fallback-llm-rules.json` |
| Security exclusions | `.ai/.ignore` |
| Change history | `CHANGELOG.md` |

Example prompt:

```text
Implement REQ-014. Use the minimum access scope from the requirement and follow
the completion workflow in .ai/rules/completion-workflow.json.
```

Where Arbiter is wired in (the `completion.arbiter_gate` rule exists), the order
at the end of a task is fixed: `./omni gate` first, which runs Arbiter and leaves
its report under the rule's `--out` directory, then `./omni requirement complete
<ID>`. Completion reads that report and refuses when `arbiter` is not installed,
when there is no report, when the report is stale (it names another commit, or a
changed file is newer than the scan) or when its gate failed, and each refusal
ends with the command to run (`./omni gate`). The escape hatch is
`--no-arbiter-check "<why, 10+ chars>"`, which completes anyway and records the
reason as a risk note on the requirement -- so a skipped check is visible in the
registry, never silent.

## Maintenance CLI

`omni` is a small dependency-free maintenance helper. It is not required for
normal day-to-day AI usage after the files are already present in a repo, but it
is the easiest way to confirm a drop-in copy is healthy. It requires Python 3.10
or newer.

Use it when you first bring the workspace into a project, change engineering
workspace configuration, edit assistant entrypoints, add rulepacks, or prepare a
public release.

Run it directly from the repository:

```bash
./omni doctor
./omni sync
./omni map
./omni context implementation
```

`./omni sync` updates generated Omni files and skips existing non-Omni files.
Use `./omni sync --force` only after manually deciding replacement is safe.

`./omni context <profile>` prints the exact low-token file set for a task.
Useful profiles are `minimum`, `implementation`, `review`, and `deep_policy`.

Or install the command once from the repository root:

```bash
python3 -m pip install -e .
```

After that, use:

```bash
omni doctor
omni sync
omni validate
omni map
omni context review
omni arbiter baseline
omni arbiter update
omni arbiter sync
```

`arbiter baseline` cuts (or, with `--refresh`, re-cuts on a green gate) the
`.arbiter/baseline.json` the gate rule compares against; `arbiter update`
upgrades Arbiter, its gate rule and a vendored subtree, and re-records the
version; `arbiter sync` runs the subtree pull (or pip upgrade), the reinstall,
the version record, the baseline refresh and doctor in order, stopping at the
first failing step. See [Arbiter alongside the workspace](#arbiter-alongside-the-workspace)
and [Updating an adopted workspace](#updating-an-adopted-workspace).

`sync` verifies that the required `.ai/` source-of-truth files exist, then
refreshes `.ai/entrypoints/`, the root shim files, and synced ignore files.

It does not overwrite the `.ai/` rules. The rules are the source of truth.

`map` generates `.ai/project-map.md`, a compact structure map of the adopter's
project repository. It is meant for LLM navigation: read the map first, choose a
small relevant path set, then inspect only those files. The generated map does
not include file contents and filters paths listed in `.ai/.ignore` plus common
dependency, build, cache, VCS, and local-session directories.

Regenerate it after substantial file moves or new top-level modules:

```bash
omni map
```

Run the doctor after copying, publishing, or adapting the workspace:

```bash
omni doctor
```

The doctor checks:

- Required `.ai/` source-of-truth files.
- JSON parse validity.
- Universal ruleset structure.
- Structured rulepack IDs, required keys, rule IDs, severities, and statements.
- Requirement registry structure and duplicate IDs, including an id that the root
  registry (or its archive) shares with a vendored workspace's registry: that is an
  error naming both files, since one id must name one requirement.
- Assistant pointer drift.
- Assistant entrypoint source drift.
- Synced ignore file drift.
- Workspace file placement.
- Fallback rule availability.
- License, notice, and trademark policy presence.
- Vendored workspaces: a directory carrying its own `.ai/omni-version.json` (a
  subtree such as `arbiter/` here) must run the same CLI files as
  this checkout; drift names the directory and the `omni update` command that
  brings it level.
- Registered MCP servers: every stdio server in `.mcp.json` is launched for real,
  taken through `initialize` and `tools/list`, and must answer with at least one
  tool (a registration that no longer starts would otherwise fail silently). A
  successful probe is remembered for 24 hours in the git directory, keyed on
  `.mcp.json`, `PATH` and the server's executable (path, mtime, size), so a
  repeat `omni doctor` or `omni gate` reads the memo and says when the server was
  probed; `omni doctor --probe` launches the servers again, a failed probe is never
  remembered, and `OMNI_DOCTOR_NO_MEMO=1` turns the memo off.
- Arbiter baseline: once `completion.arbiter_gate` is wired, `.arbiter/baseline.json`
  must exist, be no older than the newest fixed failure-ledger entry, and match
  the current `arbiter.yaml` (`config_hash`); each gap names
  `omni arbiter baseline`.
- Arbiter version: the installed `arbiter` must match the version recorded in
  `.ai/omni-version.json`; drift names `omni arbiter sync`.
- Commit identity: every commit since the gate base (the last 20 when there is
  none) must carry `git config user.email` as author and committer, and never a
  `.local` or `localhost` domain; a mismatch is a warning naming the commit,
  the email and the fix (`git commit --amend --reset-author`, or a rebase).
  Skipped when no email is configured.
- Generated project map availability.
- README architecture references.
- Changelog presence.

`validate` is an alias for `doctor`:

```bash
omni validate
```

After the `Result:` line the doctor prints one `Posture:` line, the state of the
workspace in a glance:

```text
Posture: arbiter score 96.7 (coverage 97%, full scan 2026-10-09 09:06) · gate passed (new high+ 0, fresh) · failures open 1 · requirements open 3 (pending 2, proposed 1)
```

The Arbiter part reads two reports under the parent of the `completion.arbiter_gate`
rule's `--out` directory (and one level below it: `arbiter-out/baseline/`,
`arbiter-out/omni-gate/`), because the gate's `--changed` run is a partial scan
whose grade is withheld by design. The first part is the newest *full* report
(`scan_scope.mode: full`, written by `omni arbiter baseline` or a plain `arbiter
scan`): its score (or `grade withheld`), coverage and when it ran, or `arbiter no
full scan (run ./omni arbiter baseline)`. The second is the newest *partial* report,
the gate's own: `gate passed` or `gate failed` with the unsuppressed new
high-or-critical findings and `fresh` when it still describes HEAD, `gate stale:
<reason>` when it names another commit or a changed file in the rule's scope is
newer than the scan (an unreadable report counts as stale, with the reason), or
`gate no report (run ./omni gate)`. `arbiter not wired` replaces both when no such
rule exists. Then the open (or mitigated) failure-ledger entries and the open
requirements by status.

`omni doctor --json` (and `validate --json`) print the same report as JSON instead:
`{"schema_version": 2, "ok", "passed", "warnings", "errors", "posture"}`.
`posture.arbiter` carries `wired`, `full` and `gate`; the `schema_version` 1 keys at
its top level (`present`, `fresh`, `grade`, `coverage`, `gate_passed`, ...) are kept
for one release, filled from the `gate` block. Keys are only ever added, never renamed
or removed within a schema version, so a script or a CI step can read `ok`, `errors`
and `posture.arbiter` without parsing the text.

## Code Graph (omni graph)

`omni map` tells an assistant where files live. `omni graph` tells it how the
code inside those files actually connects -- which function calls which,
which class inherits from which, which module imports which -- as a real,
traversable graph, not a vector index. There are no embeddings and no
similarity scores: every node is a code entity read straight out of a
tree-sitter syntax tree, and every edge is tagged with exactly where it came
from:

- **EXTRACTED** -- a fact read directly from one source site: this import
  statement names this module; this call site names this function; this
  class statement names this base class. Nothing was resolved.
- **INFERRED** -- resolved by traversing the graph itself (following imports
  and scopes across files to find the actual definition a name refers to),
  or, if you configure a semantic API, by that API. An INFERRED edge always
  sits on top of an EXTRACTED edge that justifies it; an unresolved
  reference is left EXTRACTED-only rather than guessed.

Build the graph (covers Python, JavaScript, and TypeScript):

```bash
omni graph build
```

This needs tree-sitter and its per-language grammars, which are an optional
extra so the workspace stays dependency-free by default. If you cloned this
repo (its `pyproject.toml` is present):

```bash
python3 -m pip install -e ".[graph]"
```

If you adopted `omni`/`make_ai.py`/`omni_graph.py` into another project via
`omni adopt --include-cli` (no `pyproject.toml` from this repo there), install
the same packages directly instead:

```bash
python3 -m pip install "tree-sitter>=0.23,<1.0" "tree-sitter-python>=0.23,<1.0" \
  "tree-sitter-javascript>=0.23,<1.0" "tree-sitter-typescript>=0.23,<1.0"
```

On Debian/Ubuntu (and other PEP 668 "externally managed" Pythons) `pip`
refuses system-wide installs. Use a virtualenv and run `build` with its
interpreter, rather than `--break-system-packages`:

```bash
python3 -m venv ~/.venvs/omni-graph
~/.venvs/omni-graph/bin/pip install "tree-sitter>=0.23,<1.0" "tree-sitter-python>=0.23,<1.0" \
  "tree-sitter-javascript>=0.23,<1.0" "tree-sitter-typescript>=0.23,<1.0"
~/.venvs/omni-graph/bin/python ./omni graph build
```

You do not have to remember the long form: if `omni graph build` finds tree-sitter
missing, it re-runs itself under `~/.venvs/omni-graph/bin/python` (or the
interpreter named by `OMNI_GRAPH_PYTHON`) when that venv has the packages, so a
plain `./omni graph build` works. If no such venv exists it prints the setup
commands above instead of a `pip install` that would be refused.

Without either, `omni graph build` fails with a clear install message
instead of a traceback. `trace` and `show` below only read the JSON `build`
already wrote, so they work with just the standard library.

Ask how two symbols are connected -- this is the point of the feature. A
plain name resolves if it's unique; use `Class::method` or the full
`file::path` id to disambiguate, the same identifiers the graph's own JSON
uses:

```bash
omni graph trace run_doctor "DoctorReport::error"
```

```
make_ai.py::run_doctor
  --[calls, INFERRED]--> make_ai.py::DoctorReport
  --[defines, EXTRACTED]--> make_ai.py::DoctorReport::error
```

Inspect one symbol's direct connections:

```bash
omni graph show "make_ai.py::DoctorReport"
```

See the whole thing as an actual graph -- a force-directed node-link SVG,
not a chart -- rendered by a small pure-Python layout (no numpy/networkx;
`render`, like `trace`/`show`, only needs the standard library):

```bash
omni graph render
```

This writes `.ai/project-graph.svg`: circles are symbols (sized by
module/class/function, colored by language), lines are resolved edges
(`calls`/`imports`/`inherits` in color, `defines` faint). It updates every
time you run it, so it always reflects the current state of your project --
regenerate it after significant changes the same way you'd re-run `omni
map`. Large graphs are capped to the highest-degree `--max-nodes` (default
300); use `--focus <symbol> --depth 2` to render just one area's
neighborhood instead of the whole codebase, and `--include-external` to
also show unresolved references (stdlib calls, third-party imports).

List everything the graph knows, not just one symbol:

```bash
omni graph show --all                       # every symbol, grouped by file, with in/out edge counts
omni graph show --all --kind class --sort degree --limit 20
omni graph show --all --file 'frontend/*' --language typescript
omni graph show --all --edges --include-external   # also print edges and unresolved references
```

Explore it interactively in 3D -- rotate, pan, zoom, click a node to read
it, double-click to pull its neighbours into the view:

```bash
omni graph view --open
```

This writes `.ai/project-graph.html`, one self-contained file (about 1.5 MB
plus your graph) that works offline: nothing is fetched at view time. It
starts with the 500 best-connected symbols (`--max-initial`, or `--all`,
or `--focus <symbol> --depth 2` for one neighbourhood) and grows as you
expand. The page has search (`/`), language and edge-type filters, colouring
by language, kind or top-level directory, an INFERRED-edge toggle, an
edge-strength slider, and a detail panel that lists each symbol's callers and callees -- click any of
them to jump there. Nodes and edges are scaled by distance so outliers far from the core cluster
and far-away edges stay readable; the styling follows the OmniEngineering brand
(`#e0475c` on `#0f0f12`). It needs a WebGL-capable browser; the SVG from `omni
graph render` and `show --all` remain for everything else. The output is
git-ignored like the JSON and SVG.

Under WSL, `omni graph view` prints the `file:///C:/...` URL (and the plain Windows
path) that a Windows browser can open, not the Linux `/mnt/c/...` path it cannot,
and `--open` launches your default Windows browser through `cmd.exe`.

The 3D engine is the unmodified [3d-force-graph](https://github.com/vasturiano/3d-force-graph)
bundle (MIT, (c) Vasco Asturiano), which renders with [three.js](https://threejs.org)
(MIT) and contains 33 other permissively licensed packages. Their notices
live in `.ai/graph-viewer/THIRD_PARTY_NOTICES.md` and are embedded in every
generated page. The assets in `.ai/graph-viewer/` are excluded from
`.ai/.ignore` so assistants do not read the 1.3 MB minified bundle.

Both `trace`/`show`/`render` for machine-readable output
(render's is a summary, not the SVG itself), and every `omni graph`
subcommand is a thin, scriptable wrapper (one verb in, one JSON document
out) so it can be exposed 1:1 as tools by an MCP relay, the same way
`omni_map` / `omni_doctor` / `omni_sync` already are.

Add `--semantic` to `build` to also run an opt-in enrichment pass tagged
`INFERRED` via a configured API. Nothing leaves this machine unless you set:

```bash
export OMNI_GRAPH_SEMANTIC_API_URL=https://your-endpoint
export OMNI_GRAPH_SEMANTIC_API_KEY=...   # optional
export OMNI_GRAPH_SEMANTIC_MODEL=...     # optional
omni graph build --semantic
```

Every relation the API suggests is checked against known graph symbol names
before being added as an edge, so a hallucinated relation can't be written
into the graph silently.

### Five layers: code, governance, history, assurance, workspace

`omni graph build` builds five layers in one graph, each with its own nodes and
edge types, so a single traversal can go from a line of code to why it exists,
when it changed, how it is proven, and which rule stops it breaking again. This
is what keeps long histories manageable: instead of rereading a changelog or a
git log, ask the graph what is tied to the thing you are about to touch.

| Layer | Nodes | Edges | Source |
| --- | --- | --- | --- |
| **code** | modules, classes, functions, tables | `calls`, `imports`, `defines`, `inherits`, ... | your source (tree-sitter, SQL migrations) |
| **governance** | `requirement`, `changelog` | `touches`, `records`, `mentions` | requirement files, changelog |
| **history** | `commit` | `delivers`, `modifies`, `logged_in`, `follows` | the git log |
| **assurance** | test files, `suite`, `failure`, `finding` | `verifies`, `contains`, `covers`, `affects`, `arose_in`, `guards`, `fixed_by`, `recurs`, `flags`, `cites`, `recorded_as` | tests, the suite registry, the failure ledger, the newest Arbiter report |
| **workspace** | `rulepack`, `rule`, `playbook`, `checklist`, and OmniEngineering's own code | `defines`, `prevented_by` | `.ai/rules`, `.ai/playbooks`, `.ai/checklists`, `make_ai.py`, `omni_graph.py` |

- A requirement `touches` the files in its declared scope (EXTRACTED) and the
  files changed by commits that deliver it (INFERRED). Changelog entries `record`
  the requirements they name and `mention` the files they cite.
- Every commit is a node, chained in order (`follows`). A commit `delivers` the
  requirement named in its subject line (a body that cites another project's id is
  ignored), `modifies` the files it changed, and is `logged_in` the changelog entry
  whose heading it added. A squash commit with no id in its message is tied to the
  requirement its changelog entry records (INFERRED).
- The assurance layer is about **your project's tests**. Test files (`test_*.py`,
  `*.test.ts`, `*Test.java`, `tests/`, ...) and anything a registered suite claims
  move to it, and `verifies` edges connect each test file to the code it calls.
  OmniEngineering's own files are tooling: they sit in the workspace layer and are
  never counted as tests, suites, or coverage. A suite groups its test files, says
  how to run them, and `covers` the code it is meant to cover.
- The failure ledger adds a `failure` node per entry with its symptom and root
  cause, linked to the code it affected, the requirement, the tests or suite that
  `guard` it, the rule or playbook that prevents a repeat (`prevented_by`, into the
  workspace layer), and any earlier failure it repeats.
- Every unsuppressed finding in the newest Arbiter report (`arbiter-out/**/report.json`, the
  `--out` of the `completion.arbiter_gate` rule) is a `finding` node named by its id (`f:17a59c37fc20`)
  that `flags` the file it was raised on and the function, class or method whose span holds the
  line, `cites` the requirement named by a `req:<ID>` tag, and is `recorded_as` the ledger entry
  whose `how_detected` quotes the finding id. Only the id, location, rule, category, severity,
  status and tags are copied -- never the evidence. Directory nodes `contain` their files and
  subdirectories, so the hierarchy root > directory > file > symbol > finding is explicit.

Traverse across the layers:

```bash
omni graph why omni_graph.py        # requirements, changelog, commits, tests, suites, failures, rules
omni graph why REQ-021              # files touched, changelog entries, commits, failures
omni graph why FAIL-003             # affected code, regression tests, prevention, fix commits
omni graph why a1b2c3d              # a commit: requirements, changelog entry, files, previous/next commit
omni graph why backend-junit        # a suite: its test files, what it covers
omni graph why f:17a59c37fc20       # an Arbiter finding: rule, severity, category, flagged code, requirement, ledger entry
omni graph findings --tree --severity high   # findings by directory, with what each is tied to (--under, --dimension, --json, --view)
omni graph lineage REQ-021          # everything upstream and downstream of it, no second endpoint needed
omni graph lineage a1b2c3d --up     # a commit: the requirement and previous commit that led to it
omni graph lineage FAIL-003 --depth 3 --json   # a failure: cause above it, tests, rules and fixes below it
omni graph timeline REQ-021         # everything dated that is tied to it, oldest first
omni graph impact                   # what the pending change set reaches: requirements, failures, suites, tests, rules, commits
omni graph impact --changed main --depth 3 --json   # a different base commit, a wider walk, machine-readable
omni graph show --all --layer history --kind commit
omni graph build --layers code,governance      # skip layers; --max-commits N bounds the git scan
```

`omni graph impact` starts from the files `omni gate` would check (the working tree
plus the commits since the merge-base, or since `--changed BASE`), resolves each the
way `why` does, and walks the cross-layer edges in both directions, two hops by
default, never a code edge: the result is bucketed by kind, each entry carrying the
hop count and the edge it was reached by, with the paths the graph does not know
listed as `unresolved`. When `.ai/project-graph.json` exists, `omni gate` prints the
same walk as one line beside the changed set (`impact: 2 requirement(s), 1 failure(s),
1 suite(s), 3 test file(s), 2 rule(s); 1 path unresolved`) -- advice, never a verdict:
a stale or broken graph prints nothing and changes nothing. `omni test run --impacted`
uses the same walk to pick the suites worth running.

Measure the token-savings claim instead of just asserting it, on your own project's own graph, right now:

```bash
omni graph benchmark          # a targeted query vs. grep-and-read-whole (git show for a commit), for real
omni graph benchmark --json
```

For one real requirement, commit, failure and file this project's own graph already has (never invented,
never hardcoded to a specific project), it runs the graph query and the naive alternative -- a plain-text
grep for the name, then every matching file read in full; a commit compares against `git show`, the diff a
person would actually read -- and reports both sizes. "Tokens" are `chars / 4`, a labelled rough estimate,
not a real tokenizer. Two safeguards keep the file case honest: it only ever picks an already-parsed
source module (never a generic file node, so a `.pptx` or an image can't win) and only a git-tracked one
(so a gitignored multi-hundred-megabyte build log that merely happens to have a graph node can't either --
both were real results the first time this ran).

### The viewer

`omni graph view` writes one self-contained, offline HTML page (nothing is fetched when it opens).

- **2D or 3D, one big switch.** The 2D view is a flat Canvas 2D drawing you pan (drag) and zoom
  (scroll or pinch); it never tilts and needs no WebGL or video memory, so it suits low-power
  machines. It is the default. The 3D view orbits in space and needs WebGL; it is created only when
  chosen and destroyed when you switch back, so 2D never holds a GPU context. `--mode 2d|3d|auto`
  sets the start (`auto` remembers your last choice).
- **Views, not layer switches.** Tabs across the top (Everything, Code, Requirements, History, Tests &
  failures, Rules & playbooks, Database) each set the layers, colours and layout for one purpose. A strip
  under them names the current view in large type, explains it in a sentence, and shows a colour key
  with counts for what is on screen. Each layer has one strong hue; kinds also have distinct shapes in 2D
  (diamond requirement, triangle commit, hexagon failure or table, page change-log entry or playbook).
- **2D layouts.** *Network* (connected things sit together), *Layers* (one labelled band per layer, parents
  kept beside their children, so links between layers stay short), and *Tree* (files, classes and methods
  as a tree, parent to child; everything without a parent is listed below, grouped by kind).
- **Select a node and its whole chain lights up.** Everything above it (parents) and below it (children)
  is drawn in gold with bold links, the rest recedes, and the view zooms to that chain. The detail panel
  shows a breadcrumb (`file > class > method`) and buttons that bring in what it connects to, grouped by
  layer. A node with no parent or child highlights what it connects to instead.
- **Trace a path between any two things.** Set a From and a To (in the *Trace a path* menu, from search
  results, or with *Trace from here / to here*) and the shortest chain of links is drawn in pink, every
  node on it labelled, with the steps written out.
- **Search.** Every word must match; `kind:commit`, `layer:assurance`, `lang:java` and `file:...`
  narrow it; results show kind, date and summary, and go to / trace from / trace to buttons; arrow keys and
  Enter work; a miss suggests the closest things. With nothing typed, a kind lists everything of that kind.
- **Comfort.** Every menu section collapses (remembered), every option has a tooltip, filter groups have
  All / None, and there is a soft-grey light theme (not pure white) as well as the dark one.

- **Findings.** When the graph holds Arbiter findings, a *Findings* tab lays them out as a tree under the
  directory, file and symbol they were raised on (`root › dir › file › Class › method › finding`), with the
  requirements they cite and the failures they were recorded as. Two more colour modes, *finding category*
  (Arbiter dimension) and *finding severity*, colour the findings and leave everything else in its layer
  colour; a *Filter: findings* section has severity and category rows; the search accepts `severity:high`,
  `dim:security` and `rule:secrets`, and matches finding ids and rule ids. A finding's panel shows
  `path:line` with a Copy button and an editor link, the rule, severity, category and status, *Trace to
  requirement* / *Trace to failure* buttons, and a copyable `arbiter review <report> --rule <rule_id>`
  line. `omni graph findings --view` opens the viewer on that tab.

`omni graph render` draws the code layer only unless you pass `--all-layers`; findings are coloured by
category, sized by severity, and a `new` finding wears a ring (see the "findings by category" legend).

### Trace an Arbiter finding

The gate produces findings; the graph makes each one a node you can walk from the line of code to
the requirement it belongs to and the ledger entry that closes it:

```bash
omni gate                                     # runs `arbiter gate` through completion.arbiter_gate; writes arbiter-out/omni-gate/report.json
omni graph build                              # the newest report's unsuppressed findings become `finding` nodes
omni graph why f:17a59c37fc20                 # rule, severity, category, the flagged file and symbol, the requirement, the failure
omni graph findings --tree --severity high    # every high finding by directory, with the requirements, failures and suites it reaches
omni graph view --focus f:17a59c37fc20 --open # the same finding in the viewer, its chain lit up: dir > file > symbol > finding
arbiter review arbiter-out/omni-gate/report.json --rule arbiter/secrets.aws-access-key   # adjudicate it
omni failure add --requirement REQ-012 --title "..." --symptom "..." --detected "arbiter finding f:17a59c37fc20"
omni graph build                              # the finding is now `recorded_as` that ledger entry
```

`omni graph sources` names the report the build would read, or says `findings: no Arbiter report
found (run ./omni gate)`. Set `findings_report` in `.ai/graph-config.json` to read a specific report,
or to `null` to leave the findings out.

### Using it in your own project

Nothing is hard-wired to OmniEngineering's layout. Ask what the build would read
from your project, and what it is missing:

```bash
omni graph sources             # per layer: files found, counts, what is missing, how to fix it
omni graph sources --write     # draft .ai/graph-config.json from what it found
```

Only the settings that differ from the defaults belong in `.ai/graph-config.json`:

| Key | Default | Use it when |
| --- | --- | --- |
| `requirements_files` | `.ai/requirements/requirements*.json` | your requirements or issues live elsewhere (a JSON list, or an object with `requirements`, of `{id/key, title/summary, status, scope/files}`) |
| `requirement_id_pattern` | derived from your ids | your ids are unusual (`#42`); by default the exact ids in your registry are matched, so `PROJ-12`, `FEAT_7` and `REQ-001` all work |
| `changelog_files` | `CHANGELOG.md`, `HISTORY.md`, `docs/CHANGELOG.md`, ... | your changelog is named or placed differently (dated `## 2026-01-31`, versioned `## [1.2.0] - 2026-01-31` and `## v1.2.0 (2026-01-31)` headings all work, as do dated `###` sections kept under one `## [Unreleased]` heading) |
| `test_globs` / `exclude_test_globs` | naming conventions | your tests do not follow them |
| `exclude_code_globs` | `[]` | directories the code layer must never parse although they are tracked and readable: test corpora with planted defects, vendored examples, generated output (`fixtures/`, `examples/**`) |
| `test_suites_file`, `failure_ledger` | `.ai/test-suites.json`, `.ai/failures/failure-ledger.json` | you keep them elsewhere |
| `ci_files` | GitHub, GitLab, Cloud Build, Azure, Jenkins, Makefile | extra CI files hold your test commands |
| `rules_dir`, `playbook_dirs`, `checklist_dirs` | `.ai/...` | your rules and playbooks live elsewhere |
| `max_commits` | 400 | you want more or less history |
| `findings_report` | newest `report.json` under the `completion.arbiter_gate` rule's `--out` (default `arbiter-out`) | you want the finding nodes read from one specific Arbiter report, or `null` to leave them out |
| `tooling_paths` | `make_ai.py`, `omni_graph.py`, `omni`, `.ai/*` | set to `[]` if OmniEngineering itself is your project |

A project with no `.ai/`, no git, no requirements, or no tests still builds: each
missing source becomes a note that says what was looked for and how to point at it.
Malformed JSON is reported by file and line instead of silently ignored, and `omni
doctor` validates the config. The suite registry is managed with `omni test`:

```bash
omni test detect [--write]   # find suites from file contents and CI commands; register them
omni test add --name "Backend JUnit" --paths backend/src/test --framework junit --command "cd backend && mvn test" --covers backend/src/main
omni test list | check | remove <id>
omni test run                # run every registered suite's command, one line per suite, exit 1 on any failure
omni test run backend-junit  # by id or name
omni test run --impacted     # only the suites `omni graph impact` ties to the pending change set
```

`omni test run` runs each suite's registered command through the shell from the
project root and reports `PASS`, `FAIL`, `SKIP` (no command registered) or `TIMEOUT`
(`--timeout`, default 900 s) with the last five lines of output for anything that did
not pass; `--json` gives the same as data. `--impacted` resolves the change set
(`--changed BASE`, else the merge-base `omni gate` uses) through the graph and picks
the suites whose files, paths or declared coverage meet a changed file or a test
file the walk reached, directory prefixes included; without a graph it says so and
runs every suite. The completion rulepack ships `completion.tests` as a required
rule, so `omni gate` runs it:

```json
{
  "id": "completion.tests",
  "severity": "required",
  "validation": {"type": "command", "run": "omni test run --impacted", "when_changed": ["**"],
                 "ignore": [".ai/**", "*.md", "docs/**"], "timeout": 900}
}
```

Every commit then runs the suites its change reaches; with no suite registered
there is nothing to run, which passes. The gate keeps the hook fast another way:
a `command` rule that passed is memoised in the gate state file
(`.git/omni-gate-last.json`) on the changed paths its own globs select, their
mtimes and sizes, and its run text, and the next `omni gate` reports it as
`passed (memo)` instead of re-running it until a scoped file changes. A failed
rule is never memoised; `omni gate --no-memo` forces every rule; the non-hook
output prints each command rule's duration (`ran     completion.tests 41.2s PASS`).
Skip the rule for one change with `omni waive completion.tests --reason "..."`,
or set its severity to `recommended` to switch it off.

### MCP server: the graph as tools for any assistant

`omni graph why/lineage/trace/...` and `omni requirement/failure show/list/search`
already print plain JSON (`--json`). `omni mcp serve` exposes the same data as MCP
(Model Context Protocol) tools over stdio, so an assistant queries this project's
requirements, changelog, commits, tests and failures directly, without a shell tool
to run the CLI and parse its output -- and without that assistant being Claude. This
is the multi-LLM half of "context management": the graph is one project fact base,
reachable by whatever is asking.

```bash
omni mcp tools          # list the tools without starting the server
omni mcp tools --json   # ...with full input schemas, e.g. to feed a client's config
omni mcp serve          # serve them over stdio (JSON-RPC 2.0, one JSON object per line) until stdin closes
```

Point an MCP client's command at `omni mcp serve` (working directory: your project
root). Every tool is read-only -- `graph_lineage`, `graph_why`, `graph_trace`,
`graph_timeline`, `graph_impact`, `graph_show`, `graph_sources`, `graph_findings`, `requirement_show`,
`requirement_list`, `requirement_search`, `failure_show`, `gate_status` -- so a
client can call them with no confirmation step; nothing here writes a requirement,
a changelog entry or a waiver. `omni requirement draft`, `omni requirement add` and
`omni gate --hook` remain how anything gets written. Hand-rolled against the wire
protocol (JSON-RPC 2.0, stdlib only) rather than an SDK dependency, in keeping with
everything else here: this has to work with only the standard library, on whatever
Python an assistant's environment already has.

## Failure Ledger

Errors are only useful if they are not made twice. The failure ledger
(`.ai/failures/failure-ledger.json`, schema `.ai/schemas/failure-ledger.schema.json`)
records what went wrong, why, the test that now catches it, and the rule or
playbook that prevents a repeat. Manage it with the CLI, not by hand:

```bash
omni failure add --requirement REQ-042 --title "Rounding drops cents" \
  --symptom "total is 10.00, expected 10.05"
omni failure update FAIL-001 --status fixed \
  --root-cause "float used for money" --fix "use Decimal" \
  --tests tests/test_totals.py::test_keeps_cents \
  --affected src/totals.py --prevention-rules money.no_floats
omni failure list --status open
omni failure search "rounding"
omni failure check       # complete, and every file/test/rule it names exists
```

`omni doctor` fails a `fixed` entry that lacks a root cause, a fix, a regression
test (or an honest `no_test_reason`), or a prevention. `omni requirement complete`
refuses a defect/bug/fix/regression requirement that has no ledger entry unless
`--no-failure-entry "<reason>"` is given (the reason is recorded as a risk note).
The rules `completion.failure_ledger`, `completion.regression_test`,
`completion.failure_becomes_rule` and `controlled.consult_failure_history`, and
steps in the debugging, testing, implementation, review, planning, handoff and
release playbooks, make consulting and recording failures part of the standard
workflow. `omni adopt` ships an empty ledger, an empty suite registry and no graph config,
never this repository's own.

## Low-Friction Editing

Most people should not need to hand-edit large JSON files for routine updates.
Use the CLI for small, safe additions.

Add a requirement:

```bash
omni requirement add \
  --title "Add release checklist" \
  --description "Create a repeatable release checklist for AI-assisted changes." \
  --category "Workflow" \
  --priority medium \
  --scope "README.md,.ai/rules/completion-workflow.json" \
  --acceptance "Checklist exists,Doctor passes"
```

If `--id` is omitted, the CLI assigns the next `REQ-###` ID.

Writing that by hand for every commit is exactly the manual overhead this workspace exists to cut down
on. Draft it instead, then edit the draft rather than starting from nothing:

```bash
omni requirement draft                 # from the current (uncommitted) change set
omni requirement draft --commit HEAD   # from one already-made commit: its subject becomes the title
```

The category is guessed from the changed paths (tests, `.github/workflows`, all-Markdown, "fix" in a
path, or a plain default), the scope is the changed files, and the requirement is always written with
status `proposed` -- never `completed` -- so nothing is trusted until a person reviews it. A matching
`### Proposed` stub is appended to `CHANGELOG.md` under today's date. If the commit's message already
cites a requirement ID, nothing is drafted (a duplicate would just be noise); `--force` overrides that.

Query and update the registry without opening the file (it grows large):

```bash
omni requirement list --status pending
omni requirement show REQ-042
omni requirement search "login"
omni requirement update REQ-042 --status blocked --note "waiting on API key"
omni requirement complete REQ-042
omni requirement update REQ-043 --status withdrawn --note "superseded by REQ-050"
omni requirement archive --keep-recent 25   # move old completed and all withdrawn entries aside
omni requirement archive --id REQ-041,REQ-043  # or name them; live (pending, blocked) work is refused
```

**Renaming the prefix.** A project that vendors several workspaces, or that adopted the
template under the default `REQ` and later wants ids that say which registry they belong
to, can change the prefix in one step:

```bash
omni requirement renumber --prefix ARB --dry-run   # per-file change counts, nothing written
omni requirement renumber --prefix ARB             # REQ-012 -> ARB-012, keeping the number
```

It rewrites every id in the registry and the archive, sets `requirement_id_prefix`,
rewrites the `requirement` fields of the failure ledger and the gate waivers, and
replaces `REQ-###` with `ARB-###` in governance text only: `.ai/**/*.md`,
`.ai/**/*.json`, the root `*.md` files (`CHANGELOG.md`, `README.md`), `docs/**/*.md`,
`.claude/**/*.md` and `.claude/skills/**` (`--paths GLOB ...` replaces that list). Code,
tests, `.git`, vendored workspaces and `arbiter-out` are never touched, so git history
keeps citing the old ids. That history stays valid because the command records
`id_aliases` (`{"REQ-012": "ARB-012"}`) in the registry: the gate, `omni requirement
show|update|complete|archive` and `omni failure check` resolve an old id to its current
one, and `omni doctor` fails on an alias that points at nothing or that is still a live
id. Running the command again with the same prefix is a no-op.

Enforce the completion rulepack instead of trusting the assistant to remember it:

```bash
omni gate            # fail if changed files lack changelog/registry updates
omni waive completion.changelog_gate --reason "docs-only typo fix"
omni hook install         # Claude Code Stop hook: blocks the assistant from finishing while the gate fails
omni hook install-git     # portable git pre-commit hook: blocks `git commit` too, any assistant, no assistant, any OS
omni hook install-git --with-graph-rebuild   # ...and rebuild the graph in the background after every commit
```

Most of the 60-odd rules across the rulepacks are judgment calls -- "reduce
cognitive load," "prefer composition," "use clear names" -- and stay that way
on purpose: mechanizing them would mean checking a shallow proxy and letting
the real judgment slide, which is worse than an honest "not machine-checked."
A rule earns a `validation` block only when there is a real, non-fake check
for it. Four types exist today:

| `validation.type` | What it actually checks |
| --- | --- |
| `co_changed` | When any changed path matches `when_changed` (and not `ignore`), at least one changed path must also match `must_also_change` -- e.g. touching anything requires touching `CHANGELOG.md`. |
| `requirement_registry_entry` | Every `REQ-###`-shaped ID cited in a commit message since the base, or newly added to `CHANGELOG.md`, must actually exist in the requirements registry, or in the registry of a vendored workspace below the root (a subtree's commits cite its own ids) -- catches a typo'd or invented ID that `omni doctor`'s registry-schema check cannot see, since that only validates the registry's own shape, never what other files claim about it. |
| `content_forbidden` | Changed files are scanned for a short list of regex patterns (a private-key header, an AWS-shaped access key, an obviously hardcoded credential). A bounded, honest safety net for the most common accidental leaks, not a claim of exhaustive secret scanning. |
| `command` | Runs the project's own check as the gate -- a scanner, a test suite, a linter -- when a changed path matches `when_changed` (minus `ignore`). `run` is split like a shell line, `{base}` becomes the gate's base commit, and `timeout` (seconds, default 600) bounds it. A non-zero exit, a timeout, or an executable missing from `PATH` fails the rule with the last lines of output attached: an unrunnable check is not a pass. `gate_status` over MCP runs it too, so keep it to checks that only read. |

Add a rule to a rulepack:

```bash
omni rule add \
  --rulepack completion \
  --name release_checklist \
  --statement "Use the release checklist before publishing a completed change." \
  --severity recommended \
  --scope "release,completion"
```

Rulepack aliases:

| Alias | Rulepack |
| --- | --- |
| `controlled` | `.ai/rules/controlled-implementation.json` |
| `completion` | `.ai/rules/completion-workflow.json` |
| `data` | `.ai/rules/data-governance.json` |
| `fallback` | `.ai/rules/fallback-llm-rules.json` |
| `hci` or `ui` | `.ai/rules/hci-ui-rules.json` |
| `oop` | `.ai/rules/oop-design.json` |

After edits, run:

```bash
omni doctor
```

## Fallback Rules for LLMs

The root assistant files stay tiny and point to `.ai/entrypoints/`. The compact
fallback operating contract lives in one central file so it does not bloat every
root shim. This protects the workspace when an LLM does not fully follow the
original configuration instruction.

The fallback contract tells every assistant to:

- Confirm or assign a `REQ-###` ID before work begins.
- State the minimum access scope before inspecting files.
- Avoid unrelated repository scans and unrelated file changes.
- Respect `.ai/.ignore` and avoid secrets, credentials, logs, caches, and local
  environment files.
- Preserve existing behavior unless it conflicts with the active requirement.
- Avoid new dependencies, schema changes, destructive data changes, and public
  interface changes unless explicitly required.
- Update docs, changelog, and requirements when work changes behavior or status.
- Run `omni doctor` for workspace changes.
- Run `omni sync` when assistant entrypoint files change.
- Report validation, risks, commit sentence, and pull request information.

The shared source for this behavior is:

```text
.ai/entrypoints/fallback-contract.md
.ai/rules/fallback-llm-rules.json
```

## Security Guardrails

Use `.ai/.ignore` to document files and paths the assistant must not read.
Common examples include:

```text
.env
.env.*
*.pem
*.key
node_modules/
vendor/
.venv/
dist/
build/
*.log
*.sqlite
*.db
```

This is a prompt-level guardrail, not a substitute for repository permissions,
secret scanning, or platform security controls.

## Symlink Alternative

For Unix-only teams, symlinks can route assistant files directly to shared
context. This is an advanced alternative for teams that understand symlink
behavior across their editors and hosting platform:

```bash
ln -s .ai/entrypoints/codex.md AGENTS.md
ln -s .ai/entrypoints/cursor.md .cursorrules
ln -s .ai/entrypoints/claude.md CLAUDE.md
mkdir -p .github
ln -s ../.ai/entrypoints/copilot.md .github/copilot-instructions.md
```

Markdown routing is the default recommendation. A drop-in copy with generated
entrypoint files works better across macOS, Linux, Windows, GitHub, package
archives, and editor integrations.

## Why It Works

OmniEngineering treats AI-assisted delivery as engineering architecture, not
scattered editor settings. Every assistant receives the same project context,
but each tool still gets its native entrypoint.

The result is a workspace where AI behavior is:

- Consistent across tools.
- Traceable to requirements.
- Safer around secrets and destructive changes.
- Easier to update.
- Easier to review.
- Less dependent on one assistant vendor or IDE.

## License

OmniEngineering is licensed under the Apache License, Version 2.0. The
OmniEngineering names, marks, and project identity are governed by the
repository trademark policy. User projects built with this workspace remain
owned and licensed by their project owners.

One directory is excluded: `arbiter/`, the vendored Arbiter subtree, is
licensed under its own `arbiter/LICENSE` (all rights reserved) and the
Apache-2.0 grant above does not extend to it. `NOTICE` and the license guide
say so; see [The vendored Arbiter subtree](#the-vendored-arbiter-subtree).

See:

- [LICENSE](LICENSE)
- [NOTICE](NOTICE)
- [Trademark policy](TRADEMARKS.md)
- [License guide](LICENSES/README.md)
- [User project license template](LICENSES/USER-PROJECT-LICENSE-TEMPLATE.md)
