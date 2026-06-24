# Product Design

## Product Summary

OmniEngineering is a repo-local software engineering workspace for AI-assisted
delivery. It gives a repository one shared operating model for requirements,
engineering rules, delivery workflows, validation, quality practice, knowledge
packs, and assistant context.

OmniContext is the context-governance layer inside OmniEngineering. It can be
read by Codex, Claude Code, Cursor, GitHub Copilot, Kiro-style workflows, local
models, DeepSeek-based workflows, OpenRouter-style model gateways, and future
tools without locking the team into one editor, model, or assistant vendor.

The product is the workspace pattern:

- `.ai/` holds the durable source of truth.
- Assistant entrypoint shims route each tool to `.ai/entrypoints/` and then
  back to the shared `.ai/` workspace.
- Structured JSON rulepacks make key policy inspectable.
- A context manifest and adapter prompts make context portable to tools that do
  not automatically read repo instruction files.
- Playbooks and checklists provide file-based engineering procedure without
  requiring an MCP server or hosted agent runtime.
- A small `omni` CLI validates that the workspace has not drifted.

## Primary Problem

Modern teams often use more than one AI coding assistant in the same repository.
Each assistant has its own native configuration file. If those files drift, the
same repository can receive inconsistent behavior, incomplete validation, missed
security boundaries, or conflicting completion standards.

OmniContext solves the assistant drift part of that problem by making
assistant-specific files routing hooks instead of independent policy documents.
OmniEngineering uses that stable context layer to support the broader software
engineering lifecycle.

## Target Users

- Individual developers who use multiple AI coding tools.
- Teams standardizing AI-assisted development practices across editors.
- Repository maintainers who need repeatable guardrails for AI changes.
- Technical leads who want requirement traceability and validation discipline.

## Jobs To Be Done

- Keep all assistants aligned to the same engineering rules.
- Make AI operating rules easy to audit and update.
- Require requirement IDs for traceable changes.
- Detect drift between entrypoint files and the `.ai/` source of truth.
- Provide a low-friction maintenance path without adding runtime dependencies.

## Product Principles

- **Repo-local first:** The system should travel with the repository.
- **Assistant-neutral:** The core rules should not depend on one vendor.
- **Boring entrypoints:** Tool-specific files should be thin and predictable.
- **Structured where it counts:** Rules, schemas, and requirements should be
  parseable when validation matters.
- **Human-readable always:** Design intent and usage should remain readable in
  Markdown.
- **No hidden service dependency:** The workspace must remain useful without a
  hosted backend.

## Non-Goals

- OmniEngineering is not a prompt marketplace.
- OmniEngineering is not a hosted policy server.
- OmniEngineering is not a replacement for CI, secret scanning, access control, or
  code review.
- OmniEngineering is not meant to generate an entire project architecture for every
  repo automatically.
- OmniEngineering does not force one assistant, editor, language, or framework.

## Success Criteria

- A new contributor can understand the pattern from the README and design docs.
- Any supported assistant shim routes to the same `.ai/` rules.
- `omni doctor` can identify missing required files and structural drift.
- Requirement-linked changes can be traced in `.ai/requirements/requirements.json`
  and `CHANGELOG.md`.
- Diagrams and design docs make the workspace explainable without requiring
  non-delivery brand collateral.
