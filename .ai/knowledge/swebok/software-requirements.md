# Software Requirements

This knowledge pack adapts common software requirements engineering practice
for OmniContext. Use it when creating, changing, reviewing, validating, or
tracing requirements.

## Purpose

Software requirements define the capabilities, constraints, qualities, and
external interactions a software system must satisfy. In OmniContext, a
requirement must be specific enough for an LLM or human engineer to determine:

- What problem or objective is being addressed.
- Who or what needs the capability.
- What behavior or constraint must hold.
- How acceptance will be judged.
- What validation evidence is required.
- What artifacts need to be updated.

## Requirements Activities

### Elicitation

Elicitation discovers requirements from stakeholders, existing systems,
documents, incidents, workflows, regulations, support tickets, analytics, and
technical constraints.

For LLM-assisted work:

- Identify stakeholders or affected roles.
- Extract explicit user goals.
- Identify implicit constraints and assumptions.
- Ask only the questions that block safe progress.
- Record unknowns as assumptions or risks.

### Analysis

Analysis turns raw needs into clear, feasible, consistent requirements.

Check for:

- Conflicts between requirements.
- Missing boundaries or actors.
- Ambiguous terms.
- Hidden design decisions.
- Overly broad scope.
- Feasibility, risk, and dependency concerns.
- Priority and sequencing.

### Specification

Specification records requirements in a durable form. In OmniContext, normal
task requirements belong in `.ai/requirements/requirements.json`. Larger
features may also use an SRS-style document under `design/` or project docs.

A strong requirement statement should include:

- Stable ID.
- Category.
- Title.
- Description.
- Priority.
- Status.
- Minimum access scope.
- Acceptance criteria.
- Validation required.
- Documentation required.
- Risk notes.

### Validation

Requirements validation checks whether the stated requirement is the right
requirement and whether it can be verified.

Validate requirements before implementation when:

- The task affects user-visible behavior.
- The task changes a public interface.
- The task changes data, security, reliability, performance, or deployment.
- Stakeholder intent is unclear.
- Acceptance criteria are subjective or not testable.

Validation methods can include review, examples, prototypes, walkthroughs,
acceptance tests, traceability checks, or comparison with existing behavior.

### Management

Requirements management keeps requirements current as the project changes.

For OmniContext:

- Add new requirements with a stable `REQ-###` ID.
- Update status when requirements are completed, blocked, deferred, or changed.
- Preserve traceability to changed files, docs, tests, and validation commands.
- Record assumptions and risks.
- Avoid silently changing requirement meaning during implementation.

## Requirement Types

| Type | Description | OmniContext Handling |
| --- | --- | --- |
| Functional | Capability or behavior the system must provide. | Acceptance criteria should describe observable behavior. |
| External interface | Interaction with users, APIs, hardware, services, files, or protocols. | Identify interface contracts and compatibility risks. |
| Quality attribute | Reliability, performance, security, maintainability, usability, portability, etc. | Define measurable or reviewable validation evidence. |
| Constraint | Limitation on technology, policy, environment, process, or design. | Capture in requirements, config, or rules as appropriate. |
| Data requirement | Data shape, lifecycle, privacy, retention, migration, or transformation. | Use data governance rules and validation. |
| Process requirement | Required workflow, review, documentation, or release behavior. | Encode in playbooks, checklists, or rulepacks. |

## Requirement Quality Criteria

Prefer requirements that are:

- Necessary.
- Unambiguous.
- Feasible.
- Verifiable.
- Traceable.
- Consistent.
- Bounded.
- Prioritized.
- Understandable to stakeholders.
- Independent of unnecessary implementation detail.

## Requirement Smells

Flag and improve requirements that contain:

- Vague words such as fast, easy, robust, seamless, simple, or user-friendly
  without measurable criteria.
- Universal claims such as always, never, all, or every without scope.
- Multiple requirements joined into one statement.
- Hidden implementation decisions.
- Missing actors or triggering conditions.
- Missing acceptance criteria.
- Missing validation evidence.
- Contradictions with existing rules, requirements, or architecture.

## LLM Operating Rules

When working with requirements:

1. Read `.ai/requirements/requirements.json`.
2. Use `.ai/knowledge/swebok/software-requirements.md` for requirement work.
3. Use `.ai/knowledge/swebok/requirements-quality-checklist.md` before
   marking a requirement ready.
4. Use `.ai/knowledge/swebok/srs-template.md` for larger requirement sets.
5. Do not treat a vague request as implementation-ready when it affects public
   behavior, data, security, or architecture.
6. Keep requirements about what must be true; move implementation approach into
   design notes unless the implementation is itself a constraint.
