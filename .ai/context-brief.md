# OmniEngineering Context Brief

Use this file as the first low-token orientation layer. Do not read the entire
workspace by default.

## Default Loading Rule

For most tasks, load only:

1. `.ai/context-brief.md`
2. `.ai/project-configuration.md`
3. `.ai/project-map.md`
4. `.ai/requirements/requirements.json`
5. The one playbook or checklist that matches the task

Then inspect only the project files named by the active requirement or prompt.

## Escalation Rule

Read more context only when the task needs it:

| Need | Read |
| --- | --- |
| Implementation work | `.ai/playbooks/implementation.md` |
| Review work | `.ai/playbooks/review.md` |
| Test planning or fixes | `.ai/playbooks/testing.md` |
| Refactoring | `.ai/playbooks/refactoring.md` |
| Release readiness | `.ai/playbooks/release.md` |
| Security-sensitive change | `.ai/checklists/security.md` |
| Completion gate | `.ai/checklists/pre-completion.md` |
| Requirements quality | `.ai/knowledge/swebok/requirements-quality-checklist.md` |
| Architecture or design | `.ai/knowledge/swebok/design-quality-checklist.md` |
| Full policy uncertainty | `.ai/rules/universal-engineering-ruleset.json` |

## Token Rules

- Read indexes before deep files.
- Prefer `.ai/project-map.md` over broad directory traversal.
- Prefer one relevant playbook over all playbooks.
- Prefer one relevant checklist over all checklists.
- Prefer one SWEBOK checklist over full SWEBOK knowledge packs.
- Do not load templates unless creating that artifact.
- Do not load design docs unless changing the workspace design or architecture.
- Compact or restart the chat when switching tasks.

## Completion Minimum

Before claiming completion, confirm:

- Requirement ID is known or assigned.
- Minimum access scope was followed.
- Relevant validation was run or explicitly marked not applicable.
- Docs or changelog impact was handled according to project policy.
- Final response includes remaining risk or follow-up, if any.
