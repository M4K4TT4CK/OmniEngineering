# Validation And Operations

## Validation Goals

OmniEngineering validation is designed to detect practical drift in a repo-local
AI-assisted engineering workspace. It favors deterministic, dependency-free
checks over exhaustive semantic analysis.

## Required Checks

Run the doctor after changes to `.ai/`, assistant entrypoints, design docs,
README architecture references, CLI behavior, or requirement tracking:

```bash
./omni doctor
```

Use the alias when a workflow expects `validate`:

```bash
./omni validate
```

## Release Readiness

Before publishing or copying the workspace into another repository:

- Run `./omni doctor`.
- Confirm README architecture references render.
- Confirm design docs match current behavior.
- Confirm `CHANGELOG.md` includes the completed requirement.
- Confirm `.ai/requirements/requirements.json` has the latest status.
- Confirm project-specific placeholders are acceptable or intentionally left for
  downstream adopters.

## Operational Risks

| Risk | Mitigation |
| --- | --- |
| Assistant entrypoint drift | Run `omni sync` after fallback contract changes. |
| Invalid JSON | Run `omni doctor` after rulepack or requirement edits. |
| Stale docs | Update README, design docs, and changelog in the same requirement. |
| Overbroad assistant access | Keep `.ai/.ignore` current and require minimum access scope. |
| False sense of security | Treat OmniEngineering as prompt and workflow governance, not access control. |

## Maintenance Cadence

- Run `doctor` after every workspace change.
- Run `sync` after assistant entrypoint or fallback text changes.
- Review design docs when README positioning changes.
- Review schema files when rulepack or requirement shapes change.
