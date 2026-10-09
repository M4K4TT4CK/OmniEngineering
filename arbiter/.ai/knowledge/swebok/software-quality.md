# Software Quality

This knowledge pack adapts common software quality practice for OmniContext.
Use it when defining, assessing, reviewing, improving, or reporting software
quality.

## Purpose

Software quality concerns how well software satisfies requirements and how well
its structure supports reliable, secure, maintainable, usable, and valuable
operation. Quality is both product-oriented and process-oriented.

In OmniContext, quality guidance should help an LLM or human engineer:

- Identify quality attributes relevant to a task.
- Select evidence that quality has been preserved or improved.
- Distinguish functional correctness from structural quality.
- Avoid quality claims without validation.
- Feed quality findings back into requirements, design, tests, and process.

## Quality Perspectives

| Perspective | Question |
| --- | --- |
| Functional quality | Does the software do the right thing for the requirement? |
| Structural quality | Is the internal structure maintainable, testable, secure, and robust? |
| Quality in use | Does the software support real users and operations effectively? |
| Process quality | Does the workflow produce trustworthy results repeatedly? |

## Quality Attributes

Common attributes include:

- Correctness.
- Reliability.
- Availability.
- Security.
- Performance.
- Usability.
- Maintainability.
- Testability.
- Portability.
- Compatibility.
- Observability.

Define attributes in measurable or reviewable terms. Avoid vague claims such as
"robust" or "simple" without evidence.

## Quality Assurance And Control

Quality assurance focuses on whether the process prevents defects. Quality
control focuses on whether the product/artifact meets expectations. Both matter.

Examples:

- Assurance: playbooks, checklists, reviews, coding standards, CI policy.
- Control: tests, static checks, inspections, audits, manual verification.

## LLM Operating Rules

When quality matters:

1. Read `.ai/knowledge/swebok/software-quality.md`.
2. Use `.ai/knowledge/swebok/quality-attribute-checklist.md`.
3. State which quality attributes are affected.
4. Select validation evidence for each important attribute.
5. Avoid claiming quality improvement without evidence.
