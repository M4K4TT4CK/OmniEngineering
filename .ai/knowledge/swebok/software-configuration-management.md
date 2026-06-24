# Software Configuration Management

This knowledge pack adapts common software configuration management practice for
OmniContext. Use it when a task changes versioned artifacts, build inputs,
release baselines, dependencies, generated files, environments, or configuration
that affects reproducibility.

## Purpose

Software configuration management controls the identity, integrity, traceability,
and change history of software artifacts. It helps teams know what changed, why
it changed, what version is valid, and how to reproduce or recover a baseline.

In OmniContext, SCM guidance should help an LLM or human engineer:

- Identify configuration items.
- Preserve baselines and traceability.
- Control changes to versioned artifacts.
- Avoid committing local or generated noise.
- Keep builds and releases reproducible.
- Report configuration risk honestly.

## Configuration Items

Configuration items can include:

- Source code.
- Requirements.
- Rulepacks.
- Schemas.
- Build scripts.
- Dependency manifests and lockfiles.
- Environment templates.
- Database migration files.
- Deployment manifests.
- Generated artifacts intended for source control.
- Documentation and release notes.
- Test fixtures and golden files.

Do not assume every generated file should be tracked. Track generated artifacts
only when the project convention or release process requires it.

## Baselines

A baseline is an identified, stable version of a set of configuration items. In
simple repositories, a git commit, tag, release branch, or package version may
serve as the baseline.

Before changing a baseline-sensitive artifact, identify:

- Current source state.
- Target change.
- Validation needed to trust the new state.
- Rollback or recovery path.

## Change Control

Change control should match risk. Small doc changes may need only review.
Dependency, schema, migration, release, and deployment changes need explicit
impact analysis and validation.

For LLM-assisted work:

- Do not modify lockfiles without understanding dependency impact.
- Do not modify generated files unless required.
- Do not stage or report ignored local notes as public artifacts.
- Do not rewrite history or reset changes without explicit user approval.
- Keep requirement IDs connected to changed configuration items.

## Status Accounting

Status accounting records what changed and why. In OmniContext this includes:

- Requirement status.
- Changed files.
- Validation commands.
- Changelog or release notes when public-facing.
- Known risks and follow-ups.
- Commit sentence and PR information.

## Audits And Reviews

SCM review asks:

- Are all intended files included?
- Are unrelated files excluded?
- Are generated/local/secret files excluded?
- Can the build or workspace be reproduced?
- Are dependencies and lockfiles consistent?
- Do docs match the changed configuration?

## LLM Operating Rules

When touching configuration-managed artifacts:

1. Read `.ai/knowledge/swebok/software-configuration-management.md`.
2. Use `.ai/knowledge/swebok/scm-checklist.md`.
3. Identify whether the change affects a baseline, dependency, build, release,
   or deployment.
4. Preserve unrelated user changes.
5. Keep local Codex/session files ignored.
6. Report changed configuration items and validation evidence.
