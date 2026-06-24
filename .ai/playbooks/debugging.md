# Debugging Playbook

Use this when investigating failures, confusing behavior, or validation errors.

## Sequence

1. Reproduce or identify the failure signal.
2. Read the smallest relevant code path.
3. Form one or two testable hypotheses.
4. Inspect logs or traces only when permitted and relevant.
5. Make one targeted change.
6. Re-run the failing check.
7. Add regression evidence when the defect is confirmed and automation is
   practical.

## Guardrails

- Do not broad-scan unrelated files unless the failure path is unknown.
- Do not mask failures by weakening validation.
- Do not delete data, caches, or lock files without explicit approval.
- Preserve the original error in the final report.
