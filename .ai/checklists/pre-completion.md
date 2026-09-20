# Pre-Completion Checklist

- Requirement status is known.
- Intended files changed only.
- Construction quality checklist considered for code/config changes.
- Testing quality checklist considered for validation-heavy changes.
- Maintenance impact checklist considered for released or depended-on behavior.
- SCM checklist considered for dependency, build, generated, release, or
  configuration artifact changes.
- Quality attribute checklist considered for quality-sensitive claims.
- Professional practice checklist considered before final reporting.
- Economics decision checklist considered for major tradeoff claims.
- Computing, mathematical, or engineering foundations checklist considered for
  relevant technical assumptions.
- Tests or validation run, or skipped with reason.
- New test frameworks, test directories or check scripts are registered as test
  suites (`omni test detect`); `omni test check` passes.
- Every defect, failed check or regression fixed in this task is in the failure
  ledger with its root cause, regression test (or reason), and prevention;
  `omni failure check` passes.
- Any rule, playbook or checklist that should have prevented a failure was
  changed in this task.
- Docs updated when behavior or workflow changed.
- Changelog updated when public release notes are desired.
- Local-only notes remain ignored.
- Risks and follow-ups are stated.
- Final response includes commit sentence and PR information when applicable.
