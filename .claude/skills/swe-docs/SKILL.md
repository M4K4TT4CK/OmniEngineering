---
name: swe-docs
description: Build and read the generated engineering documents (SAD, SDD, SRS, traceability matrix, test plan, module reference, class diagram, changes summary) with `./omni docs build`. Use before a design review or a release, when someone asks how the system is built or what is untested, after a slice of work lands, and when docs/generated looks stale. Nothing in it is written by a model.
---

# Engineering documents from the code and the registry

`./omni docs build` writes a set of standard documents to `docs/generated/` (or the ruleset's
`configuration.docs_output_dir`). Every line comes from the code graph, the requirement registry, the
failure ledger, the test-suite registry, git history and source docstrings. A section with no data behind it
says **Not derived** and why. Treat that as a finding, not a gap to fill by hand.

## When to run it

- **Before a design review or a plan.** Read the SAD's building-block and runtime views, then the SDD entry
  for each module you will touch. Pair it with the `run-plan` and `review-ai-code` skills when they are installed.
- **After clarifying requirements.** `./omni requirement add` regenerates the SRS on its own once the docs
  have been built in this repository. Check that the new requirement reads correctly there. Pair it with `clarify`.
- **Before calling a slice done.** Read the test plan's *Requirements with no linked test* list, and add the
  tests it names. Pair it with `test-debug`.
- **Before a release.** Read the SAD's deployment view and *Risks and technical debt* section and the changes
  summary's open failures. Pair it with `sre-readiness`.

## How to run it

1. `./omni docs build`. If the code graph is missing or stale it is rebuilt first, which takes a few seconds.
   Use `--no-graph-rebuild` to keep the graph as it is.
2. Commit `docs/generated/` together with the change that moved it. The output is byte-identical for the same
   inputs, so the diff shows only what really changed. The `Generated from commit` line in `index.md` is the
   only volatile line.
3. After that, `./omni requirement add`, `update --status` and `complete` regenerate the documents
   automatically. Pass `--no-docs` to skip once, or set `configuration.docs_auto_regenerate` to `false` to stop it.
4. To make `./omni gate` fail when the documents are stale, set `configuration.docs_gate_freshness` to `true`.
   It is off by default. `./omni docs build --check` runs the same check by hand.

To document another checkout without writing into it, point the graph and the output elsewhere:
`./omni docs build --root ../other --graph /tmp/other-graph.json --output /tmp/other-docs`.

## How to read the output

Start at `index.md`. It links every document and states which commit the code graph was built at.

| Question | Document |
| --- | --- |
| What is the system, what surrounds it, how is it split up? | `architecture.md` (SAD), sections 3 and 4 |
| What is module X for and why is it built that way? What does it import? | `design.md` (SDD); import lists in `modules.md`; signatures with `./omni docs build --detail full` |
| What must be true, and how is each requirement accepted? | `requirements.md` (SRS) |
| Which files and tests belong to REQ-NNN? | `requirements-traceability.md` |
| What is untested, and which failures lack a regression test? | `test-plan.md` |
| What changed recently, what is blocked, which required docs were never updated? | `CHANGES-SUMMARY.md` |

Read the diagrams with their notes. Large graphs are capped (24 components, 40 edges, 25 classes; tighter at the default summary detail), and the
note under each diagram says what was collapsed or left out. The runtime view uses only call edges the graph
resolved with confidence. When an entry point dispatches through a table or a callback, the view says it is
not derived rather than guessing.

## Rules

- Never hand-edit `docs/generated/`. Change the code, the registry (`./omni requirement ...`) or the ledger
  (`./omni failure ...`), then rebuild.
- If a document is wrong, the source data is wrong. Fix the source.
- Quote IDs, paths and counts from the documents exactly when you report on them.
