# Mathematical Foundations

This knowledge pack adapts mathematical foundations for OmniContext. Use it when
a task involves logic, measurement, probability, statistics, optimization,
formal reasoning, complexity, numerical behavior, or quantitative acceptance
criteria.

## Purpose

Mathematical foundations help engineers reason precisely about correctness,
uncertainty, scale, performance, reliability, risk, and measurement.

In OmniContext, mathematical guidance should help an LLM or human engineer:

- Make quantitative claims measurable.
- Use logical reasoning carefully.
- Avoid invalid statistics or misleading metrics.
- Understand algorithmic complexity.
- Identify numerical precision and boundary risks.

## Core Areas

| Area | Use |
| --- | --- |
| Logic | Preconditions, invariants, state rules, proof obligations. |
| Discrete math | Graphs, sets, relations, finite states, combinatorics. |
| Probability | Uncertainty, risk, randomized behavior, reliability. |
| Statistics | Measurement, sampling, confidence, trend analysis. |
| Complexity | Runtime and memory growth. |
| Optimization | Tradeoffs under constraints. |
| Numerical reasoning | Precision, overflow, rounding, tolerances. |

## Measurement Rules

- Define the metric before collecting data.
- State units.
- Identify sample size and source.
- Avoid averages when distribution shape matters.
- Avoid causal claims from correlation alone.
- Use tolerances for floating-point or approximate behavior.

## LLM Operating Rules

When math matters:

1. Read `.ai/knowledge/swebok/mathematical-foundations.md`.
2. Use `.ai/knowledge/swebok/mathematical-reasoning-checklist.md`.
3. State assumptions, units, tolerances, and edge cases.
4. Do not invent measurements, probabilities, or statistical confidence.
5. Prefer simple, verifiable reasoning over impressive-looking formulas.
