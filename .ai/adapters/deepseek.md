# DeepSeek Adapter

Use this prompt with DeepSeek chat, DeepSeek API wrappers, or local models based
on DeepSeek weights. DeepSeek is a model family, so the exact app may not read
repository files unless you explicitly provide this instruction.

```text
You are operating in an OmniContext-controlled repository.

Read LLM_CONTEXT.md first. Then read .ai/context-manifest.json and follow its
required_read_order. Treat .ai/rules/universal-engineering-ruleset.json as the
highest-priority project rule after system and user messages.

Use .ai/requirements/requirements.json for task traceability. Use .ai/.ignore
as a hard prompt-level exclusion list. If you cannot access a required file,
name the missing file and apply the fallback operating contract in
LLM_CONTEXT.md.

When switching from another model, do not rely on prior chat memory. Reload the
OmniContext files before editing.
```
