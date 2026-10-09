# Software Engineering Models And Methods

This knowledge pack adapts common software engineering models and methods
practice for OmniContext. Use it when selecting a modeling approach, development
method, notation, or analysis technique.

## Purpose

Models and methods give engineers structured ways to understand, analyze,
design, verify, and communicate software. A model is a simplified representation
of something important. A method is a repeatable way of doing work.

In OmniContext, models and methods should help an LLM or human engineer:

- Choose useful representations without over-modeling.
- Make assumptions explicit.
- Analyze behavior, structure, data, risk, or workflow.
- Communicate design and requirements clearly.
- Connect models to implementation and validation.

## Common Model Types

| Model Type | Use |
| --- | --- |
| Context model | Show system boundary and external actors/systems. |
| Data model | Show entities, relationships, schemas, or data lifecycle. |
| State model | Show allowed states and transitions. |
| Flow model | Show control, data, event, or user flow. |
| Interface model | Show API, message, file, UI, or protocol contracts. |
| Deployment model | Show runtime nodes, services, environments, and dependencies. |
| Risk model | Show hazards, threats, failure modes, or operational risks. |

## Methods

Methods may include iterative development, test-first work, use-case analysis,
domain modeling, model-based design, prototyping, design reviews, static
analysis, formal specification for critical pieces, and risk-based validation.

Pick the method that reduces uncertainty for the task. Do not add method
ceremony when a smaller representation is enough.

## Model Quality

Good models are:

- Purposeful.
- Traceable to requirements or risks.
- Consistent with current code and docs.
- As simple as possible.
- Precise enough to support decisions.
- Updated or retired when stale.

## LLM Operating Rules

When modeling or selecting a method:

1. Read `.ai/knowledge/swebok/software-engineering-models-and-methods.md`.
2. Use `.ai/knowledge/swebok/model-method-selection-checklist.md`.
3. State the model purpose.
4. Keep the model close to the decision it supports.
5. Link models to requirements, design, tests, or risks.
6. Do not create diagrams or abstractions that nobody will maintain.
