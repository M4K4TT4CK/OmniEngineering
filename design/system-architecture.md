# System Architecture

## Architecture Summary

OmniEngineering is a repository pattern with a small maintenance CLI. The
architecture is intentionally file-based, with OmniContext serving as the
assistant context-governance layer:

- `.ai/` stores the source-of-truth context, rules, schemas, adapters,
  playbooks, checklists, knowledge packs, and requirements.
- Root assistant files and portable adapter prompts route tools to `.ai/`.
- `make_ai.py` and `omni` provide sync, validation, and low-friction authoring.
- `README.md`, `CHANGELOG.md`, `assets/`, and `design/` explain the workspace
  and its delivery behavior.

See [Context Routing](diagrams/context-routing.svg) for the primary routing
diagram.

## Components

| Component | Responsibility |
| --- | --- |
| `.ai/core-context.md` | Human-readable global operating instructions. |
| `.ai/project-configuration.md` | Project-specific command and policy placeholders. |
| `.ai/context-manifest.json` | Machine-readable load order for any local or hosted LLM. |
| `.ai/adapters/*.md` | Copy-paste prompts for tools without native repo instruction loading. |
| `.ai/playbooks/*.md` | Task-specific engineering procedures. |
| `.ai/checklists/*.md` | Compact completion and safety gates. |
| `.ai/knowledge/**/*.md` | Body-of-knowledge guidance adapted for project use. |
| `.ai/rules/*.json` | Structured companion rulepacks. |
| `.ai/requirements/requirements.json` | Requirement registry and task traceability. |
| `.ai/schemas/*.json` | Schema contracts for requirements and rulepacks. |
| `LLM_CONTEXT.md` | Universal model-agnostic entrypoint. |
| `AGENTS.md` | Codex entrypoint and fallback contract. |
| `CLAUDE.md` | Claude Code entrypoint and fallback contract. |
| `.cursorrules` | Cursor entrypoint and fallback contract. |
| `.github/copilot-instructions.md` | GitHub Copilot entrypoint and fallback contract. |
| `.kiro/steering/omnicontext.md` | Kiro-style steering entrypoint. |
| `make_ai.py` | Dependency-free maintenance implementation. |
| `omni` | Repo-local command shim for `make_ai.py`. |
| `README.md` | Public overview and usage guide. |
| `design/` | Product, architecture, workflow, and delivery design source. |

## Source Of Truth Boundary

The `.ai/` directory is authoritative for assistant behavior. Root assistant
files are generated or synchronized from shared fallback text in `make_ai.py`.
They exist because assistants expect native entrypoint names, not because policy
should be duplicated by hand.

When a rule must be enforced or inspected by tooling, it belongs in a structured
JSON file. When a rule needs narrative explanation, it belongs in Markdown
documentation that points back to the structured source.

## Assistant Routing

1. A user opens a repository with one or more AI coding assistants.
2. The assistant reads its native entrypoint file.
3. The shim directs the assistant to the matching source under
   `.ai/entrypoints/`.
4. The entrypoint source directs the assistant to core context, project
   configuration, requirements, and structured rulepacks.
5. The assistant uses the shared workflow for implementation and completion.

The fallback contract in `.ai/entrypoints/fallback-contract.md` repeats the
minimum operating rules for cases where an assistant does not fully load the
`.ai/` files.

For models that do not automatically read repository files, the user should
paste an adapter prompt from `.ai/adapters/`. The adapter tells the model to
read the relevant `.ai/entrypoints/` source and `.ai/context-manifest.json`
before loading the required `.ai/` files.

For model-router gateways such as OpenRouter-style APIs, OmniContext is sent as
part of the request context. The selected model or provider may change, so each
new routed session should reload the universal context and manifest instead of
trusting previous model memory.

## Validation Boundary

`omni doctor` validates the shape and presence of the workspace. It is not a
full semantic proof system. Its job is to catch practical drift:

- Missing required `.ai/` files.
- Invalid JSON.
- Missing top-level ruleset keys.
- Malformed rulepack and requirement structures.
- Assistant pointer drift.
- Assistant entrypoint source drift.
- Synced ignore file drift.
- Workspace file placement.
- Missing README architecture asset references.
- Missing license, notice, or trademark policy files.
- Missing generated project map.
- Missing CLI entrypoints.

## Extension Points

- Add assistant entrypoints by extending the generated entrypoint source map and
  shim target map in `make_ai.py`.
- Add rulepacks under `.ai/rules/` and include them in validation.
- Add task playbooks under `.ai/playbooks/` and reference them from the manifest.
- Add completion checklists under `.ai/checklists/` and reference them from the manifest.
- Add schema contracts under `.ai/schemas/`.
- Add design docs under `design/` when product behavior changes.
- Add behavior and architecture diagrams under `design/diagrams/`.

## Constraints

- The CLI should stay dependency-free.
- The workspace should remain portable across macOS, Linux, and Windows clones.
- Assistant entrypoint sync should not overwrite `.ai/` source-of-truth files.
- Validation should be helpful without requiring a full JSON Schema runtime.
