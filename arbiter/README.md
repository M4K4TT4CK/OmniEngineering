# Arbiter

**A polyglot repository and system evaluator that refuses to grade what it did
not actually inspect.**

| | |
|---|---|
| **Version** | 0.1.0 — pre-release, no tagged releases yet |
| **Python** | 3.11 or later |
| **Runtime dependencies** | PyYAML |
| **License** | Proprietary — see [Licensing](#licensing) |

Arbiter scans one repository, or a **system** of several, and produces findings
across seven dimensions: security, compliance, quality, drift, interface, supply
chain and assurance. Alongside the findings it reports how much of its own
rubric it was able to run, and withholds the overall grade when that coverage
falls below threshold.

It ships with native probes that require nothing installed, wraps external
analyzers through declarative adapters, maps findings to five government control
frameworks, and includes an A/B harness for comparing configurations, tools, and
builds of Arbiter itself. Every report carries a machine-checked claim ledger
recording which checks support each assertion and which abstained.

---

## Why Arbiter

Every scanner finds things. That was never the gap. The gap is what happens
next: a clean report from almost any tool looks identical whether it checked
everything and found nothing, or skipped half the repository and never said
so. A missing binary, an unconfigured rule, a stack the tool didn't
recognize — all of it quietly disappears into the same green result as a
genuine pass.

That distinction sounds small, and it is the entire reason this exists. A
report that gets used for something that matters — a security review, a
compliance package, a decision to ship — has to say "checked, and clean" in a
way that is visibly different from "could not check this part." Arbiter's
premise is not "find more bugs than the other scanner." It is that a tool
should never assert something it did not verify, and the gap between
verified and unverified should be visible in every output, not buried in a
log nobody reads.

So coverage is a first-class number, not a footnote: every probe that could
not run records why, the figure it produces is a real denominator over what
was actually read, and the overall grade is withheld outright — rather than
estimated or extrapolated — when coverage falls too low. That makes Arbiter a
more conservative tool than one that always hands back a grade. It's meant
to be.

The same discipline applies to the fix a finding hands back, not just the
finding itself. Every finding's remediation is labelled with where it came
from — a maintained, rule-specific fix; a fix pulled straight from the tool's
own output that run; a bare reference link; or nothing rule-specific at all —
so "see the docs" never gets to read the same as an actual, actionable fix,
and a report can say honestly how much of its advice is real guidance versus
a shrug.

---

## Principles

1. **Coverage is reported, never assumed.** A probe that could not run is
   recorded as *not assessed*, with a reason. It is never silently a pass.
2. **Deterministic and inferred findings never blend.** Every finding carries
   `provenance`. Inferred findings are advisory and do not gate by default.
3. **The tool never executes the target.** No `npm install`, no
   `terraform init`, no importing the target's Python.
4. **One core, many languages.** Language support arrives as probes and
   adapters, not core changes.
5. **No claim outruns its basis.** Every assertion carries the checks that
   support it and the checks that abstained, and eleven invariants are
   machine-checked before the report is written.
6. **Adaptation is deliberate, never ambient.** Learning accumulates offline; a
   scan reads one pinned knowledge version and records its hash.

## Capabilities

### Dimensions and scoring

Findings are weighted by severity times confidence, then rolled up per dimension:

| Dimension | Weight | Covers |
|---|---|---|
| `security` | 0.30 | Secrets, insecure resources, disabled protections |
| `quality` | 0.20 | Complexity, file and function size, test presence |
| `supply_chain` | 0.15 | Unpinned dependencies and actions, invented imports |
| `interface` | 0.15 | Seams between repositories in a system |
| `compliance` | 0.10 | Control-mapped resource policy |
| `drift` | 0.10 | Documentation, contracts and code disagreeing |
| `assurance` | **0.00** | Whether the checking itself is switched on |

`assurance` scores zero deliberately. A repository with four hundred `# noqa`
comments is not insecure, it is *unmeasured* — a fact about the evidence, not
about the code, and folding the two into one grade would be the exact
conflation the dimension exists to expose.

### What it detects

Thirteen native probes covering secrets, a provider-neutral resource policy over
Terraform, CloudFormation, CDK and Kubernetes, AST metrics in 11 languages,
supply-chain pinning, documentation drift, cross-repo interface seams, defects
characteristic of machine-drafted code, contract mismatches between artifacts,
and assurance. Plus adapters for `ruff`, `bandit`, `checkov`, `semgrep` and
`gitleaks`.

Full table, requirements and rationale: **[docs/probes.md](docs/probes.md)**.

### Beyond single-repository scanning

| Capability | Summary |
|---|---|
| **Multi-repo systems** | Six seam checks across repositories — constant disagreement, unprovided environment variables, ungranted IAM permissions, unexposed ports. Every finding cites spans in both repos. → [docs/systems.md](docs/systems.md) |
| **Terraform plan reading** | Resolved variables, expanded `for_each`, flattened modules. Unknown values report as *not assessed*, never as a pass. → [docs/probes.md](docs/probes.md#infrastructure-as-code) |
| **Control coverage** | NIST 800-53r5, NIST 800-171r2, SSDF 800-218, FedRAMP Moderate r5, CMMC L2 — independent packs, five resolution states, honest denominators. → [docs/compliance.md](docs/compliance.md) |
| **Claim integrity** | A claim ledger per report, ten invariants, verified before the report is written. → [docs/evidence.md](docs/evidence.md#claim-integrity) |
| **Calibration** | Offline learning from adjudicated findings; confidence moves, severity never does. → [docs/calibration.md](docs/calibration.md) |
| **A/B harness** | Compare profiles, analyzers, or two builds of Arbiter over one target. → [docs/ab-testing.md](docs/ab-testing.md) |

## Quick start

```bash
pip install -e .
arbiter scan ./my-repo
```

```bash
./tools/install_tools.sh     # optional: the five external analyzers, pinned
                              # (bash — on Windows use Git Bash or WSL)
```

Full installation, optional extras and training setup: **[SETUP.md](SETUP.md)**.

Or reach a hosted instance and install no analyzers at all:

```bash
pip install arbiter-eval
export ARBITER_SERVER=https://arbiter.example.com
export ARBITER_API_KEY=arb_...   # issued by hand by whoever runs the server
arbiter remote scan ./my-repo
```

The output is identical, because it is rendered by the same code; the analysis
happened on the server. HTTPS only — an `http://` address is refused before the
key is sent.

Pointing this at a repository of your own, rather than the examples above?
**[RUNNING-ON-YOUR-OWN-CODE.md](RUNNING-ON-YOUR-OWN-CODE.md)** walks through
install, first scan, baseline and calibration end to end.

## Commands

```bash
arbiter scan ./repo                            # analyze and report
arbiter scan ./repo --changed origin/main      # only what changed; skips what needs the whole tree
arbiter scan --system arbiter-system.yaml      # several repos, one verdict
arbiter gate ./repo --baseline .arbiter/baseline.json   # exit 1 on policy failure
arbiter probes ./repo                          # what can run here, and why not
arbiter explain f:8c41d2ae9b07                 # one finding in full
arbiter baseline arbiter-out/report.json       # snapshot current state
arbiter diff before.json after.json            # new / fixed / unchanged
arbiter verify arbiter-out/report.json         # does any claim outrun its basis?
arbiter controls arbiter-out/report.json       # control coverage, including gaps
arbiter review arbiter-out/report.json         # adjudicate a batch in one pass
arbiter feedback f:8c41 --false-positive       # adjudicate one finding
arbiter learn                                  # what has been learned
arbiter ab --spec examples/ab-native-vs-checkov.yaml    # compare two arms
arbiter dashboard                              # the trend across runs, as one page
```

Against a hosted instance, with nothing but Arbiter itself installed locally:

```bash
arbiter remote health                          # what this server is, and what it allows
arbiter remote scan ./repo                     # analyze and report, server-side
arbiter remote gate ./repo                     # exit 1 on policy failure
arbiter remote review-queue arbiter-out/report.json   # draw a queue; marks stay blank
arbiter api serve                              # run an instance yourself
arbiter mcp                                    # serve the tools over MCP, on stdio
arbiter mcp --http --root /srv/work            # ...or over HTTPS, to several people
arbiter bundle build --out ./bundle            # an offline installer: Arbiter, no analyzers
arbiter bundle verify ./bundle                 # every hash, and nothing unlisted
```

Full reference: **[docs/cli.md](docs/cli.md)**.

## Administration

### Configuration

`arbiter.yaml` at the repository root is the whole configuration surface —
profile, gate thresholds, suppressions and house rules. There is no second
config file and no implicit user-level defaults. The repository's own
`arbiter.yaml` is a working example.

→ **[docs/configuration.md](docs/configuration.md)**

### Profiles

A profile is a capability budget. A probe needing a capability the profile
forbids is skipped with that reason recorded, so the report says what the
profile cost you.

| Profile | Network | Model | For |
|---|---|---|---|
| `offline` | no | no | air-gapped and high-side environments |
| `ci` | no | no | fast, deterministic gating |
| `connected` | yes | yes | full depth |
| `audit` | yes | yes | everything, no time budget |

### Output and exit codes

`--format json,sarif,html,markdown,console`. JSON is canonical and written
first; every other format is a rendering of it. Under GitHub Actions
(`--github`, automatic when `GITHUB_ACTIONS=true`) `gate` also prints each
finding as a workflow-command annotation and appends the pull-request comment
to the job summary; see [docs/ci.md](docs/ci.md#github-actions).

Every scan and gate also appends one summary line — time, commit, grade,
coverage, counts, gate result; no evidence and no paths — to
`arbiter-out/history.jsonl`, which accumulates across runs and is what
`arbiter dashboard` renders (`--no-history` skips it).

| Code | Meaning |
|---|---|
| `0` | pass |
| `1` | gate failure |
| `2` | error |

### State

| Path | Contents | Commit it? |
|---|---|---|
| `arbiter.yaml` | configuration | yes |
| `.arbiter/baseline.json` | accepted current state | yes |
| `.arbiter/knowledge.json` | calibration ledgers | yes |
| `.arbiter/external-severity.json` | measured severities for external checks | yes |
| `arbiter-out/`, `arbiter-ab/` | run output | no — git-ignored |

### Continuous integration

```yaml
- uses: ./ci/github-action
  with:
    target: .
    profile: ci
    baseline: .arbiter/baseline.json
```

The action uploads SARIF so findings render inline on pull requests. A GitLab
template is in `ci/gitlab/`. → **[docs/ci.md](docs/ci.md)**

A ready-made two-job workflow — a fast blocking check on pull requests, a
full scan nightly — is in `examples/pull-request-gate/`, and
`RUNNING-ON-YOUR-OWN-CODE.md` walks through putting it on a real repository.

`arbiter scan --changed REF` reads only what a branch touched and records the
checks that need the whole tree as not assessed. Traefik: 59s → 6s, with the
file-scoped findings identical to a full scan on the files both read.
→ **[docs/ci.md](docs/ci.md#scanning-only-what-changed)**

### Testing

```bash
python -m pytest -q -m "not slow"   # the fast tier: about two minutes
python -m pytest -q -m slow         # the slow tier: real analyzers, about five minutes
python -m pytest -q                 # both
```

The golden-fixture tests are the ones that matter: `fixtures/legacy-platform`
holds fourteen deliberately planted defects that `.arbiter-expected.yaml`
enumerates, and any change that regresses on one of them fails the suite.

The suite is split by area under `tests/` (`test_probes_security.py`,
`test_probes_iac.py`, `test_policy_gate.py`, `test_adapters.py`,
`test_mcp_api.py` and so on), with the fixture scans in `conftest.py` and the
helpers more than one file needs in `helpers.py`. Tests marked `slow` run a
real external analyzer through the adapters (`./tools/install_tools.sh`
installs them) or scan more than the fixtures; CI runs that tier once, in its
own job, and the fast tier on every platform.

## Documentation

| Document | Contents |
|---|---|
| [SETUP.md](SETUP.md) | Installation, optional extras, enabling the training loop |
| [RUNNING-ON-YOUR-OWN-CODE.md](RUNNING-ON-YOUR-OWN-CODE.md) | Start here to point Arbiter at your own repository |
| [docs/architecture.md](docs/architecture.md) | Pipeline, module layout, fingerprinting, scoring |
| [docs/cli.md](docs/cli.md) | Full command reference, formats, exit codes |
| [docs/configuration.md](docs/configuration.md) | `arbiter.yaml`, profiles, gating, suppressions, house rules |
| [docs/probes.md](docs/probes.md) | Every check, what it requires, what it catches |
| [docs/systems.md](docs/systems.md) | Multi-repository systems and seam checks |
| [docs/compliance.md](docs/compliance.md) | Control packs and coverage states |
| [docs/evidence.md](docs/evidence.md) | Claim integrity, injection training, corpus tuning |
| [docs/calibration.md](docs/calibration.md) | Learning, adjudication, external severity |
| [docs/testing-parameters.md](docs/testing-parameters.md) | Every constant that decides a verdict, and how firmly each is held |
| [docs/ab-testing.md](docs/ab-testing.md) | The A/B harness |
| [docs/ci.md](docs/ci.md) | CI integration and continuous training |
| [docs/hosted-api.md](docs/hosted-api.md) | Running an instance, reaching one with `arbiter remote`, custody and TLS |
| [docs/mcp.md](docs/mcp.md) | The MCP surface: the tools, stdio and HTTPS, and who is allowed to call |
| [docs/bundle.md](docs/bundle.md) | The air-gapped bundle: what it carries, what it excludes and why, build, verify, install offline |
| [docs/pilot-runbook.md](docs/pilot-runbook.md) | Standing a pilot up, in the order the steps have to happen |
| [docs/pilot-terms.md](docs/pilot-terms.md) | What each tester is told happens to their code |
| [docs/licensing.md](docs/licensing.md) | Licensing requirements and the questions still open (ARB-005) |
| [CHANGELOG.md](CHANGELOG.md) | Dated entries, referenced to requirements |
| [NOTICE.md](NOTICE.md) | Third-party components and their licenses |

## Project status

Implemented through P2 plus claim integrity and calibration: the engine, policy,
resource graph, cross-repo seam checks, Terraform plan reading, a
machine-checked claim ledger and an offline learning loop. Everything documented
here works today.

Tuned against a corpus of **41 public repositories** in three populations —
deliberately vulnerable (16), well-maintained production (20) and teaching
material (5) — of which **5 are held out** and never tuned against, leaving a
tuning set of 36. On the severities that gate a build the separation is total:
zero criticals and zero highs across 2.3 million lines of well-maintained
production code. Method and limits: [docs/evidence.md](docs/evidence.md).

**The air-gapped bundle** exists without the analyzers: `arbiter bundle build`
writes Arbiter and its runtime dependencies as wheels, the knowledge file,
install scripts and a hashed manifest, and `arbiter bundle verify` refuses any
altered or extra file ([docs/bundle.md](docs/bundle.md)). Including ruff,
bandit, semgrep, checkov and gitleaks stays blocked on the redistribution
review, ARB-005; the manifest says so, and operators install them from their
own mirror. The Claude Code
skill is `.claude/skills/arbiter/SKILL.md`; the commercial control packs
(PCI DSS v4, HIPAA Security Rule, SOC 2 TSC, CIS Controls v8) sit beside the
five government packs in `src/arbiter/packs/controls/`, each declaring the
scanner-relevant subset it covers and the human work that remains. Arbiter
also runs on itself: `pr-check` gates every pull request with
`arbiter gate .` under `arbiter.yaml`. The ARB-049 debt sweep worked the
self-scan through the review flow: 482 active findings became 321 (score 71
to 81), 55 of the remainder are accepted complexity and length debt
suppressed per file until 2027-04-01, and the gate's baseline was re-cut from
the result.

## Versioning

The project is at `0.1.0` and has no tagged releases. All changes are recorded
in [CHANGELOG.md](CHANGELOG.md) under `[Unreleased]`, grouped by date and
referenced to the requirement they serve.

Reproducibility is versioned separately from the package: a scan is reproducible
given `(commit, config, knowledge version)`, and `--pin-knowledge` fails the run
if calibration has moved since a release gate was pinned.

## Licensing

Arbiter is **proprietary**. Copyright © 2026 Nicholas J Pauken, all rights
reserved — see [LICENSE](LICENSE), which `pyproject.toml` references.

**Scan output belongs to you.** Reports, findings, baselines and knowledge files
that Arbiter generates from your material are yours, with no restriction on
publishing them. That clause is in `LICENSE` today and is meant to survive into
the operative licence unchanged.

> **Administrative note:** `LICENSE` is an *interim* notice. It asserts
> ownership, reserves all rights and assigns scan output to the user, but it is
> not the operative grant — the terms on which Arbiter may be used have not been
> drafted and need qualified counsel. Do not distribute against it.

The licensing position is specified in **[docs/licensing.md](docs/licensing.md)**
— eight requirements covering the operative grant, copyright ownership,
ownership of scan output, and the redistribution review that blocks including
the analyzers in the air-gapped bundle. Tracked as ARB-005.

Arbiter depends only on PyYAML at runtime. The external analyzers it adapts are
neither vendored nor redistributed — each is installed separately by the
operator and governed by its own license. Those licenses, and the review still
required before shipping an air-gapped bundle that includes them, are recorded
in [NOTICE.md](NOTICE.md).
