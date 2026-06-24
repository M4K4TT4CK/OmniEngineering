# OpenRouter Adapter

Use this prompt when calling models through OpenRouter or an OpenRouter-compatible
flow. OpenRouter provides a unified endpoint for many models and supports routing
and fallback behavior, so OmniContext must be included in the messages sent to
the selected model.

```text
You are being called through OpenRouter in an OmniContext repository.

OpenRouter may route this request to different model slugs or providers. Do not
assume any prior model-specific memory. Treat the supplied repository context as
the only durable source of truth.

Before editing or making architectural recommendations, load:
1. LLM_CONTEXT.md
2. .ai/context-brief.md
3. .ai/context-manifest.json
4. The smallest context profile that fits the task
5. One task-relevant playbook, checklist, or rulepack when needed

If context is too large for the selected model, summarize each loaded file and
ask for the next required file instead of guessing. When switching model slugs,
reload this context from the repository rather than inheriting another model's
summary.

Follow .ai/.ignore as the prompt-level exclusion list. Confirm or create a
REQ-### ID, state minimum access scope, make the smallest safe change, update
docs when needed, and report validation honestly.
```
