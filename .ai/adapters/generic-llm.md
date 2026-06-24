# Generic LLM Adapter

Use this prompt with any hosted model, local model, chat UI, or terminal wrapper
that does not automatically read repository instruction files.

```text
You are working inside an OmniContext repository.

Before making changes, read LLM_CONTEXT.md and .ai/context-manifest.json.
Then read the files listed in required_read_order, starting with:
- .ai/core-context.md
- .ai/project-configuration.md
- .ai/requirements/requirements.json
- .ai/rules/universal-engineering-ruleset.json

Treat .ai/ as the source of truth. Follow the fallback contract in
LLM_CONTEXT.md if any source file is unavailable.

Do not inspect secrets, environment files, logs, caches, dependency folders,
build output, or anything listed in .ai/.ignore.

For every task, confirm or create a REQ-### ID, state the minimum access scope,
make the smallest safe change, update relevant docs, run available validation,
and report completion status honestly.
```
