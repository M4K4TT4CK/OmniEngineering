# Local Model Adapter

Use this prompt for Ollama, LM Studio, llama.cpp wrappers, local DeepSeek/Qwen
models, or other local coding models with limited context.

```text
You are working inside an OmniContext repository.

Use a compact loading strategy:
1. Read LLM_CONTEXT.md.
2. Read .ai/context-manifest.json.
3. Read .ai/core-context.md.
4. Read .ai/project-configuration.md.
5. Read .ai/requirements/requirements.json.
6. Read only the .ai/rules/*.json files relevant to the active task.

If context is limited, summarize loaded rules before editing and ask for the
next required file instead of guessing. Never ignore .ai/.ignore. Never claim
completion without validation or an explicit explanation that validation was not
available.
```
