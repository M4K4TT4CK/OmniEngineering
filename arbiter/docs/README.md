# Arbiter documentation

Start with the [project README](../README.md): what Arbiter is, install, a
first scan, and gating. Then pick what you need below.

## Using Arbiter

| Document | Contents |
|---|---|
| [../SETUP.md](../SETUP.md) | Installation, optional extras, external analyzers, Windows notes |
| [../RUNNING-ON-YOUR-OWN-CODE.md](../RUNNING-ON-YOUR-OWN-CODE.md) | Walkthrough: first scan, baseline, adjudication, pull-request gate, system mode |
| [cli.md](cli.md) | Every command and flag, output formats, exit codes |
| [reading-a-report.md](reading-a-report.md) | Every field of `report.json` and what the console shows |
| [configuration.md](configuration.md) | Every `arbiter.yaml` key: profiles, gating, precision tiers, model verification, suppressions, house rules, scoring |
| [probes.md](probes.md) | Every check, what it needs, what it catches; language packs and `taint:` data flow |
| [ci.md](ci.md) | GitHub Actions, GitLab, partial scans with `--changed`, Arbiter's own CI |
| [systems.md](systems.md) | Multi-repository systems and seam checks |
| [compliance.md](compliance.md) | The ten control packs and coverage states |
| [cwe-top25.md](cwe-top25.md) | The 2024 CWE Top 25, per language: assessed, gap, out of reach, not applicable |
| [ab-testing.md](ab-testing.md) | Comparing profiles, analyzers or two builds of Arbiter |

## How good the findings are

| Document | Contents |
|---|---|
| [precision.md](precision.md) | Precision tiers, the per-rule ledger and its CI guard, the label format, the model verification pass, and the current numbers |
| [testing-harness.md](testing-harness.md) | The AI-written corpora (and the held-out one), human-label sampling, regression guards, mutation variants, ground truth and model labels |
| [evidence.md](evidence.md) | Claim integrity, injection training, corpus tuning |
| [calibration.md](calibration.md) | Learning from your verdicts; external-analyzer severity |
| [testing-parameters.md](testing-parameters.md) | Every constant that decides a verdict, and how firmly each is held |

## Hosted, MCP and offline use

| Document | Contents |
|---|---|
| [hosted-api.md](hosted-api.md) | Running an instance, `arbiter remote`, custody and TLS |
| [mcp.md](mcp.md) | The MCP tools, stdio and HTTPS, who may call |
| [bundle.md](bundle.md) | The air-gapped bundle: build, verify, install offline |
| [pilot-runbook.md](pilot-runbook.md) | Standing a pilot up, step by step |
| [pilot-terms.md](pilot-terms.md) | What each tester is told happens to their code |

## The project

| Document | Contents |
|---|---|
| [architecture.md](architecture.md) | Modules, the scan pipeline, fingerprinting, caching, scoring |
| [gaps-and-next-steps.md](gaps-and-next-steps.md) | A dated snapshot of gaps and the plan (2026-10-21, with a status note) |
| [licensing.md](licensing.md) | The Apache-2.0 decision and third-party obligations |
| [../CHANGELOG.md](../CHANGELOG.md) | Dated changes, by requirement |
| [../NOTICE.md](../NOTICE.md) | Third-party components and their licenses |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | How to report defects; contributions are not accepted yet |
| [`../.claude/skills/arbiter/SKILL.md`](../.claude/skills/arbiter/SKILL.md) | The Claude Code skill for working with Arbiter |
