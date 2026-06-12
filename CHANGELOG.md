# Changelog

## 2026-06-12

### Completed

- `REQ-008` | Authoring interface | Added low-friction CLI commands for
  appending requirements and structured rulepack rules without hand-editing
  large JSON files, documented the workflow, and used the new commands to add
  the requirement and a rulepack authoring recommendation.
- `REQ-007` | Structured rulepacks | Converted enforceable companion rules
  from Markdown to schema-backed JSON rulepacks, added a rulepack schema, and
  updated doctor validation to check rulepack IDs, required keys, rule IDs,
  severities, and statements.
- `REQ-006` | Ruleset refinement | Replaced the Python-specific data rule
  file with bounded, framework-agnostic data governance rules covering data
  boundaries, contracts, validation, transformations, privacy, persistence,
  migrations, and testing.
- `REQ-005` | Documentation clarity | Repositioned OmniContext as a repo-local
  AI control plane for solving context drift across assistants, clarified that
  cloned repositories do not require `make_ai.py` for normal usage, and updated
  the CLI description to present it as maintenance tooling.
- `REQ-004` | Assistant fallback rules | Added shared fallback LLM rules and
  embedded a specific fallback operating contract into Claude, Cursor, and
  Copilot entrypoints so assistants still receive core constraints if they skip
  the primary `.ai/` configuration.

## 2026-06-11

### Completed

- `REQ-003` | Executable validation | Added a dependency-free OmniContext CLI
  with `sync`, `doctor`, and `validate` commands, introduced a requirements
  registry, added schema contracts for requirements and the global ruleset,
  documented the new workflow, and added repository hygiene ignores.
- `REQ-002` | Documentation | Added a polished OmniContext README,
  repository architecture explanation, setup guidance, daily workflow notes, and
  a supporting SVG architecture diagram.
- `REQ-001` | AI workspace ruleset | Added the universal engineering ruleset
  as the controlling global configuration, aligned controlled implementation and
  completion workflow rules to the pasted requirements, added project
  configuration placeholders, refreshed assistant entrypoints, and changed the
  bootstrap script to verify source-of-truth rule files instead of overwriting
  them.
