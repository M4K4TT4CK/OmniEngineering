# Assistant Workflow

## Workflow Summary

Every AI-assisted change should move through a controlled lifecycle:

1. Confirm or create a requirement ID.
2. Identify the minimum access scope.
3. Inspect only the relevant files.
4. Implement the smallest safe change.
5. Update documentation and traceability records.
6. Run validation.
7. Report completion with status, risks, and PR information.

See [Assistant Lifecycle](diagrams/assistant-lifecycle.svg) for the visual flow.

## Startup

The assistant should read the relevant `.ai/` files before editing:

- `.ai/core-context.md`
- `.ai/project-configuration.md`
- `.ai/requirements/requirements.json`
- `.ai/rules/universal-engineering-ruleset.json`
- Relevant companion rulepacks under `.ai/rules/`

If the assistant cannot access those files, it should apply the fallback
contract in `.ai/entrypoints/fallback-contract.md`.

## Requirement Handling

Every task should map to a requirement ID. If the user does not provide one, the
assistant should create the next sequential ID using the configured prefix.

The requirement record should define:

- The problem or change.
- The minimum access scope.
- Acceptance criteria.
- Required validation.
- Required documentation.
- Known risks or assumptions.

## Controlled Implementation

The assistant should make the smallest safe maintainable change. It should not
perform broad refactors, dependency changes, schema changes, destructive data
changes, or public interface changes unless the requirement explicitly calls for
them.

## Completion

Completion requires more than code edits. The assistant should update affected
docs, update the changelog, update requirement status, and run relevant
validation before claiming success.

The final report should include:

- Requirement ID and status.
- Files changed.
- Validation performed.
- Documentation and changelog status.
- Risks or follow-ups.
- Commit entry sentence.
- Pull request information.
