# Software Testing

This knowledge pack adapts common software testing practice for OmniContext.
Use it when selecting, designing, running, reviewing, or reporting validation.

## Purpose

Software testing evaluates software behavior and quality against expected
outcomes. Testing is not only a final gate; it is feedback throughout
requirements, design, construction, maintenance, and release.

In OmniContext, testing guidance should help an LLM or human engineer:

- Choose validation proportional to risk.
- Connect tests to requirements and acceptance criteria.
- Detect regressions early.
- Report evidence honestly.
- Improve testability when software is hard to verify.

## Testing Concepts

### Test Objectives

Testing can demonstrate expected behavior, reveal defects, reduce uncertainty,
protect against regressions, validate quality attributes, and provide release
confidence. A test does not prove absence of defects; it provides evidence
within a stated scope.

### Test Levels

| Level | Purpose |
| --- | --- |
| Unit | Verify isolated functions, classes, or modules. |
| Component | Verify a cohesive component with its internal collaborators. |
| Integration | Verify interactions across boundaries. |
| System | Verify end-to-end behavior of the whole system. |
| Acceptance | Verify stakeholder-facing acceptance criteria. |
| Regression | Verify previously working behavior still works. |

### Test Techniques

- Example-based tests for known scenarios.
- Boundary-value tests for edges and limits.
- Equivalence partitioning for representative input groups.
- State-transition tests for lifecycle or workflow behavior.
- Property or invariant tests for broad behavioral rules.
- Contract tests for APIs, schemas, events, or files.
- Exploratory/manual testing when automation is not practical.
- Static analysis, linting, type checks, and review as complementary checks.

### Test Oracles

A test oracle is how a test determines whether behavior is correct. Oracles may
come from requirements, examples, specifications, prior behavior, invariants,
schemas, snapshots, expert review, or external systems.

If no oracle exists, say so. Do not invent expected behavior silently.

### Testability

Testability improves when software is observable, controllable, deterministic,
modular, and documented. If a change is difficult to test, consider whether the
design should expose a better boundary, clearer contract, or more direct
feedback.

## Risk-Based Testing

Increase validation depth when a change affects:

- Money, safety, privacy, auth, or permissions.
- Data loss, migration, or persistence.
- Public APIs, schemas, events, or file formats.
- Shared libraries or common utilities.
- Deployment, configuration, or operations.
- Complex state, concurrency, time, randomness, or external integrations.

## Defect Handling

When a defect is found:

1. Capture the failure signal.
2. Identify the requirement or expected behavior.
3. Add or update a regression test when practical.
4. Fix the smallest responsible scope.
5. Re-run the failing check and relevant surrounding checks.
6. Report both defect cause and validation evidence.

## LLM Operating Rules

When testing:

1. Read `.ai/playbooks/testing.md`.
2. Read `.ai/knowledge/swebok/software-testing.md`.
3. Use `.ai/knowledge/swebok/testing-quality-checklist.md`.
4. Tie each important test to a requirement, risk, or changed behavior.
5. Prefer automated validation when practical.
6. Report skipped validation honestly and include residual risk.
