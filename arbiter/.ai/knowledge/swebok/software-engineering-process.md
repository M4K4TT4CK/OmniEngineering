# Software Engineering Process

This knowledge pack adapts common software engineering process practice for
OmniContext. Use it when defining, selecting, tailoring, evaluating, or improving
the way engineering work flows through a repository.

## Purpose

A software engineering process defines how work moves from need to delivered
software. It should make quality, traceability, validation, and handoff
repeatable without turning every task into bureaucracy.

In OmniContext, process guidance should help an LLM or human engineer:

- Choose the right workflow for the task.
- Tailor ceremony to risk.
- Preserve traceability across requirements, design, code, tests, and release.
- Improve the process based on observed failures.
- Keep work model-agnostic and tool-agnostic.

## Process Elements

| Element | Purpose |
| --- | --- |
| Activities | Work performed, such as planning, design, construction, testing, review, release, and maintenance. |
| Artifacts | Requirements, code, tests, docs, diagrams, configs, releases, and decisions. |
| Roles | Humans, models, reviewers, maintainers, stakeholders, or systems responsible for work. |
| Entry criteria | Conditions before an activity starts. |
| Exit criteria | Conditions before an activity is complete. |
| Measures | Evidence used to understand status, quality, and risk. |

## Process Tailoring

Use lightweight process for low-risk changes. Increase discipline when work
touches security, data, public interfaces, architecture, dependencies,
operations, or released behavior.

Tailoring questions:

- What is the active requirement?
- What is the smallest safe workflow?
- Which playbook applies?
- Which checklist is required before completion?
- What validation evidence is enough?
- What handoff information is needed?

## Process Improvement

Improve the process when repeated failures appear:

- Requirements remain ambiguous.
- Reviews find the same defect pattern.
- Validation is skipped or unreliable.
- Releases require manual rescue.
- Models repeatedly lose context.
- Handoffs omit important status.

## LLM Operating Rules

When process matters:

1. Read `.ai/knowledge/swebok/software-engineering-process.md`.
2. Use `.ai/knowledge/swebok/process-tailoring-checklist.md`.
3. Select only the playbooks needed for the task.
4. Avoid loading every process file by default.
5. Report process gaps as follow-ups when they affect delivery risk.
