# Software Design

This knowledge pack adapts common software design practice for OmniContext. Use
it when a task changes structure, interfaces, modules, data flow, quality
attributes, or important implementation strategy.

## Purpose

Software design translates requirements and constraints into a structure that
can be built, tested, maintained, and evolved. Design work should explain the
shape of the solution before or during construction, especially when the change
crosses module boundaries or affects public behavior.

In OmniContext, design guidance should help an LLM or human engineer determine:

- What responsibilities belong where.
- What interfaces and contracts are required.
- What data flows through the system.
- What quality attributes influence the solution.
- What tradeoffs and constraints were accepted.
- What validation will prove the design is fit for purpose.

## Design Activities

### Architectural And Detailed Design

Architectural design identifies major components, dependencies, external
interfaces, deployment concerns, and quality-attribute tradeoffs. Detailed
design defines classes, functions, data structures, API shapes, error paths,
state transitions, and local algorithms.

Use architectural design when a change affects system boundaries. Use detailed
design when a change affects implementation structure inside a bounded area.

### Decomposition

Break the system into cohesive units with clear responsibilities. Prefer
modules that are easy to name, test, replace, and reason about.

Good decomposition:

- Groups related behavior.
- Minimizes unnecessary coupling.
- Exposes stable contracts.
- Hides volatile implementation detail.
- Keeps data ownership clear.

### Interface Design

Interfaces include APIs, function signatures, events, file formats, user
interfaces, database boundaries, command-line contracts, and integration
points.

For every changed interface, identify:

- Inputs and outputs.
- Validation rules.
- Error behavior.
- Compatibility expectations.
- Versioning or migration concerns.
- Test or contract coverage.

### Data And State Design

State is often where software gets complicated. Identify the source of truth,
allowed transitions, persistence rules, consistency boundaries, and failure
modes.

Use state machines, tables, schemas, or examples when prose is not precise
enough.

### Quality Attribute Design

Design must account for qualities such as security, reliability, usability,
performance, maintainability, portability, observability, and testability.

Make quality attributes concrete. "Fast" is weak; "p95 response under 200 ms for
cached reads under expected load" is better.

## Design Principles

- Prefer simple, explicit structures over clever structures.
- Keep high-level policy separate from low-level mechanism.
- Use dependency direction intentionally.
- Make invalid states hard to represent where practical.
- Design for testability and observability.
- Preserve existing architecture unless the requirement justifies change.
- Document non-obvious tradeoffs.
- Avoid speculative generality.

## Design Smells

Flag and improve designs that show:

- Unclear ownership.
- Cyclic dependencies.
- Hidden global state.
- Large modules with mixed responsibilities.
- Public interfaces that expose internal detail.
- Untestable logic.
- Error handling as an afterthought.
- Data contracts described only in examples.
- Design decisions made without requirement traceability.

## LLM Operating Rules

When working on design:

1. Confirm the active requirement.
2. Read relevant requirements and quality constraints.
3. Read `.ai/knowledge/swebok/software-design.md`.
4. Use `.ai/knowledge/swebok/design-quality-checklist.md` for design review.
5. Use `.ai/knowledge/swebok/design-brief-template.md` for larger changes.
6. Keep design notes separate from implementation unless the task is small.
7. Do not introduce architecture changes without acceptance criteria and
   validation evidence.
