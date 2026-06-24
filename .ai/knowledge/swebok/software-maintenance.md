# Software Maintenance

This knowledge pack adapts common software maintenance practice for
OmniContext. Use it when modifying existing software after it has been built,
released, integrated, or relied upon by users or other systems.

## Purpose

Software maintenance preserves and improves useful software over time. It
includes fixing defects, adapting to changed environments, improving behavior or
quality, preventing future problems, and retiring obsolete behavior safely.

In OmniContext, maintenance guidance should help an LLM or human engineer:

- Classify the maintenance change.
- Understand impact before editing.
- Preserve intended behavior.
- Avoid regressions.
- Keep documentation and traceability current.
- Plan compatibility, migration, and rollback where needed.

## Maintenance Categories

| Category | Meaning | Examples |
| --- | --- | --- |
| Corrective | Fix a discovered fault. | Bug fix, regression fix, incident patch. |
| Adaptive | Adjust to a changed environment. | Runtime upgrade, API change, platform change, dependency change. |
| Perfective | Improve behavior, performance, maintainability, or usability. | Refactor, speed improvement, UX polish, developer experience. |
| Preventive | Reduce future failure or maintenance cost. | Add tests, improve observability, deprecate unsafe path. |

Many tasks combine categories. Name the dominant category and any secondary
category.

## Maintenance Activities

### Impact Analysis

Before changing maintained software, identify:

- Affected requirements.
- Current behavior and users.
- Callers, dependents, and downstream consumers.
- Data or schema impact.
- Compatibility expectations.
- Operational and deployment impact.
- Test and rollback strategy.

### Change Implementation

Maintenance changes should be small, traceable, and compatible unless a breaking
change is explicitly approved.

Prefer:

- Regression tests for corrective fixes.
- Compatibility layers for adaptive changes.
- Measured evidence for performance or quality improvements.
- Documentation for behavior changes.
- Deprecation paths before removal.

### Maintenance Review

Review maintenance changes for unintended side effects. Existing behavior is a
source of requirements, even when not fully documented.

### Retirement And Deprecation

When removing behavior, document:

- What is removed.
- Who or what depends on it.
- Migration path.
- Timeline.
- Rollback or recovery option.

## Maintenance Risks

Maintenance work is risky when:

- Tests are missing.
- Existing behavior is undocumented.
- External consumers depend on implicit behavior.
- Data migrations are needed.
- Dependencies or platforms change.
- The change spans many modules.
- The original design intent is unclear.

## LLM Operating Rules

When performing maintenance:

1. Classify the maintenance category.
2. Read `.ai/knowledge/swebok/software-maintenance.md`.
3. Use `.ai/knowledge/swebok/maintenance-impact-checklist.md`.
4. Inspect current behavior before changing it.
5. Add regression evidence for bug fixes when practical.
6. Document compatibility, migration, and rollback concerns.
7. Report residual risk clearly.
