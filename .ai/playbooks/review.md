# Review Playbook

Use this when asked to review code, docs, architecture, or an implementation
plan.

## Review Priorities

1. Bugs and behavioral regressions.
2. Security or privacy issues.
3. Missing validation or test coverage.
4. Requirement ambiguity, missing acceptance criteria, or broken traceability.
5. Design problems such as unclear ownership, unsafe coupling, or untestable
   structure.
6. Construction problems such as hidden behavior changes, weak error handling,
   or missing validation.
7. Testing problems such as weak oracle, missing regression coverage, or
   unreported skipped checks.
8. Maintenance problems such as incomplete impact analysis or compatibility
   risk.
9. Quality problems such as unsupported quality claims or missing quality
   evidence.
10. Professional-practice problems such as overstated certainty, hidden risk,
   or skipped validation presented as success.
11. Economic problems such as unjustified complexity, unrecorded technical debt,
   or ignored opportunity cost.
12. Computing, math, or engineering-foundation problems such as invalid
   assumptions, unsupported metrics, or unexamined failure modes.
13. Data loss, migration, or compatibility risk.
14. Maintainability problems that are likely to cause defects.

## Review Format

- Lead with findings ordered by severity.
- Include file and line references when available.
- Keep summaries brief and secondary.
- State when no issues are found.
- Call out residual test gaps.

## Non-Goals

- Do not rewrite code during review unless the user asks for fixes.
- Do not treat preference-only style comments as high-severity findings.
