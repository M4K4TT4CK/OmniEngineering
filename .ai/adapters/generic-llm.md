# Generic LLM Adapter

Use this prompt with any hosted model, local model, chat UI, or terminal wrapper
that does not automatically read repository instruction files.

```text
You are working inside an OmniContext repository.

Before making changes, read LLM_CONTEXT.md, .ai/context-brief.md, and
.ai/context-manifest.json.

Use the smallest context profile that fits the task:
- minimum for questions, small docs, or simple edits
- implementation for code changes
- review for review or risk checks
- deep_policy only when policy or workflow uncertainty requires it

Treat .ai/ as the source of truth. Follow the fallback contract in
LLM_CONTEXT.md if any source file is unavailable.

Do not inspect secrets, environment files, logs, caches, dependency folders,
build output, or anything listed in .ai/.ignore.

For every task, confirm or create a REQ-### ID, state the minimum access scope,
make the smallest safe change, update relevant docs, run available validation,
and report completion status honestly.
```
