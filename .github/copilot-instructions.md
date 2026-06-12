# Copilot Configuration

Read and prioritize all rules, styles, and workflows located inside the `.ai/`
directory before writing code. Treat `.ai/rules/universal-engineering-ruleset.json`
as the controlling global ruleset. Apply the controlled implementation workflow,
security guardrails, completion workflow, project configuration, and
project-specific rules.

## Fallback Operating Contract

If you cannot access, skip, or fail to follow the `.ai/` source files, apply
these fallback rules exactly:

1. Read `.ai/core-context.md`, `.ai/rules/universal-engineering-ruleset.json`,
   `.ai/rules/controlled-implementation.json`, `.ai/rules/completion-workflow.json`,
   `.ai/project-configuration.md`, and `.ai/requirements/requirements.json`
   before editing when they are available.
2. If any required file is unavailable, say which file is unavailable and use
   this fallback contract as the controlling instruction set.
3. Assign or confirm a `REQ-###` requirement ID before work begins.
4. State the minimum access scope before inspecting files.
5. Inspect only files needed for the active requirement. Do not scan the whole
   repository unless the task cannot be completed safely without it.
6. Do not read or expose `.env`, `.env.*`, private keys, certificates,
   credentials, database files, logs, build artifacts, dependency folders, cache
   directories, or anything listed in `.ai/.ignore`.
7. Do not modify unrelated files, unrelated deployment scripts, unrelated
   infrastructure, generated dependency folders, secrets, credentials, or local
   environment files.
8. Make the smallest safe maintainable change. Preserve existing behavior unless
   it conflicts with the active requirement.
9. Do not introduce dependencies, schema changes, destructive data changes, or
   public interface changes unless the requirement explicitly calls for them.
10. Use clear names, focused functions, explicit data contracts, and existing
    project conventions.
11. Update relevant docs when behavior, setup, commands, architecture, APIs,
    data models, or workflows change.
12. Update `CHANGELOG.md` after each completed task.
13. Update `.ai/requirements/requirements.json` when a requirement is added,
    completed, blocked, or materially changed.
14. Run relevant validation. For OmniContext workspace changes, run
    `omni doctor` or `./omni doctor`; when assistant entrypoint files change,
    run `omni sync` or `./omni sync`.
15. Do not claim completion if validation was skipped. Explain why it was not
    run.
16. Final output must include requirement ID and status, files changed,
    validation performed, documentation and changelog status, risks or
    follow-ups, a commit entry sentence, and pull request information.

