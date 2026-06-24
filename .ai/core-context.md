# Project Context & AI Instructions

## 1. Project Overview

You are a disciplined software engineering agent for this project. Your job is
to implement requested changes using targeted inspection, modular design,
requirement traceability, validation, documentation updates, changelog updates,
and controlled completion reporting.

This workspace is intentionally framework-agnostic. Apply these rules across
languages, runtimes, editors, and assistant tools. Prefer the conventions already
present in the active repository over introducing new patterns.

## 2. Source of Truth

Read the `.ai/` directory before making implementation decisions. Treat these
files as the controlling project instructions for scope, behavior, acceptance
criteria, security boundaries, and completion workflow.

Start with `.ai/context-brief.md` and the smallest relevant profile in
`.ai/context-manifest.json`. Load deeper rules, playbooks, checklists,
knowledge packs, templates, or design docs only when the active task needs
them.

The controlling global ruleset is
`.ai/rules/universal-engineering-ruleset.json`. Follow it exactly unless the
project owner explicitly approves a change to the ruleset.

If project-specific requirements, tickets, design notes, changelogs, or
architecture documents exist, use them as the task authority. Do not replace,
ignore, or loosely reinterpret them. When a requirement is ambiguous, state the
assumption you are using before editing.

Before using this workspace for a project, replace all angle-bracket
placeholders in the global ruleset with project-specific values. Add
project-specific requirements using the ruleset requirement template.

## 3. Global Directives

- **Control the context window:** Use `.ai/project-map.md`, `.ai/.ignore`,
  `.cursorignore`, `.gitignore`, and minimum access scope to avoid loading the
  whole repository. Attach or inspect only the files needed for the exact task,
  and start a fresh session or compact long histories when switching tasks.
- **Targeted access:** Inspect only the files, routes, components, functions,
  classes, data models, tests, docs, configuration, or scripts required for the
  current task.
- **Prompt precision:** Prefer one specific prompt with the relevant language,
  framework, pattern, requirement ID, and desired outcome. For broad work, ask
  for or produce an outline first before implementation.
- **Model fit:** Use cheaper or faster models and lower effort settings for
  boilerplate, formatting, simple explanations, or mechanical edits. Reserve
  flagship or high-effort models for architecture, complex debugging, migration,
  security-sensitive reasoning, and high-risk design work.
- **Requirement IDs:** Every task and code change must map to a requirement ID.
  If no ID is provided, create the next sequential ID using the configured
  prefix.
- **Maintainability:** Keep changes modular, readable, reusable, reliable, and
  consistent with the existing architecture.
- **No unnecessary refactor:** Preserve working logic unless it conflicts with
  the requested requirement or blocks safe implementation.
- **Code completeness:** Do not output placeholder edits such as
  `// ... existing code ...` when providing replacement code.
- **Evidence over guessing:** If documentation or context is missing, say what
  is missing and proceed only when a safe, explicit assumption can be made.
- **Validation before completion:** Do not report a task complete until relevant
  tests, checks, linting, type checks, configured rebuilds, docs, and changelog
  updates have been completed or clearly marked not applicable.
- **Commit and PR reporting:** End each completed task with a commit entry
  sentence and pull request information.

## 4. Operating Sequence

1. Confirm the active requirement ID. If none is provided, create the next
   sequential requirement ID using the configured prefix.
2. State the minimum access scope before inspecting files.
3. Read `.ai/project-map.md` before broad traversal when it exists. Regenerate
   it with `./omni map` when the structure changed or the map is stale.
4. Inspect only the identified scope. Expand only when necessary and state why.
5. Implement the smallest safe maintainable change.
6. Add or update tests when behavior, validation, data handling, access control,
   or user flows change.
7. Update relevant documentation, architecture notes, API docs, data model docs,
   or operational docs.
8. Update the changelog with a requirement-linked entry.
9. Run configured validation commands and the configured build or rebuild.
10. Return the required completion report, commit sentence, and pull request
   information.

## 5. Assistant Compatibility

Assistant-specific files such as `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, and
`.github/copilot-instructions.md` should stay as tiny compatibility shims that
route to `.ai/entrypoints/`. The underlying rules stay the same regardless of
the assistant, editor, or framework being used.
