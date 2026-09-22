---
name: infrastructure-audit
description: Review provider-neutral infrastructure, IaC, CI/CD, identity, and deployment safety with a repeatable evidence checklist instead of provider assumptions.
---

# Infrastructure audit

Use before judging infrastructure, deployment, or CI/CD changes as safe, correct, or complete.

1. Identify the actual platform from repository evidence -- provider blocks, manifests, CI runner configuration -- never assume AWS, Azure, GCP, or Kubernetes by default.
2. Establish the account, subscription, project, cluster, region, environment, and branch a change would apply to before evaluating any external action.
3. Trace identity paths: which principal executes each step, what it is granted, whether the grant is scoped to the resource and action needed, and whether it is broader than the prior configuration.
4. Read IaC as it will actually apply -- resolved variables, expanded loops and `for_each`, and referenced modules -- not the top-level file in isolation. Treat an unresolved or dynamic value as unverified, not passing.
5. Check CI/CD trust: which events trigger a pipeline, what secrets or tokens it can reach, whether a fork or external contributor's change can influence a privileged step, and whether third-party actions or images are pinned.
6. Evaluate resilience and rollback: what happens on partial failure, whether the change is reversible, and what the rollback path actually is -- "revert the commit" is not a rollback for a migration or another one-way state transition.
7. Check observability: whether the change is visible after deployment through metrics, logs, or alerts, and whether a regression would be caught before a user reports it.
8. Note cost-relevant changes -- new managed services, scaling policy changes, cross-region traffic -- as a follow-up, not a blocker, unless the acceptance criteria include cost.

For provider- and tool-specific checks, read [references/provider-checks.md](references/provider-checks.md) only for the provider(s) present in the repository.

Report the inferred platform, the principals and grants reviewed, how IaC was resolved before evaluation, the CI/CD trust boundaries checked, the rollback path, observability gaps, and any configuration left unresolved or unverifiable. Do not approve solely because a plan or lint tool produced no errors -- an unrecognized resource type or an unresolved variable is a gap, not a pass.
