# Implementation Playbook

Use this when changing code, configuration, docs, tests, or assets.

## Sequence

1. Confirm the requirement ID.
2. Read `.ai/context-manifest.json`.
3. Read `.ai/project-map.md` to choose the smallest relevant project paths
   before traversing repository files. If the map is missing or stale, run
   `./omni map`.
4. Read only the task-relevant rules and playbooks.
5. For new or changed requirements, apply the SWEBOK requirements knowledge
   pack before editing.
6. For structural changes, apply the SWEBOK software design knowledge pack.
7. Apply the SWEBOK software construction knowledge pack while editing code.
8. For dependency, build, generated, release, or config changes, apply the SCM
   knowledge pack.
9. For algorithm, runtime, persistence, network, concurrency, or quantitative
   changes, apply the relevant foundations knowledge pack.
10. Inspect the minimum access scope.
11. Make the smallest maintainable change.
12. Update tests or validation fixtures when behavior changes.
13. Update docs when behavior, setup, architecture, or workflows change.
14. Run validation.
15. Report outcome honestly.

## Engineering Rules

- Do not rewrite unrelated code.
- Do not introduce dependencies unless required.
- Do not change public interfaces unless required.
- Preserve user changes in the working tree.
- Prefer structured parsers and APIs over ad hoc text manipulation.
- Keep generated or session-only notes out of public files unless asked.

## Completion Evidence

The final report should mention changed files, validation commands, skipped
checks with reasons, residual risks, and the commit sentence.
