# Licensing Requirements

> **This document states requirements, not legal advice.** It records what the
> licensing position must achieve and which questions remain open. The
> operative licence text must be drafted or reviewed by qualified counsel
> before any distribution. Nothing here is a substitute for that review.

Tracked as **ARB-005**. Status: **pending** — the requirements below are
settled; the operative licence instrument that satisfies them does not yet
exist.

**Progress, 2026-09-12.** L-2 and L-8 are satisfied, and L-1 is satisfied
*provisionally*: a `LICENSE` file now exists and asserts ownership, reserves all
rights and assigns scan output to the user (L-4's substance), but it explicitly
is not the operative grant. L-3 still cannot be drafted, because it follows from
open questions 1 and 2 below, and L-6 is untouched. What remains needs counsel.

**Progress, 2026-09-14.** Open questions 1 and 2 are answered: copyright is
held individually by Nicholas J Pauken, not an entity, and the licensing model
is per-repository-or-per-system — a client buys a licence against either one
named repository or a named group of them, matching Arbiter's own repository/
system distinction (`HANDOFF.md`). This scheme is not in force yet: it takes
effect at a later date to be set, and while testing continues nobody —
including a prospective client — is bound by any licence restriction; today's
`LICENSE` (no rights granted) is what actually governs until that date is set.
A draft of L-3's grant table follows that section below, built from these
answers plus what L-4 through L-6 had already settled. It is a drafting aid,
not counsel-reviewed text. Still unanswered and called out inline: how
repositories are added to or removed from a system licence over its life,
whether consulting delivery against a client's repository counts as a second
licensed repository, term/termination, modification rights, and liability
limits.

## Contents

- [Current position](#current-position)
- [Why this needs settling](#why-this-needs-settling)
- [Requirements](#requirements)
- [Third-party obligations](#third-party-obligations)
- [Distribution scenarios](#distribution-scenarios)
- [Open questions for counsel](#open-questions-for-counsel)

---

## Current position

| Fact | Location | State |
|---|---|---|
| Licence referenced from metadata | `pyproject.toml` | present — `license = { file = "LICENSE" }` |
| `LICENSE` file | repository root | present — **interim notice, not a grant** |
| Copyright notice | `LICENSE`, `pyproject.toml` | present — Nicholas J Pauken, 2026 |
| Ownership of scan output assigned to the user | `LICENSE` §3 | present |
| Inbound contribution terms | `CONTRIBUTING.md` | present — contributions not accepted |
| Operative grant (who may use it, for what) | not drafted | **absent** |
| Third-party component inventory | `NOTICE.md` | present, accurate |
| Redistribution review for bundled analyzers | not started | **absent** |

Ownership is now asserted and scan output is assigned to the user. What is still
missing is the grant itself: a recipient of this code today has a clear statement
of who owns it and an explicit refusal of permission, which is a defensible
interim position but is not a licence. Closing that gap is what remains.

## Why this needs settling

Three things already depend on an answer:

1. **The air-gapped bundle ships without the analyzers.** `arbiter bundle
   build` ([bundle.md](bundle.md)) carries Arbiter and its dependencies only,
   and its manifest says why. A bundle that ships the five external analyzers
   is a redistribution, and redistribution is exactly what the current NOTICE
   says has not been reviewed.
2. **`semgrep` is LGPL-2.1.** The other four adapted tools are permissive
   (MIT/Apache-2.0). LGPL carries obligations that permissive licences do not,
   and those obligations differ sharply between "the operator installs it" and
   "we ship it".
3. **Compliance output invites scrutiny.** Arbiter produces artefacts for
   accreditation packages. A tool whose own licensing is unstated is a poor fit
   for an audit trail, and a reviewer is entitled to ask.

## Requirements

### L-1 — The repository must carry an operative licence

A `LICENSE` file at the repository root, referenced from `pyproject.toml` and
the README. Until it exists, no terms are granted and every other requirement
here is unenforceable.

**Acceptance:** `LICENSE` exists; `pyproject.toml` points at it; README's
licensing section cites it rather than describing its absence.

### L-2 — Copyright ownership must be asserted

A copyright line naming the owner and year, applied consistently. Ownership must
be settled before terms are drafted, because the terms are granted *by* the
owner — including whether any contribution to date was made under an employment
or contractor agreement that assigns it elsewhere.

**Acceptance:** owner identified in writing; notice present in `LICENSE` and, if
adopted, in source headers.

### L-3 — The grant must state permitted use precisely

"Proprietary" is a category, not a grant. The instrument must answer, explicitly:

| Question | Must state |
|---|---|
| Who may use it | named licensee, organisation, or per-seat |
| For what | internal evaluation, production use, consulting delivery |
| May they modify | yes/no, and whether modifications must be reported |
| May they redistribute | almost certainly no — but it must *say* so |
| May output be published | **yes, unambiguously** — see L-4 |
| Term and termination | perpetual, subscription, or revocable |
| Warranty and liability | disclaimer, limitation |

**Acceptance:** each row answered in the licence text.

#### Draft grant (pending counsel review — not operative)

Built from the two settled points — copyright held individually by
Nicholas J Pauken; one licence per named repository — plus what L-4 through
L-6 already decided. Anything marked `[OPEN]` is a real gap, not a style
choice, and blocks this from being the acceptance-satisfying text on its own.

| Question | Draft answer |
|---|---|
| Who may use it | The named licensee (individual or organisation) identified in the licence instrument itself, not transferable without the copyright holder's written consent. |
| Scope | Resolved 2026-09-14: a licence is purchased against either a single named repository or a named group of repositories (a licensed **system**, matching Arbiter's own system-scan unit in `HANDOFF.md`) — the licence instrument states which, and lists the repository or repositories covered by canonical remote URL as of the licence date. Scanning any repository outside that list, including a fork or mirror of one that is covered, requires a separate licence or an amendment adding it to the existing one. `[OPEN]` — how repositories are added to or removed from an existing system licence over the licence's life is not yet specified. |
| For what | Internal evaluation and production use against the licensed repository are covered. `[OPEN]` — consulting delivery (running the licensed copy against a client's repository, as opposed to the licensee's own) is a different repository under this scope and is not covered by a single licence; whether that needs its own model is unanswered. |
| May they modify | Draft default: no, without the copyright holder's written consent. `[OPEN]` — not one of the two answered questions; carried here as the conservative default for proprietary per-unit software, not a decision. |
| May they redistribute | No. |
| May output be published | Yes, unambiguously — see L-4; this licence does not narrow it. |
| Term and termination | `[OPEN]` — perpetual per repository, or subscription, is unanswered. Matters here specifically because "one licence per repository or system" reads differently as a one-time purchase than as a recurring one. |
| Warranty and liability | Carries `LICENSE` §5's "as is" disclaimer forward. `[OPEN]` — no liability cap or limitation is drafted yet. |
| Effective date | Resolved 2026-09-14: this scheme is not in force yet. It takes effect at a date to be set later, before which no client-facing enforcement of it exists. |

**During testing, no licence restriction applies.** Until the effective date
above is set, nobody running Arbiter — including a prospective client
evaluating it — is bound by the per-repository/per-system scheme drafted here.
`LICENSE` as it stands today (no rights granted, all rights reserved) is what
actually governs in the interim; this draft describes what replaces it, not
what applies now. Nothing in this document should be read as, or communicated
to a client as, a restriction currently in effect.

**Acceptance (unchanged from above):** each row of the original table answered
in the licence text — this draft closes "who," "scope," "effective date," and
narrows "for what" and "redistribute," but does not close the row on its own
until the remaining `[OPEN]` items are resolved.

### L-4 — Scan output must belong to the user

Arbiter's product is a report about the user's own code. The licence must not
claim rights over findings, reports, baselines or knowledge files generated by a
user's run, and must not restrict publishing them.

This matters more than usual here: reports are intended for accreditation
packages and pull-request comments. A licence that left ownership of output
ambiguous would make the tool unusable for its primary purpose.

**Acceptance:** an explicit clause assigning output to the user.

### L-5 — Third-party components must remain separable

Arbiter depends on PyYAML at runtime and *adapts* five external analyzers
without vendoring them. The licence must not imply any grant over those tools,
and `NOTICE.md` must remain accurate as the inventory.

**Acceptance:** `NOTICE.md` lists every third-party component, its licence and
whether it is redistributed; the licence disclaims grants over them.

### L-6 — Bundled redistribution requires a completed review

No distribution that *includes* third-party binaries may ship until each
component's redistribution terms have been reviewed and recorded — with
particular attention to `semgrep` (LGPL-2.1).

**Acceptance:** a per-component review recorded in `NOTICE.md`, dated, naming
who performed it. This requirement blocks the air-gapped bundle roadmap item.

### L-7 — Test fixtures must not carry live or third-party-owned credentials

`fixtures/legacy-platform/app/config.py` contains the AWS documentation example
key. That is published by AWS as an example and is not live, and `NOTICE.md`
already says so. The requirement is that this remains true and is checked:
planted defects must never use a real credential, and fixtures must not
incorporate third-party code under incompatible terms.

**Acceptance:** NOTICE states the provenance of every planted credential; no
fixture contains a live secret.

### L-8 — Contribution terms must be stated before outside contributions

If the project accepts contributions, inbound terms must be settled first — a
CLA, a DCO, or an explicit statement that contributions are not accepted.
Retrofitting this after accepting a patch is materially harder.

**Acceptance:** stated in `CONTRIBUTING.md`, or an explicit "no external
contributions" note.

## Third-party obligations

Current inventory, from [NOTICE.md](../NOTICE.md):

| Component | Licence | Redistributed today | Obligation if bundled |
|---|---|---|---|
| PyYAML | MIT | no — pip dependency | attribution |
| ruff | MIT | no — operator installs | attribution |
| bandit | Apache-2.0 | no — operator installs | attribution, NOTICE, state changes |
| checkov | Apache-2.0 | no — operator installs | attribution, NOTICE, state changes |
| gitleaks | MIT | no — operator installs | attribution |
| **semgrep** | **LGPL-2.1 (CLI)** | no — operator installs | **requires review before bundling** |

The "redistributed today" column is the load-bearing one. Every entry reads
*no*, which is why nothing is currently blocked. The air-gapped bundle would
change every row, which is what L-6 guards.

## Distribution scenarios

| Scenario | Licensing posture | Blocked? |
|---|---|---|
| Internal use only, source stays in-house | L-1, L-2 sufficient | no |
| Source delivered to a named customer | L-1 … L-5, L-8 | no, once L-1–L-3 drafted |
| Air-gapped bundle including analyzers | all, plus L-6 | **yes** until L-6 done |
| Hosted/SaaS offering (ARB-018) | L-3 term structure differs; L-6 does not apply — see below | term structure and custody need counsel |

**A correction about the hosted scenario.** An earlier note here and in
`.ai/project-context.md` said that "LGPL obligations differ again for network
use". That points the wrong way. LGPL-2.1 obligations attach to *conveying a
copy*, and it carries no network-use clause — that is the AGPL, which
`semgrep`'s CLI is not under. Running `semgrep` server-side and returning
findings over HTTP conveys no copy to the caller, so **L-6 does not gate a
hosted offering**; hosting triggers fewer third-party obligations than the
air-gapped bundle, not more.

What still needs counsel for hosting is L-3's term structure, which differs for
a service from a delivered copy, and the custody of customer source — retention,
deletion, confidentiality, incident notification and sub-processor disclosure.
Those are contractual questions, not copyright ones. See
[hosted-api.md](hosted-api.md).

## Open questions for counsel

1. **Who owns the copyright** — individual, or an entity to be formed?
2. **Is the intended model** per-seat, per-repository-scanned, or site licence?
   L-3's term structure follows from this.
3. **Does bundling `semgrep`** (LGPL-2.1) into an air-gapped archive constitute
   distribution requiring relinking provisions, or does invoking it as a
   separate executable keep it at arm's length?
4. **Do the government control packs** (NIST, FedRAMP, CMMC) carry any
   attribution or derivation constraint? They are US Government publications and
   are generally not copyrightable, but the *pack encoding* is original work.
5. **Should source headers** carry per-file notices, or is a root `LICENSE`
   sufficient for the intended distribution model?

Until questions 1 and 2 are answered, L-3 cannot be drafted, and L-1 cannot be
satisfied with anything better than a placeholder.
