# Third-party components

Arbiter itself is licensed under the Apache License, Version 2.0
([LICENSE](LICENSE), [NOTICE](NOTICE)). That licence covers Arbiter's own code
and documentation only. It grants nothing over the components below, each of
which is governed by its own licence.

The adapters in `src/arbiter/packs/adapters/` invoke external analyzers as
separate executables but do not vendor or redistribute them. Each is installed
separately by the operator.

The **Redistributed** column is the load-bearing one.

| Component | License | Role | Redistributed |
|---|---|---|---|
| PyYAML | MIT | runtime dependency | not from this repository — installed by pip; an `arbiter bundle build` bundle carries its unmodified wheel |
| tomli | MIT | runtime dependency on Python < 3.11 only | as PyYAML: installed by pip; carried as an unmodified wheel in a bundle built for Python < 3.11 |
| ruff | MIT | optional analyzer | no — installed by the operator |
| bandit | Apache-2.0 | optional analyzer | no — installed by the operator |
| checkov | Apache-2.0 | optional analyzer | no — installed by the operator |
| semgrep | LGPL-2.1 (CLI) | optional analyzer | no — installed by the operator |
| gitleaks | MIT | optional analyzer | no — installed by the operator |
| 3d-force-graph (with three.js and the packages it bundles) | MIT | `omni graph view` viewer, part of the OmniEngineering workspace | yes — vendored unmodified in `.ai/graph-viewer/`; notices in [.ai/graph-viewer/THIRD_PARTY_NOTICES.md](.ai/graph-viewer/THIRD_PARTY_NOTICES.md) |
| OmniEngineering workspace | Apache-2.0 | `.ai/`, `omni`, `make_ai.py`, `omni_graph.py`, `omni_mcp.py` | yes — its NOTICE is reproduced in [NOTICE](NOTICE) |

The optional extras in `pyproject.toml` (`ast`, `mcp`, `api`, `dev`, `tools`)
are installed by pip on the operator's request and are not redistributed.
The tree-sitter grammars the language packs read (various licences) are
downloaded by `tools/install_tools.sh` into the user's cache on request and
are not redistributed either.

The PyYAML and tomli wheels in a bundle are the projects' own published wheels,
which carry their licence files in their `dist-info` metadata. That this meets
the MIT attribution condition is an inference, not a reviewed determination.

## The analyzers are not in the bundle

Before building a distributable air-gapped bundle that *does* include the five
analyzers, their rows above change and each licence must be reviewed for
redistribution terms — `semgrep` (LGPL-2.1) in particular.

**That review has not been done.** It is tracked as requirement L-6 in
[docs/licensing.md](docs/licensing.md) (ARB-005), which blocks including these
binaries in the air-gapped bundle until it is complete and recorded here with a
date and a reviewer. The bundle `arbiter bundle build` writes today carries
Arbiter and its runtime dependencies only, and its manifest says so
([docs/bundle.md](docs/bundle.md)).

## Planted credentials

The AWS access key in `fixtures/legacy-platform/app/config.py` is the example
key published in AWS documentation. It is not a live credential. The
secret-shaped values a full-history gitleaks scan reported on 2026-10-21 (all
in `tests/` and `fixtures/`; see [CHANGELOG.md](CHANGELOG.md)) were reviewed
and are synthetic test values written to exercise the secrets probes.
