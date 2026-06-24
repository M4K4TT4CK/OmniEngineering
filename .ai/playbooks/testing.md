# Testing Playbook

Use this to decide what validation is required and how to report it.

## Test Selection

- Use `.ai/knowledge/swebok/software-testing.md` for testing strategy.
- Use `.ai/knowledge/swebok/testing-quality-checklist.md` before accepting
  validation as sufficient.
- Use `.ai/knowledge/swebok/software-quality.md` when validation supports a
  quality claim.
- Use computing, mathematical, or engineering foundations checklists when tests
  rely on algorithms, quantitative claims, runtime behavior, or failure modes.
- Run the narrowest test that proves the change.
- Add broader tests for shared behavior, public interfaces, data handling, or
  cross-module contracts.
- For construction changes, use the construction quality checklist to confirm
  developer-level verification is sufficient.
- Run lint, typecheck, build, or doctor when configuration, docs, generated
  files, or workspace behavior changes.

## Reporting

Report each validation command with one of:

- `passed`
- `failed`
- `not run`

When a check is not run, explain why and identify the residual risk.

## Default OmniContext Checks

For this workspace, prefer:

```bash
./omni doctor
./omni validate
```
