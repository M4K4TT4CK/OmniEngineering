# Software Construction

This knowledge pack adapts common software construction practice for
OmniContext. Use it when writing, modifying, integrating, reviewing, or
validating source code and configuration.

## Purpose

Software construction is the hands-on creation and integration of working
software. It includes coding, configuration, developer testing, integration,
reuse, debugging, and local quality checks.

In OmniContext, construction guidance should help an LLM or human engineer:

- Minimize unnecessary complexity.
- Build for verification.
- Integrate changes safely.
- Reuse existing assets responsibly.
- Preserve behavior unless the requirement changes it.
- Produce evidence that the change works.

## Construction Fundamentals

### Minimize Complexity

Code should be clear before it is clever. Minimize complexity through readable
names, small units, local reasoning, straightforward control flow, and existing
project conventions.

Avoid:

- Deep nesting.
- Overly abstract helpers.
- Magic values without names.
- Logic duplicated across paths.
- Mixed responsibilities in one function or module.

### Anticipate Change

Anticipating change does not mean building speculative frameworks. It means
isolating likely variation points, keeping contracts explicit, and avoiding
choices that make foreseeable changes expensive.

### Construct For Verification

Write code so faults can be found quickly. Favor deterministic behavior,
testable boundaries, meaningful errors, observable state, and automated tests
where practical.

### Reuse Deliberately

Reuse existing functions, modules, tests, and patterns when they fit. Do not
reuse code that brings unwanted coupling, unclear ownership, stale behavior, or
security risk.

### Follow Standards

Use the repository's language, formatting, naming, typing, error handling,
testing, and review conventions. When no convention exists, choose the simplest
maintainable option and document the assumption if it matters.

## Construction Activities

### Coding

- Keep edits scoped to the requirement.
- Prefer small, composable units.
- Validate inputs at trust boundaries.
- Handle errors intentionally.
- Keep public interfaces stable unless explicitly changing them.
- Avoid placeholder code in completed changes.

### Integration

- Understand how the changed unit connects to callers, data stores, external
  services, jobs, events, or UI flows.
- Integrate in the smallest safe increment.
- Add scaffolding or compatibility layers only when they reduce real risk.
- Re-run the checks that exercise the integration boundary.

### Developer Testing

Construction includes developer-level validation such as unit tests,
integration tests, static checks, type checks, linting, and manual verification.

Choose tests based on risk:

- Unit tests for isolated logic.
- Integration tests for cross-boundary behavior.
- Regression tests for bug fixes.
- Contract tests for APIs, schemas, events, or file formats.
- Manual verification for UI or operational flows when automation is not
  available.

### Debugging

Debug by reproducing, isolating, hypothesizing, changing one thing, and
re-running the failing check. Do not weaken checks to make failures disappear.

## Construction Smells

Flag and improve construction that shows:

- Broad unrelated edits.
- Untested behavior changes.
- Silent error swallowing.
- Hidden dependency changes.
- Non-deterministic behavior without reason.
- Hard-coded environment assumptions.
- Data validation only in the UI.
- Security-sensitive logic without tests or review notes.
- Generated or local-only files mixed into public changes.

## LLM Operating Rules

When constructing software:

1. Confirm the active requirement.
2. Use `.ai/playbooks/implementation.md`.
3. Use `.ai/knowledge/swebok/software-construction.md` for coding guidance.
4. Use `.ai/knowledge/swebok/construction-quality-checklist.md` before
   completion.
5. Make the smallest safe change.
6. Run relevant validation.
7. Report skipped validation honestly.
