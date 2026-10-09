# OmniEngineering Context Brief

Use this file as the first low-token orientation layer. Do not read the entire
workspace by default.

## Default Loading Rule

For most tasks, load only:

1. `.ai/context-brief.md`
2. `.ai/project-context.md` — arbiter's decision history and open gaps
3. `.ai/project-configuration.md`
4. `.ai/project-map.md`
5. The active requirement via `python omni requirement show <ID>` or
   `python omni requirement list --status pending` (never the raw registry
   file)

Then inspect only the project files named by the active requirement or prompt.
`README.md` "Layout" names the module responsible for each pipeline stage.
Before editing a file, `python omni graph why <path>` lists the requirements,
tests and earlier failures already attached to it.

## Escalation Rule

Read more context only when the task needs it:

| Need | Read |
| --- | --- |
| Implementation scope and traceability | `.ai/rules/controlled-implementation.json` |
| Completion gate | `.ai/rules/completion-workflow.json` |
| Data contracts, validation, persistence | `.ai/rules/data-governance.json` |
| Module boundaries and interfaces | `.ai/rules/oop-design.json` |
| Full policy uncertainty | `.ai/rules/universal-engineering-ruleset.json` |
| A task-shaped procedure (debugging, review, release...) | `.ai/playbooks/` |
| Pre-implementation / pre-completion gates | `.ai/checklists/` |
| Engineering-discipline depth | `.ai/knowledge/swebok/README.md` |
| Probe, adapter or pack behavior | `README.md`, `src/arbiter/packs/` |
| Training and calibration workflow | `SETUP.md`, `tools/` |
| What broke before, and why | `python omni failure search <text>` |

## Token Rules

- Prefer `.ai/project-map.md` over broad directory traversal.
- Prefer the one relevant rulepack over all rulepacks.
- Never load `training/`, `arbiter-out/` or `fixtures/` wholesale; open the
  specific fixture a test names.
- Respect `.ai/.ignore`.

## Completion Minimum

Before claiming completion, confirm:

- Requirement ID is known or assigned (`python omni requirement add|draft`).
- Minimum access scope was followed.
- `python -m pytest tests/ -q` was run, or explicitly marked not applicable.
- A fixed defect has a failure-ledger entry (`python omni failure add`).
- `python omni gate` passes, or each failure is explicitly waived with
  `python omni waive <rule-id> --reason "..."`.
- `CHANGELOG.md` and `.ai/project-context.md` record the outcome.
- Final response includes remaining risk or follow-up, if any.

## When The Task Touches Arbiter

Read the integration handbook once: `docs/arbiter-integration.md` in the OmniEngineering
repository (https://github.com/paukennick/OmniEngineering/blob/main/docs/arbiter-integration.md;
an adopted workspace carries no copy). It says how `omni gate` runs `arbiter gate`, how
findings reach the ledger and the graph, and which switches turn each piece off.
