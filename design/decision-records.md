# Decision Records

## ADR-001: Use A Repo-Local Source Of Truth

**Status:** Accepted

OmniEngineering stores engineering context, assistant context, rules,
playbooks, checklists, and knowledge packs inside the repository rather than a
hosted service. This keeps the system portable, reviewable, and
version-controlled.

## ADR-002: Keep Assistant Entrypoints Thin

**Status:** Accepted

Assistant-specific files should route back to `.ai/` and include a compact
fallback contract. They should not become separate policy documents.

## ADR-003: Keep The CLI Dependency-Free

**Status:** Accepted

The maintenance CLI uses only the Python standard library. This reduces setup
friction and makes `omni doctor` safe to run in fresh clones.

## ADR-004: Use Structured JSON For Enforceable Rules

**Status:** Accepted

Markdown is good for explanation, but structured JSON is better for validation.
Rulepacks and requirements use JSON so the doctor can inspect them.

## ADR-005: Keep Delivery Diagrams Versionable

**Status:** Accepted

SVG diagrams render well in README files and remain versionable as text.
Non-delivery brand exploration is kept outside the public workspace package.
