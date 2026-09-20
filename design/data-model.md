# Data Model

## Model Summary

OmniEngineering uses repository files as its data model. The system separates
human narrative, structured rules, requirement records, knowledge packs,
assistant routing, and validation schemas.

See [Data Model Diagram](diagrams/data-model.svg) for the relationship map.

## Core Entities

| Entity | Location | Format | Owner |
| --- | --- | --- | --- |
| Core context | `.ai/core-context.md` | Markdown | Maintainer |
| Project configuration | `.ai/project-configuration.md` | Markdown | Maintainer |
| Context manifest | `.ai/context-manifest.json` | JSON | Maintainer |
| Adapter prompt | `.ai/adapters/*.md` | Markdown | Maintainer |
| Playbook | `.ai/playbooks/*.md` | Markdown | Maintainer |
| Checklist | `.ai/checklists/*.md` | Markdown | Maintainer |
| Knowledge pack | `.ai/knowledge/**/*.md` | Markdown | Maintainer |
| Universal ruleset | `.ai/rules/universal-engineering-ruleset.json` | JSON | Maintainer |
| Companion rulepack | `.ai/rules/*.json` | JSON | Maintainer |
| Requirement registry | `.ai/requirements/requirements.json` | JSON | Maintainer or CLI |
| Failure ledger | `.ai/failures/failure-ledger.json` | JSON | CLI (`omni failure`) |
| Test suite registry | `.ai/test-suites.json` | JSON | CLI (`omni test`) |
| Graph config | `.ai/graph-config.json` | JSON | Maintainer or `omni graph sources --write` |
| Schema contracts | `.ai/schemas/*.json` | JSON Schema-style JSON | Maintainer |
| Assistant entrypoint | `LLM_CONTEXT.md`, `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `.github/copilot-instructions.md`, `.kiro/steering/omnicontext.md` | Markdown | CLI sync |
| Changelog | `CHANGELOG.md` | Markdown | Maintainer |
| Design docs | `design/*.md` | Markdown | Maintainer |

## Requirement Record

Each requirement record tracks a unit of work that can be referenced by
assistants, documentation, and the changelog.

Required fields:

- `id`
- `category`
- `title`
- `description`
- `priority`
- `status`
- `minimum_access_scope`
- `acceptance_criteria`
- `validation_required`
- `documentation_required`
- `risk_notes`

The ID format uses the configured prefix and a three-digit number, such as
`REQ-011`.

## Failure Record

Each failure-ledger entry records what went wrong so it is not repeated. It is
ingested by `omni graph build` as the assurance layer of the code graph.

Required fields: `id` (`FAIL-###`), `title`, `status` (`open`, `fixed`,
`mitigated`, `wontfix`), `symptom`.

Required once `status` is `fixed` or `mitigated`: `root_cause` (why, not what),
`fix_summary`, `regression_tests` or a `no_test_reason`, and `prevention_rules`
or `prevention_notes`.

Optional: `date`, `severity`, `how_detected`, `requirement` (the `REQ-###` being
worked), `affected` (files, directories or symbols), `fix_commits`,
`recurrence_of` (an earlier `FAIL-###`).

`prevention_rules` names rule ids from `.ai/rules/*.json` or repository paths
such as a playbook. Each reference becomes a graph edge (`affects`, `arose_in`,
`guards`, `prevented_by`, `fixed_by`, `recurs`); references that do not resolve
are reported by `omni failure check` and as a build note, never silently added.

## Test Suite Record

`.ai/test-suites.json` lists the project's test suites. Required: `id`, `paths`
(files, directories or globs; every source file under them becomes a test in the
graph whatever it is named). Optional: `name`, `kind` (`unit`, `integration`,
`e2e`, `validation`, `smoke`, `other`), `framework`, `command`, `covers` (code
paths the suite is meant to cover), `notes`. Registered suites win over
auto-detected ones; detection reads test-framework signals in file contents (not
comments) and the test commands in CI files.

## Graph Config

`.ai/graph-config.json` is optional and holds only settings that differ from the
defaults: `requirements_files`, `requirement_id_pattern`, `changelog_files`,
`test_globs`, `exclude_test_globs`, `test_suites_file`, `failure_ledger`,
`ci_files`, `rules_dir`, `playbook_dirs`, `checklist_dirs`, `max_commits`,
`tooling_paths`. Unknown keys and wrongly typed values are reported, not
silently used.

## Graph Layers

The project graph (`.ai/project-graph.json`, generated and git-ignored) carries a
`layer` on every node and edge that is not in the default `code` layer:

- `code`: the project's modules, classes, functions, tables and their
  `calls`/`imports`/`defines` relations.
- `governance`: `requirement` and `changelog` nodes; `touches`, `records`,
  `mentions` edges.
- `history`: `commit` nodes; `delivers`, `modifies`, `logged_in`, `follows` edges.
- `assurance`: the project's test nodes, `suite` and `failure` nodes; `verifies`,
  `contains`, `covers`, `affects`, `arose_in`, `guards`, `fixed_by`, `recurs` edges.
- `workspace`: `rulepack`, `rule`, `playbook` and `checklist` nodes plus
  OmniEngineering's own code (`tooling_paths`); `defines` and `prevented_by` edges.

Non-source files that a requirement, changelog entry or failure references
become `file` nodes so the links have somewhere to land.

## Rulepack Record

Each companion rulepack has stable metadata and a list of rules:

- `rulepack_id`
- `version`
- `title`
- `purpose`
- `applies_to`
- `rules`

Each rule requires:

- `id`
- `severity`
- `statement`

Rules can also include scope tags and lightweight validation hints.

## Schema Strategy

The schema files document expected structures for requirements and rulepacks.
The current doctor implementation performs lightweight checks with Python's
standard library rather than a full JSON Schema engine. This keeps the CLI
portable while still catching common drift.

## Data Governance

OmniEngineering does not store secrets or runtime application data. The
`.ai/.ignore` file documents prompt-level exclusions for sensitive paths such as
environment files, keys, logs, databases, caches, dependency folders, and build
artifacts.

The repository permissions and hosting platform remain responsible for actual
access control.
