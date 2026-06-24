# Software Engineering Management

This knowledge pack adapts common software engineering management practice for
OmniContext. Use it when work involves planning, estimation, risk, coordination,
tracking, quality gates, release readiness, or multi-step delivery.

## Purpose

Software engineering management plans, coordinates, monitors, and controls
engineering work so that software is delivered with predictable scope, quality,
risk, and traceability.

In OmniContext, management guidance should help an LLM or human engineer:

- Turn vague work into manageable requirements.
- Identify scope, risk, priority, and dependencies.
- Track progress and blockers.
- Coordinate validation and documentation.
- Prepare credible handoffs, commits, and PRs.
- Avoid claiming completion without evidence.

## Management Concerns

### Initiation And Scope

Clarify objective, stakeholders, affected systems, success criteria, and
constraints. If the request is too broad, split it into smaller requirements.

### Planning

Planning should define:

- Requirement IDs.
- Work breakdown.
- Minimum access scope.
- Dependencies.
- Risks.
- Validation strategy.
- Documentation needs.
- Completion criteria.

### Measurement And Tracking

Track meaningful signals:

- Requirements completed, blocked, or deferred.
- Validation passed, failed, or skipped.
- Defects found and fixed.
- Scope changes.
- Risk status.
- Open questions and decisions.

Avoid fake precision. Prefer honest status over optimistic certainty.

### Risk Management

Common software engineering risks include unclear requirements, weak tests,
data loss, integration uncertainty, dependency changes, security exposure,
schedule pressure, and undocumented behavior.

Every high-risk task should name mitigations and residual risk.

### Quality And Completion Control

Management is responsible for completion discipline:

- Requirement status is updated when appropriate.
- Validation evidence exists.
- Documentation is current.
- Release or handoff notes are clear.
- Known risks are not hidden.

## LLM Operating Rules

When managing engineering work:

1. Read `.ai/knowledge/swebok/software-engineering-management.md`.
2. Use `.ai/knowledge/swebok/engineering-management-checklist.md`.
3. Break broad requests into requirements.
4. Track blockers and assumptions explicitly.
5. Use handoff packets for model, tool, or human transitions.
6. Report status without overstating certainty.
