# Model Router Adapter

Use this prompt with any model-router gateway that can send the same request to
many model providers or model slugs. Examples include OpenRouter-style APIs,
custom LiteLLM gateways, proxy layers, or internal model brokers.

```text
You are receiving this request through a model-router gateway inside an
OmniContext repository.

The router or selected model may change between calls. Do not rely on prior
model memory, provider memory, cache state, or a previous model's interpretation
of the repository.

Load context in this order:
1. LLM_CONTEXT.md
2. .ai/context-brief.md
3. .ai/context-manifest.json
4. The smallest context profile that fits the task
5. One task-relevant playbook, checklist, or rulepack when needed

Treat .ai/ as the source of truth. If a routed fallback model takes over, it
must use this same loading order before editing or answering.

For each task, restate:
- Active REQ-### ID
- Minimum access scope
- Loaded OmniContext files
- Context profile used
- Validation expected before completion

Do not read files excluded by .ai/.ignore. Do not claim completion unless
validation ran or the reason validation could not run is explicit.
```
