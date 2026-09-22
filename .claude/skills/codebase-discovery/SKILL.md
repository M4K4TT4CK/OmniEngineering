---
name: codebase-discovery
description: Map an unfamiliar or polyglot repository before planning cross-cutting engineering work, including build systems, boundaries, execution paths, generated code, and verification commands.
---

# Codebase discovery

Use this before consequential work in an unfamiliar, large, or polyglot repository.

1. Read repository instructions and current version-control status before interpreting code.
2. Identify manifests, language versions, package/workspace boundaries, build entry points, test systems, generated-code rules, CI workflows, deployment definitions, and ownership metadata.
3. Trace the requested behavior from its external entry point through state changes, dependencies, and observable output. Cite paths and symbols.
4. Separate source files from generated, vendored, cached, fixture, and build output. Do not plan manual edits to generated artifacts unless the repository requires them.
5. Derive verification commands from repository configuration and CI rather than guessing common defaults.
6. Record uncertainties that materially affect scope, compatibility, permissions, or test safety.

Return a compact map of relevant components, execution path, constraints, likely edit surface, verification commands, and unresolved questions. Do not turn discovery into implementation unless edits were authorized.
