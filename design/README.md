# OmniEngineering Design Documents

This directory is the design source for OmniEngineering. It explains the product
intent, system boundaries, architecture, data model, workflows, CLI behavior,
validation model, and delivery decisions.

## Documents

| Document | Purpose |
| --- | --- |
| [Product Design](product-design.md) | Defines the problem, users, product principles, and non-goals. |
| [System Architecture](system-architecture.md) | Describes the repo-local architecture and assistant routing model. |
| [Data Model](data-model.md) | Defines the human and machine-readable files that carry project context. |
| [Assistant Workflow](assistant-workflow.md) | Shows how an assistant should move from request to validated completion. |
| [CLI Design](cli-design.md) | Documents the dependency-free `omni` maintenance interface. |
| [Validation And Operations](validation-and-operations.md) | Defines health checks, release checks, and operational expectations. |
| [Decision Records](decision-records.md) | Captures current design decisions and tradeoffs. |

## Diagrams

| Diagram | Purpose |
| --- | --- |
| [Context Routing](diagrams/context-routing.svg) | Shows assistant entrypoints routing into `.ai/`. |
| [Assistant Lifecycle](diagrams/assistant-lifecycle.svg) | Shows the controlled task workflow. |
| [Data Model](diagrams/data-model.svg) | Shows relationships between requirements, rules, schemas, and docs. |

## Design Principles

- Keep `.ai/` as the source of truth.
- Keep assistant entrypoints small and boring.
- Keep policy machine-readable where validation matters.
- Keep human documentation close to the decisions it explains.
- Keep the CLI dependency-free and safe to run in any repo.
- Keep presentation assets separate from the delivery workspace unless they
  directly explain behavior or architecture.
