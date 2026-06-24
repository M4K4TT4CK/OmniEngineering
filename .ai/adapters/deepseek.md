# DeepSeek Adapter

Use this prompt with DeepSeek chat, DeepSeek API wrappers, or local models based
on DeepSeek weights. DeepSeek is a model family, so the exact app may not read
repository files unless you explicitly provide this instruction.

```text
You are operating in an OmniContext-controlled repository.

Read LLM_CONTEXT.md first. Then read .ai/context-brief.md and
.ai/context-manifest.json. Use the smallest context profile that fits the task.
Read the universal ruleset only when policy depth is needed.

Use .ai/requirements/requirements.json for task traceability. Use .ai/.ignore
as a hard prompt-level exclusion list. If you cannot access a required file,
name the missing file and apply the fallback operating contract in
LLM_CONTEXT.md.

When switching from another model, do not rely on prior chat memory. Reload the
brief, manifest, active requirement, and task-relevant files before editing.
```
