# Third-party components

The adapters in `src/arbiter/packs/adapters/` invoke external analyzers but do
not vendor or redistribute them. Each is installed separately by the operator.
Their licenses apply to those tools, not to Arbiter.

The **Redistributed** column is the load-bearing one: every entry reads *no*,
which is why no third-party obligation is currently triggered.

| Component | License | Role | Redistributed |
|---|---|---|---|
| PyYAML | MIT | runtime dependency | no — installed by pip |
| ruff | MIT | optional analyzer | no — installed by the operator |
| bandit | Apache-2.0 | optional analyzer | no — installed by the operator |
| checkov | Apache-2.0 | optional analyzer | no — installed by the operator |
| semgrep | LGPL-2.1 (CLI) | optional analyzer | no — installed by the operator |
| gitleaks | MIT | optional analyzer | no — installed by the operator |

Before building a distributable air-gapped bundle that *does* include these
binaries, every row above changes and each license must be reviewed for
redistribution terms — `semgrep` (LGPL-2.1) in particular.

**That review has not been done.** It is tracked as requirement L-6 in
[docs/licensing.md](docs/licensing.md), which blocks including these binaries
in the air-gapped bundle until it is complete and recorded here with a date and
a reviewer. The bundle `arbiter bundle build` writes today carries Arbiter and
its runtime dependencies only, and its manifest says so
([docs/bundle.md](docs/bundle.md)).

The AWS access key in `fixtures/legacy-platform/app/config.py` is the example
key published in AWS documentation. It is not a live credential.
