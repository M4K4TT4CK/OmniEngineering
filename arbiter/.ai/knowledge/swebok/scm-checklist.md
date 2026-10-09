# SCM Checklist

Use this checklist before completing changes to configuration-managed artifacts.

## Identification

- Configuration items changed are identified.
- Generated files are intentional or excluded.
- Local/session-only files are ignored.
- Secrets and environment files are excluded.

## Change Control

- Requirement ID is linked to the change.
- Dependency, lockfile, schema, build, or deployment impact is understood.
- Baseline-sensitive changes have validation evidence.
- Destructive git operations were not used without approval.

## Reproducibility

- Build or validation commands are documented.
- Required generated artifacts can be reproduced or are intentionally tracked.
- Version or release impact is understood.
- Rollback or recovery path is known when needed.

## Status Accounting

- Changed files are reported.
- Validation status is reported.
- Docs/changelog policy is followed.
- Risks and follow-ups are listed.
