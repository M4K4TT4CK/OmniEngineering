# Construction Quality Checklist

Use this checklist before reporting code or configuration construction complete.

## Scope

- Change maps to the active requirement.
- Files changed are within the intended scope.
- Unrelated refactors are absent or justified.
- User changes in the working tree are preserved.

## Code Quality

- Names are clear.
- Control flow is understandable.
- Error handling is explicit.
- Inputs are validated at trust boundaries.
- Public interfaces remain compatible or the change is documented.
- No placeholder or dead code remains.

## Integration

- Callers and downstream consumers are considered.
- Configuration and environment assumptions are documented.
- Data, schema, API, event, or file-format contracts are respected.
- Backward compatibility is handled when required.

## Verification

- Relevant tests or checks were run.
- New tests were added when behavior changed and automation is practical.
- Manual verification is documented when automation is unavailable.
- Failed or skipped validation is reported honestly.

## Release Hygiene

- Docs are updated when behavior, setup, commands, or workflows changed.
- Local-only files are ignored.
- Changelog updates follow project policy.
- Final report includes risks and follow-ups.
