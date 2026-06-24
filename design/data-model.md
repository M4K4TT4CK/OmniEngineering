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
