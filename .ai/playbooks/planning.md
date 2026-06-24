# Planning Playbook

Use this when the user asks for a feature, fix, investigation, architecture
change, or ambiguous task.

## Required Output Before Editing

- Active or proposed `REQ-###` ID.
- One-sentence task objective.
- Minimum access scope.
- Files or areas that should not be accessed.
- Acceptance criteria.
- Validation plan.
- Documentation impact.
- Known assumptions.

## Planning Rules

- Prefer existing project conventions over new abstractions.
- For requirement-heavy work, use
  `.ai/knowledge/swebok/software-requirements.md`.
- Validate requirement quality with
  `.ai/knowledge/swebok/requirements-quality-checklist.md`.
- For design-heavy work, use `.ai/knowledge/swebok/software-design.md` and the
  design quality checklist.
- For multi-step or high-risk work, use
  `.ai/knowledge/swebok/software-engineering-management.md`.
- For process-heavy work, use
  `.ai/knowledge/swebok/software-engineering-process.md`.
- For modeling or method choices, use
  `.ai/knowledge/swebok/software-engineering-models-and-methods.md`.
- For cost, value, prioritization, or tradeoff decisions, use
  `.ai/knowledge/swebok/software-engineering-economics.md`.
- For high-impact engineering decisions, use
  `.ai/knowledge/swebok/engineering-foundations.md`.
- Before broad traversal, read `.ai/project-map.md` when it exists. If it is
  missing or stale after structural changes, run `./omni map` before selecting
  files to inspect.
- Ask a question only when a safe assumption would be risky.
- If the task is small and clear, plan briefly and proceed.
- If the task is broad, split it into requirements before implementation.
- Do not inspect broad repo areas until the scope justifies it.

## Stop Conditions

Stop and ask for direction when the task requires destructive data changes,
credential access, legal/security approval, external production access, or a
choice between incompatible product directions.
