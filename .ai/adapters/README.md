# OmniContext LLM Adapters

This folder contains copy-paste adapter prompts for models and tools that do
not automatically read one of the native entrypoint files.

Use these when switching between local models, hosted chat models, terminal
wrappers, model-router gateways such as OpenRouter, DeepSeek-based tools,
Kiro-style workflows, or any other assistant that needs explicit repository
context.

## Adapter Rule

Every adapter points back to the same source of truth:

1. `.ai/entrypoints/`
2. `.ai/context-brief.md`
3. `.ai/context-manifest.json`
4. `.ai/project-map.md`
5. `.ai/project-configuration.md`
6. `.ai/requirements/requirements.json`
7. One relevant playbook, checklist, or rulepack

If a tool has a native instruction file, prefer its tiny root shim. If it does
not, paste the matching adapter prompt into the model's system, developer, or
project instruction field.

For model routers, include the adapter prompt in every new routed session. A
router can switch model providers or model slugs, so context should be treated
as request payload, not as durable model memory.

## Token And Model Discipline

- Control the context window. Do not let a model auto-scan the whole repository
  when `.ai/project-map.md` and a targeted file list are enough.
- Respect `.ai/.ignore`, `.cursorignore`, `.gitignore`, and equivalent tool
  ignore files.
- Attach or inspect only the files required for the active requirement.
- Start a fresh session or compact history when switching tasks.
- Use smaller or lower-effort models for boilerplate, formatting, simple docs,
  and mechanical edits.
- Use stronger or higher-effort models for architecture, complex debugging,
  migrations, security-sensitive work, and high-risk design decisions.
