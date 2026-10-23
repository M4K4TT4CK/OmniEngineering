# Contributing To OmniEngineering

Thanks for helping make OmniEngineering more useful.

## Contribution License

Unless you explicitly say otherwise in writing, any contribution intentionally
submitted to this project is submitted under the Apache License, Version 2.0.
This matches the contribution rule in the project `LICENSE`.

## What To Send Back

Useful contributions include:

- Bug fixes in `omni`, `make_ai.py`, `omni_graph.py` or `omni_mcp.py`.
- Better validation checks.
- New assistant adapters.
- Improved playbooks and checklists.
- SWEBOK-aligned knowledge improvements.
- Migration notes from real projects.
- Better diagrams or clearer documentation.
- Security hardening.
- Compatibility notes for local models and model routers.

## Contribution Expectations

- Keep changes focused and reviewable.
- Preserve the repo-local, standard-library-only design: the workspace must
  work without MCP and without third-party packages (tree-sitter for
  `omni graph build` and Arbiter stay optional), unless a requirement
  explicitly changes that direction.
- Do not add secrets, private project data, customer data, logs, local
  environment files, or generated dependency folders.
- Keep public docs useful to other engineering teams, not only one local setup.
- Before submitting, run `python3 -m unittest discover -s tests`,
  `./omni doctor` and `./omni gate`. The gate wants a requirement
  (`./omni requirement add`) and a `CHANGELOG.md` entry that cites it.
- Do not change `arbiter/`: it is a vendored subtree of a separately licensed
  project; send Arbiter changes upstream.

## Forks And Naming

Forks are welcome. If you distribute a modified fork, use a distinct name and
follow `TRADEMARKS.md`.

## Community Intent

You can use OmniEngineering freely. The ask is simple: when you discover
something generally useful, send it back if you can. Good engineering compounds
when the field notes return home.
