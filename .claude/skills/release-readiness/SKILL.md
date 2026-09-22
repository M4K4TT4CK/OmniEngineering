---
name: release-readiness
description: Produce an evidence-based merge or release decision after implementation, including acceptance criteria, testing, security, compatibility, deployment, rollback, and residual risk.
---

# Release readiness

Use as an independent audit after implementation. Remain read-only.

Build an evidence map from each acceptance criterion to code, tests, command output, or an explicit gap. Inspect the complete diff and relevant surrounding paths. Confirm that failures, skipped tests, scanner suppressions, new dependencies, configuration changes, migrations, external contracts, observability, rollout, and rollback are accounted for.

Require stronger evidence for security-sensitive, stateful, cross-service, infrastructure, authentication, authorization, cryptographic, or irreversible changes. Passing unit tests alone is insufficient.

Return:

- Acceptance-criteria evidence map
- Blocking findings
- Non-blocking follow-ups
- Verification performed and missing
- Deployment and rollback readiness
- Residual risk and confidence
- Decision: Verified within stated scope, Conditionally acceptable, Changes required, or Blocked
