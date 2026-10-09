# Computing Foundations

This knowledge pack adapts computing foundations for OmniContext. Use it when a
task depends on core computing concepts such as algorithms, data structures,
concurrency, operating systems, networks, databases, distributed systems, or
runtime behavior.

## Purpose

Computing foundations help engineers reason about what software can do, how it
uses resources, and where failure modes come from.

In OmniContext, computing guidance should help an LLM or human engineer:

- Choose appropriate data structures and algorithms.
- Understand resource and complexity tradeoffs.
- Respect concurrency, networking, persistence, and runtime constraints.
- Avoid incorrect assumptions about platforms and environments.
- Validate behavior at the right abstraction level.

## Core Areas

| Area | Typical Questions |
| --- | --- |
| Algorithms | What is the expected complexity and failure mode? |
| Data structures | What representation supports the required operations? |
| Operating systems | What file, process, permission, path, or scheduling behavior matters? |
| Networking | What latency, retry, ordering, protocol, or timeout behavior matters? |
| Databases | What consistency, indexing, transaction, migration, or query behavior matters? |
| Concurrency | What races, locks, queues, idempotency, or ordering guarantees matter? |
| Distributed systems | What partial failure, retries, duplication, or eventual consistency matters? |
| Runtime/platform | What language, memory, dependency, packaging, or environment behavior matters? |

## LLM Operating Rules

When computing foundations matter:

1. Read `.ai/knowledge/swebok/computing-foundations.md`.
2. Use `.ai/knowledge/swebok/computing-foundations-checklist.md`.
3. State the relevant computing assumption.
4. Validate complexity, concurrency, persistence, or platform behavior when it
   affects correctness.
5. Do not hide uncertainty about runtime or environment behavior.
