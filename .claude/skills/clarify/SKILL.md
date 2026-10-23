---
name: clarify
description: Gather requirements before building. Explore the code read-only, ask only the questions that change the design, state assumptions out loud, and turn the result into testable REQ entries in .ai/requirements/requirements.json. Use in plan mode for any new feature, change, migration or project, and whenever a request is ambiguous. A fast, time-boxed form covers interviews and one-line prompts.
---

# Clarify requirements

Requirements say what must be true when the work is done. They are not a list of things to build. Each one must be checkable by a person or a test, and each one gets an id that later commits, tests and changelog entries cite.

The skill has two forms:

- **Full (the default, made for plan mode).** For features, changes and projects. Discovery and drafting happen inside the plan. Nothing is written until the plan is approved.
- **Time-boxed (about 10 minutes).** For interviews, one-line prompts ("build a task queue") and small, clear tasks. See [Time-boxed form](#time-boxed-form).

Use the full form unless the task is small and clear, or the clock is short.

## Full form

### 1. Frame the problem
In two or three lines, state:
- **Goal:** what changes for whom, in one sentence.
- **Actors and triggers:** the users, systems or events involved.
- **Done:** how someone will know it worked.
- **Kind:** feature, fix, refactor, non-functional (performance, reliability, security), migration, or new project.

### 2. Look before asking (read-only)
Don't ask about anything the repository can answer. In plan mode, only read and search; nothing is written.
- `.ai/project-map.md`: where things live. If it's missing or stale, note that `./omni map` should run after approval.
- `./omni requirement search <term>` and `./omni requirement list`: requirements that already cover the work. Extend an existing requirement rather than duplicating it.
- `./omni graph why <file-or-area>`: the requirements, tests and earlier failures already attached to the code. Every failure it lists goes into the validation plan.
- The code, its tests and its docs for the area. Note current behaviour, interfaces that others call, and constraints such as data shape, configuration or dependencies.

Write down which facts came from the code, so the questions only cover what is left.

### 3. Ask the questions that change the design
- Use the `AskUserQuestion` tool. Ask **at most 4 questions per round, with 2–4 options each**, and do **no more than 2 rounds**.
- Put the recommended option first, labelled "(Recommended)". Each option's description says what it changes: scope, design, cost or risk.
- Ask only when the answer changes design, scope or acceptance. Anything else becomes an assumption.
- Rank questions by impact. Topics are in the [question bank](#question-bank).
- If the person says "go with your recommendations", take the recommended options and record them as decisions, not assumptions.

### 4. Assume what's left, out loud
Record each assumption with what it implies, for example: "Assume at-least-once delivery, so handlers must be idempotent."

Don't assume these; ask, or stop:
- destructive data changes;
- credentials or production access;
- security or legal approval;
- public interface breaks;
- money spent;
- a choice between product directions that can't both be true.

These are the stop conditions in `.ai/playbooks/planning.md`.

### 5. Draft the requirements
Write one requirement per independently verifiable capability or constraint. Each one has:

| Field | What it holds |
|---|---|
| title | Short and specific, e.g. "Retry failed jobs with backoff" |
| category | `feature`, `fix`, `non-functional`, `refactor`, `docs`, `migration`… |
| priority | `critical` / `high` (must have) / `medium` / `low` (nice to have) |
| description | Actor, behaviour and condition: "When a handler raises, the worker re-queues the job with exponential backoff up to N attempts, then moves it to the dead-letter list." |
| acceptance | Measurable checks, as Given/When/Then or numbers. Include at least one failure or edge case. |
| validation | How it will be checked: `pytest`, a named test file, a manual step, a benchmark. |
| docs | The docs that must change: README, API reference, CHANGELOG… |
| scope | The files or directories the work may touch (minimum access scope). |
| risks | What could go wrong, and the assumptions it depends on. |

Rules:
- Make non-functional requirements measurable: "p99 < 200 ms at 50 req/s on one node", not "fast". Replace words like *robust*, *scalable*, *user-friendly* and *secure* with a check.
- State what is **out of scope** as explicitly as what is in.
- Check every requirement against `.ai/knowledge/swebok/requirements-quality-checklist.md`, covering identity, clarity, completeness, feasibility, verifiability and traceability. Fix each failure before the plan goes out.
- Check for conflicts with existing requirements and with `.ai/rules/`. Resolve each conflict, or record it as a risk.
- For a large feature, also plan a short SRS-style document under `design/` (or the project's docs). The registry entries then point at its sections.

### 6. Order the work
Group the requirements into slices:
- Slice 1 is the smallest thing that runs end to end.
- Each later slice adds one capability and leaves everything working.
- List the REQ ids in each slice.

This order drives implementation and the prompts that follow.

### 7. Write the plan
The plan holds, in this order:
1. **Goal**, with its actors and definition of done (from step 1).
2. **What the code already tells us** (from step 2), with file references.
3. **Decisions**: answered questions and their consequences.
4. **Assumptions**: each with its consequence.
5. **Out of scope.**
6. **Requirements**: the step 5 table, with ids marked *new* or *update REQ-###*.
7. **Slices** (from step 6).
8. **Validation plan**: tests to add or run, plus the earlier failures and regression tests that `omni graph why` showed.
9. **Commands to run after approval** (step 8), exactly as they will run.

### 8. After approval, register
Run one command per new requirement. The next REQ-### is picked automatically.

```
./omni requirement add --title "Retry failed jobs with backoff" \
  --category feature --priority high --status pending \
  --description "When a handler raises, the worker re-queues the job with exponential backoff (base 1 s, cap 60 s) up to 5 attempts, then moves it to the dead-letter list." \
  --acceptance "a job that fails twice then succeeds is run 3 times and marked done,a job that always fails ends in the dead-letter list after 5 attempts,retry delays grow and never exceed 60 s" \
  --validation "pytest tests/test_retry.py" --docs "README,CHANGELOG" \
  --scope "src/worker.py,tests/test_retry.py" --risks "handlers must be idempotent (at-least-once)"
```

- List fields (`--acceptance`, `--validation`, `--docs`, `--scope`, `--risks`) are **comma-separated**, so keep commas out of any single item.
- Plan approval counts as acceptance, so use `--status pending`. Use `proposed` for any requirement the plan marks as still open.
- For a requirement that already exists, don't add a duplicate. `./omni requirement update REQ-###` changes its status, title or description, or appends a risk note. To change its acceptance criteria, edit its entry in `.ai/requirements/requirements.json`.
- Finish with `./omni requirement list`, and show the person the new ids.
- From then on, cite the ids in commits, tests and CHANGELOG entries. The completion gate and the project graph both trace through them.

## Time-boxed form
1. **Restate** the problem in one sentence, with its 2–3 core user flows.
2. **Ask 5–8 questions in one message, ranked.** Each one must change the design; say what it changes.
3. **Assume** whatever isn't answered, out loud.
4. **Write** a one-page spec (the project's spec doc if it has one, otherwise `design/spec.md`), with *Out of scope* and *Assumptions* sections.
5. **Register** must-haves as `high` and nice-to-haves as `low`, with `./omni requirement add` (see step 8).
6. **Order the slices** by REQ id.

## Question bank
Ask the questions that matter for this system, starting with the riskiest.

- **Users and actors:** who uses it, in which roles, and who is affected but doesn't use it?
- **Scope:** what is explicitly *not* part of this? What is the smallest useful version?
- **Data:** volume, growth, retention, privacy or regulated data. Can any of it be lost on restart?
- **Interfaces:** REST, CLI, library or UI? Who calls it today, and must they keep working (compatibility, versioning)?
- **Failure:** what happens when a dependency is slow, down or wrong? Do we need retries, timeouts, idempotency, partial results?
- **Consistency and ordering:** read-your-writes, eventual consistency, FIFO, per-key ordering? Delivery at-most, at-least or exactly once?
- **Security:** authentication, authorization, secrets, untrusted input, audit needs.
- **Performance and scale:** requests per second, latency target (p99), data size, single node or several, concurrency.
- **Operability:** health checks, metrics, logs, alerts, configuration, deployment and rollback.
- **Migration and rollout:** existing data or users to move, feature flags, backfill, and how to undo it.
- **Done:** who signs off, and what demo or test proves it?

## Permission modes
The skill works the same way in every mode; only how its writes and commands happen changes. It writes `.ai/requirements/requirements.json` (through `./omni requirement`) and, for large features, a spec under `design/`.

- **Plan mode** (read-only): steps 1–7 as written. The plan holds the full draft, and step 8's commands run once the plan is approved.
- **Default** (asks before edits and commands): run step 8's commands in one batch, saying in one line what they do before the prompt appears. If one is declined, run the rest and say what was skipped.
- **Accept edits:** file writes go straight through, but commands still ask, so batch them.
- **Auto / bypass:** run straight through, but still show the questions, assumptions and requirement list before registering them.
