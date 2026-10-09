# Adapter: Kiro

`.ai/entrypoints/kiro.md` is the generated text `./omni sync` writes into
`.kiro/steering/omnicontext.md` — Kiro's own steering-file convention. This
adapter is the part that generation can't cover: how Kiro's spec-driven
workflow (specs, tasks, task status) sits on top of this repo's `REQ-###`
requirement registry.

## Don't run two tracking systems at once

Kiro organizes work into specs with their own task lists. This repo's
tracking is the requirements registry, queried with
`python omni requirement show|list|search` (never read whole), with ids
allocated by `python omni requirement add` and retired ids kept unique
against `.ai/requirements/requirements-archive.json`. If a Kiro spec produces work that touches
this repo, map it onto a `REQ-###` id rather than letting the spec's task
list stand in for one — otherwise `CHANGELOG.md` entries, acceptance
criteria, and validation records have nothing stable to cite, and the
fallback contract's "assign or confirm a `REQ-###` id before work begins"
rule has nothing to point at.

- One Kiro spec, one `REQ-###` id (or one per major task inside the spec if
  the tasks are independently shippable) — pick whichever granularity
  `CHANGELOG.md` entries will actually be written at.
- Kiro's own task-completion state is not a substitute for this project's
  validation record. A task marked done in Kiro still needs the
  `validation_required` commands from the requirement entry run and reported.

## Steering file staleness

If `.kiro/steering/omnicontext.md` looks out of date with
`.ai/entrypoints/kiro.md`, that's a sync problem, not a content problem —
run `./omni sync` rather than hand-editing the steering file, since sync
will overwrite hand edits on the next run anyway.
