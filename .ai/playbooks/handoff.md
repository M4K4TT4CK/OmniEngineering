# Handoff Playbook

Use this when switching between models, tools, or human maintainers.

## Model Handoff Packet

Include:

- Active `REQ-###` ID.
- Current objective.
- Files changed.
- Files inspected.
- Decisions made.
- Validation already run.
- Validation still needed.
- Risks or assumptions.
- Next recommended action.
- Current blockers and decisions when the work spans multiple steps.
- Quality, process, or professional-practice concerns that affect the next
  actor.
- Failures recorded or still open (`omni failure list`) and any that lack a root
  cause, regression test, or prevention.

## Router And Local Model Handoff

For model routers or local models, do not rely on prior chat memory. Reload:

1. `LLM_CONTEXT.md`
2. `.ai/context-manifest.json`
3. Active requirement
4. Task-relevant playbook
5. Task-relevant rulepack
