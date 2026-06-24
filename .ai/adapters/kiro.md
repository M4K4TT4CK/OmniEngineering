# Kiro Adapter

Use this prompt if Kiro does not automatically load
`.kiro/steering/omnicontext.md` in the current workspace.

```text
This repository uses OmniContext.

Before planning or implementing, read .kiro/steering/omnicontext.md. Then read
LLM_CONTEXT.md, .ai/context-brief.md, and .ai/context-manifest.json. Use the
smallest context profile that fits the task and treat .ai/ as the project
source of truth.

Use Kiro specs or plans only when they do not conflict with the OmniContext
rules, requirement registry, security exclusions, and completion workflow.
```
