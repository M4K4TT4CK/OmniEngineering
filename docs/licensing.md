# Licensing

> **Not legal advice.** This document records the licensing decision the
> copyright holder made, what follows from it mechanically, and which questions
> remain open. Where a statement is an inference rather than a decision or a
> quotation of the licence, it says so.

Tracked as **ARB-005**. Status: **pending** — the licence is decided and in
force; one requirement, L-6 (the redistribution review for bundling the
analyzers), is still open and only blocks shipping the analyzers inside the
air-gapped bundle.

## Contents

- [Current position](#current-position)
- [What the licence means for you](#what-the-licence-means-for-you)
- [Requirements](#requirements)
- [Third-party obligations](#third-party-obligations)
- [Distribution scenarios](#distribution-scenarios)
- [Open questions](#open-questions)
- [History: the superseded proprietary draft](#history-the-superseded-proprietary-draft)

---

## Current position

**Decided 2026-10-21 by the copyright holder, Nicholas J Pauken:** Arbiter is
licensed under the **Apache License, Version 2.0**, and the repository
(`paukennick/arbiter`) stays public. This replaces the interim "all rights
reserved" notice that `LICENSE` carried from 2026-09-12, and drops the planned
proprietary per-repository commercial licence (the L-3 draft grant, kept below
as history). Apache-2.0 also matches OmniEngineering, which vendors Arbiter as
a git subtree and is itself Apache-2.0.

| Fact | Location | State |
|---|---|---|
| Licence text | `LICENSE` | the standard, unmodified Apache-2.0 text |
| Copyright and attribution notice | `NOTICE` | "Arbiter / Copyright 2026 Nicholas J Pauken", followed by the OmniEngineering workspace's own NOTICE |
| Licence referenced from metadata | `pyproject.toml` | `license = { file = "LICENSE" }` plus the `License :: OSI Approved :: Apache Software License` classifier |
| Ownership of scan output | `README.md`, `NOTICE`, this file | stated: output belongs to the user (L-4) |
| Inbound contribution terms | `CONTRIBUTING.md` | outside contributions not accepted yet; Apache-2.0 §5 stated as the basis if that changes |
| Trademark policy | `TRADEMARKS.md` | the OmniEngineering identity policy; Apache-2.0 §6 grants no trademark rights |
| Third-party component inventory | `NOTICE.md` | present, accurate |
| Redistribution review for bundled analyzers | not started | **absent** — L-6 |

## What the licence means for you

This is a plain summary, not a substitute for [LICENSE](../LICENSE):

- You may use, copy, modify and redistribute Arbiter, including commercially,
  under the terms of Apache-2.0 — chiefly, keep `LICENSE` and `NOTICE` with any
  redistribution and mark files you changed (§4).
- The licence includes a patent grant from contributors (§3) and disclaims
  warranty and liability (§7, §8).
- It grants no right to use the project's names or marks beyond describing the
  origin of the work (§6).

**Scan output belongs to you.** Apache-2.0 licenses the software; it says
nothing about what the software produces from your material, and Arbiter's
copyright holder claims nothing over it. Reports, findings, baselines,
knowledge files and any other artefact Arbiter generates from your own code are
yours, with no restriction on publishing them — in accreditation packages,
audit evidence, pull-request comments or anywhere else. (Rule packs, templates
and other Arbiter source that you copy into your own material remain under
Apache-2.0 as part of the Work.)

## Requirements

The eight requirements written on 2026-09-12 were drafted for a proprietary
licence. Their state under Apache-2.0:

| Requirement | State |
|---|---|
| **L-1** — the repository carries an operative licence, referenced from `pyproject.toml` and the README | **met** — `LICENSE` is Apache-2.0 |
| **L-2** — copyright ownership asserted | **met** — Nicholas J Pauken, individually (answered 2026-09-14); notice in `NOTICE` |
| **L-3** — the grant states permitted use precisely | **met by adoption** — Apache-2.0 answers each row of the original table (who, for what, modification, redistribution, term, warranty); the per-repository draft is superseded |
| **L-4** — scan output belongs to the user | **met** — stated in `README.md`, `NOTICE` and above |
| **L-5** — third-party components remain separable | **met** — `NOTICE.md` inventories them; Apache-2.0 grants rights only in the Work |
| **L-6** — bundled redistribution requires a completed review | **open** — see below |
| **L-7** — fixtures carry no live or third-party-owned credentials | **met** for what was checked — the 2026-10-21 full-history gitleaks scan reported only synthetic test values and the published AWS example key (`CHANGELOG.md`) |
| **L-8** — contribution terms stated before outside contributions | **met** — `CONTRIBUTING.md` says outside contributions are not accepted yet and names Apache-2.0 §5 as the inbound basis; whether to add a CLA or DCO is the owner's open decision |

### L-6 — Bundled redistribution requires a completed review

No distribution that *includes* the third-party analyzer binaries may ship
until each component's redistribution terms have been reviewed and recorded —
with particular attention to `semgrep` (LGPL-2.1).

**Acceptance:** a per-component review recorded in `NOTICE.md`, dated, naming
who performed it. This blocks only including the analyzers in the air-gapped
bundle. Arbiter invokes `semgrep` as a separate executable that the operator
installs and does not bundle it, and the bundle `arbiter bundle build` writes
today carries Arbiter and its runtime dependencies only
([bundle.md](bundle.md)). Relicensing Arbiter under Apache-2.0 does not change
this question: it is about semgrep's licence, not Arbiter's.

## Third-party obligations

Current inventory, from [NOTICE.md](../NOTICE.md):

| Component | Licence | Redistributed today | Obligation if bundled |
|---|---|---|---|
| PyYAML | MIT | only as its unmodified wheel inside a built bundle | attribution |
| tomli (Python < 3.11) | MIT | only as its unmodified wheel inside a bundle built for Python < 3.11 | attribution |
| ruff | MIT | no — operator installs | attribution |
| bandit | Apache-2.0 | no — operator installs | attribution, NOTICE, state changes |
| checkov | Apache-2.0 | no — operator installs | attribution, NOTICE, state changes |
| gitleaks | MIT | no — operator installs | attribution |
| **semgrep** | **LGPL-2.1 (CLI)** | no — operator installs | **requires review before bundling** |

The "redistributed today" column is the load-bearing one. No analyzer is
redistributed, which is why nothing is currently blocked. Including the
analyzers in the air-gapped bundle would change those rows, which is what L-6
guards. (That the PyYAML and tomli wheels' own `dist-info` licence files meet
the MIT attribution condition is an inference, not a reviewed determination.)

## Distribution scenarios

| Scenario | Licensing posture | Blocked? |
|---|---|---|
| Use, internal or public, from source | Apache-2.0 | no |
| Source or a fork redistributed by anyone | Apache-2.0 §4: keep `LICENSE` and `NOTICE`, mark changed files | no |
| Air-gapped bundle without the analyzers | Apache-2.0 for Arbiter; MIT for the PyYAML/tomli wheels | no |
| Air-gapped bundle including the analyzers | all of the above, plus L-6 | **yes** until L-6 is done |
| Hosted/SaaS offering (ARB-018) | Apache-2.0 for Arbiter; L-6 does not apply — see below | custody terms are contractual, not licensing |

**A correction about the hosted scenario** (recorded 2026-09-13). An earlier
note here and in `.ai/project-context.md` said that "LGPL obligations differ
again for network use". That points the wrong way. LGPL-2.1 obligations attach
to *conveying a copy*, and it carries no network-use clause — that is the AGPL,
which `semgrep`'s CLI is not under. Running `semgrep` server-side and returning
findings over HTTP conveys no copy to the caller, so **L-6 does not gate a
hosted offering**. This is a reading of the licence text, not a reviewed legal
determination.

What a hosted service still needs is custody terms for customer source —
retention, deletion, confidentiality, incident notification and sub-processor
disclosure. Those are contractual questions, not copyright ones, and
Apache-2.0 does not answer them. See [hosted-api.md](hosted-api.md).

## Open questions

Questions 1 and 2 of the original list were answered on 2026-09-14
(individual ownership; the per-repository model), and question 2's answer is
superseded by the 2026-10-21 decision. What remains:

1. **Does bundling `semgrep`** (LGPL-2.1) into an air-gapped archive constitute
   distribution requiring relinking provisions, or does invoking it as a
   separate executable keep it at arm's length? (L-6; still needs a person to
   review and sign.)
2. **Do the government control packs** (NIST, FedRAMP, CMMC) carry any
   attribution or derivation constraint? They are US Government publications and
   are generally not copyrightable, but the *pack encoding* is original work.
   The commercial packs (PCI DSS, SOC 2 TSC) transcribe identifiers from
   licensed documents ([compliance.md](compliance.md)).
3. **Should source files carry per-file Apache-2.0 headers**, or is the root
   `LICENSE` plus `NOTICE` sufficient? Undecided; none have been added.
4. **Inbound contribution terms** — whether to accept outside contributions at
   all, and if so whether to rely on Apache-2.0 §5 alone or add a DCO or CLA.
   Undecided; see [CONTRIBUTING.md](../CONTRIBUTING.md).
5. **A trademark policy for the name "Arbiter".** `TRADEMARKS.md` is the
   OmniEngineering identity policy and names only OmniEngineering marks. No
   Arbiter-specific policy has been adopted.

## History: the superseded proprietary draft

**Superseded 2026-10-21.** Kept as a short record of the reasoning, not as
terms anyone is bound by. Nothing here was ever in force.

- **2026-09-12.** `LICENSE` became an interim notice: it asserted copyright,
  reserved all rights, granted none, and assigned scan output to the user
  (L-4's substance). It said explicitly that it was not the operative grant.
  The reasoning was that an explicit refusal of permission is a defensible
  placeholder while terms are unsettled, and that ownership of output had to be
  stated from the start because the product is a report about the user's code.
- **2026-09-14.** The owner answered the first two open questions: copyright is
  held individually, and the intended model was a licence per named repository
  or per named group of repositories (a "system", matching Arbiter's own scan
  unit). A draft L-3 grant table was written from those answers, with gaps left
  marked: how repositories join or leave a system licence, whether consulting
  delivery against a client's repository needs its own licence, modification
  rights, term and termination, and liability limits. It was to take effect at
  a later date that was never set, and testers were told no restriction applied
  in the meantime.
- **2026-10-20.** The repository was found to be public while `LICENSE` still
  reserved all rights (`docs/gaps-and-next-steps.md`).
- **2026-10-21.** The owner chose Apache-2.0 and kept the repository public.
  The open items in the draft grant fall away with the model they belonged to;
  `LICENSE` §3's statement that scan output belongs to the user is carried
  forward above and in `README.md`.
