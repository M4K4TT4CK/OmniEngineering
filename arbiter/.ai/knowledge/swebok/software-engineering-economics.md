# Software Engineering Economics

This knowledge pack adapts common software engineering economics practice for
OmniContext. Use it when a task involves cost, value, tradeoffs, estimation,
technical debt, prioritization, buy/build decisions, or release timing.

## Purpose

Software engineering economics helps engineers reason about value, cost, risk,
time, uncertainty, and tradeoffs. It keeps technical decisions connected to
delivery outcomes and stakeholder value.

In OmniContext, economics guidance should help an LLM or human engineer:

- Identify economic drivers behind a change.
- Compare alternatives by cost, benefit, risk, and reversibility.
- Avoid expensive over-engineering.
- Make technical debt visible.
- Explain tradeoffs honestly.

## Economic Concerns

| Concern | Question |
| --- | --- |
| Value | What user, business, operational, or learning value does this create? |
| Cost | What effort, complexity, runtime cost, maintenance cost, or opportunity cost is introduced? |
| Risk | What uncertainty could change the expected outcome? |
| Time | Does timing affect value, cost, or risk? |
| Reversibility | Can the decision be undone cheaply? |
| Technical debt | Does this defer necessary work, and is the debt intentional? |

## Estimation

Use estimates as ranges, not guarantees, when uncertainty is high. Name
assumptions and identify what would reduce uncertainty.

For LLM-assisted work, estimate relative risk and scope when exact time or cost
is not knowable.

## Tradeoff Analysis

Prefer lightweight tradeoff tables for meaningful choices:

| Option | Benefit | Cost | Risk | Reversibility |
| --- | --- | --- | --- | --- |

The best engineering option is not always the most technically elegant one. It
is the option that best satisfies the requirement under current constraints.

## LLM Operating Rules

When economics matters:

1. Read `.ai/knowledge/swebok/software-engineering-economics.md`.
2. Use `.ai/knowledge/swebok/economics-decision-checklist.md`.
3. State value, cost, risk, and reversibility for major alternatives.
4. Do not invent precise estimates without evidence.
5. Record intentional technical debt and follow-up conditions.
