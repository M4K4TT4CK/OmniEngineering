---
name: performance-proof
description: Establish and validate performance claims with repeatable baselines, profiling, controlled changes, and statistically honest comparison.
---

# Performance proof

Apply when performance is an acceptance criterion, regression, or suspected bottleneck.

1. Define the user-visible or system metric and an acceptable threshold.
2. Record hardware, runtime, build mode, dataset, concurrency, warmup, cache state, and dependency conditions.
3. Run enough baseline samples to expose normal variance.
4. Profile before changing code and identify the dominant cost.
5. Change one justified factor at a time while preserving correctness.
6. Repeat the identical benchmark and compare distribution, not only the best run.
7. Check whether gains transfer to a representative end-to-end workload.

Track latency percentiles, throughput, CPU, memory, allocations, I/O, network, database calls, and lock/contention behavior as applicable. Reject claims that fall within noise or shift cost to an unmeasured resource.

Report the exact commands, environment, sample count, baseline, result, variance, profile evidence, correctness checks, tradeoffs, and limitations.
