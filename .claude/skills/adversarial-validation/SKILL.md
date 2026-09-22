---
name: adversarial-validation
description: Design and execute authorized negative, boundary, fuzz, race, failure-injection, memory-safety, and regression testing when asked to break, harden, or deeply validate code.
---

# Adversarial validation

Use this workflow after the behavioral contract and authorized test boundary are known.

1. Extract invariants, trust boundaries, state transitions, concurrency points, resource limits, and dependency assumptions.
2. Rank failure hypotheses by impact and plausibility.
3. Choose the lowest-cost test capable of falsifying each important hypothesis.
4. Preserve minimal reproducers and distinguish product defects from environment or test-harness defects.
5. Convert confirmed failures into deterministic regression tests when practical.
6. Stop destructive, expensive, production, or third-party testing until explicitly authorized.

Cover relevant classes: missing/malformed/oversized input, numeric and collection boundaries, Unicode and encoding, injection, unauthorized cross-user or cross-tenant access, replay and ordering, cancellation and timeout, partial dependency failure, corrupted state, concurrency and races, resource exhaustion, shutdown and recovery.

For language-specific tools and selection criteria, read [references/language-tooling.md](references/language-tooling.md) only for languages present in the repository.

Report hypotheses tested, commands, observable results, confirmed defects, inconclusive cases, coverage limitations, and residual risk. A clean run does not prove absence of vulnerabilities.
