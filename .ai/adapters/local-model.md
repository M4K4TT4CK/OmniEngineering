# Local Model Adapter

Use this prompt for Ollama, LM Studio, llama.cpp wrappers, local DeepSeek/Qwen
models, or other local coding models with limited context.

```text
You are working inside an OmniContext repository.

Use a compact loading strategy:
1. Read LLM_CONTEXT.md.
2. Read .ai/context-brief.md.
3. Read .ai/context-manifest.json.
4. Use the minimum context profile unless the task needs implementation or review.
5. Read only the one playbook, checklist, or rulepack relevant to the active task.

If context is limited, summarize loaded rules before editing and ask for the
next required file instead of guessing. Never ignore .ai/.ignore. Never claim
completion without validation or an explicit explanation that validation was not
available.
```
