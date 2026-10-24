# Reading a Report

What a scan writes, and what each part means. The examples come from
`fixtures/legacy-platform`, a small repository with fourteen planted defects:

```bash
arbiter scan fixtures/legacy-platform --no-adapters --no-open --out /tmp/arb-demo
```

`/tmp/arb-demo/report.json` is the canonical record. The console summary, the
HTML page, Markdown and SARIF are renderings of it
([cli.md](cli.md#output-formats)).

## Contents

- [The console summary](#the-console-summary)
- [Grade and coverage](#grade-and-coverage)
- [Not assessed](#not-assessed)
- [A finding](#a-finding)
- [Precision tiers](#precision-tiers)
- [Model verdicts](#model-verdicts)
- [The gate block](#the-gate-block)
- [Other top-level keys](#other-top-level-keys)

---

## The console summary

```text
  arbiter 0.1.0  ·  root  ·  profile=offline
  1 repo(s): root(168 loc)
  stacks: github_actions, python, terraform

  3 critical  3 high  4 medium  8 low  5 info
  tiers: 0 verified · 23 advisory · 0 inferred (tier_mode legacy: gate reads every tier)
  overall 61.0/100   coverage 98%

  DIMENSION        SCORE   COVERAGE   FINDINGS
  ...
  FINDINGS
  critical root:app/config.py:4  AWS access key ID committed
           arbiter/secrets.aws-access-key  f:cf802335c8f2  [advisory] rule on probation: not declared verified
```

Each finding line gives severity, `repo:path:line`, the title, the rule id,
the finding id (use it with `arbiter explain`), and the precision tier with
its reason. `--limit` sets how many findings the console shows (default 40);
the JSON always holds every one.

## Grade and coverage

`scorecard` holds a score from 0 to 100 and a coverage fraction per
dimension, and an `overall` grade weighted across dimensions
([configuration.md](configuration.md#scoring) has the weights).

Coverage is checks that ran divided by checks that applied. A probe that
could not run (a missing analyzer, a missing grammar, a profile that forbids
the network) lowers coverage by exactly what it would have checked.

When overall coverage falls below `score.coverage_threshold` (default 0.6),
`scorecard.withheld` is `true`, `overall` is empty and `withheld_reason` says
why. A partial scan (`--changed`, `--only-files`) always withholds the grade.
A withheld grade is not a low grade; it is no grade.

## Not assessed

The console and Markdown reports list every probe that did not run, with its
reason, in a **NOT ASSESSED** block; in the JSON each entry in `probes` has a
`status` and a `reason`. `not applicable` (nothing to check, such as
cross-repo seams on a single repository) leaves the coverage denominator; a
probe that was prevented from running stays in it.

A finding count of zero for a check that is not assessed means nothing about
that check. The **Read first** block at the top of the Markdown and HTML
reports says when the grade is withheld, checks were skipped, or findings at
or above `verify.min_severity` were not model-verified.

## A finding

```json
{
  "rule_id": "arbiter/secrets.aws-access-key",
  "title": "AWS access key ID committed",
  "dimension": "security",
  "severity": "critical",
  "confidence": "high",
  "provenance": "deterministic",
  "location": {"path": "app/config.py", "start_line": 4},
  "evidence": "aws-access-key:AKIA************MPLE",
  "cwe": ["CWE-798"],
  "controls": ["NIST-800-53r5:IA-5", "NIST-800-53r5:SC-28"],
  "status": "new",
  "suppressed": false,
  "id": "f:cf802335c8f2",
  "tier": "advisory",
  "tier_reason": "rule on probation: not declared verified",
  "requirements": [],
  "verification": {}
}
```

| Field | Meaning |
|---|---|
| `severity` | how much the defect matters: `critical`, `high`, `medium`, `low`, `info`. Set by the rule and by `probes.severity_overrides`; calibration never changes it |
| `confidence` | how sure the rule is that this is a real instance; calibration may move it |
| `provenance` | `deterministic` (a rule matched) or `inferred` (a model said so). Inferred findings never fail a gate unless `gate.gate_on_inferred` is set |
| `evidence` | the matched text, with secrets masked |
| `status` | `new` or `existing` against the baseline (`--baseline`) |
| `suppressed`, `suppression_reason` | set by a `suppress:` entry; the finding stays in the report ([configuration.md](configuration.md#suppressions)) |
| `id` | a fingerprint that ignores the line number, so a baseline survives reformatting ([architecture.md](architecture.md#fingerprinting)) |
| `remediation`, `remediation_source` | the fix, and where it came from: `table` (written for this rule), `field` (taken from the external tool's own output this run), `link` (a reference link only), `default` (nothing rule-specific) |
| `scope_note` | the repository's own docs claim this location is handled elsewhere; unverified, and it changes nothing |
| `cwe`, `controls` | weakness ids and the control-framework controls the rule maps to ([compliance.md](compliance.md)) |
| `requirements` | the Arbiter requirement ids the rule was built or fixed under |
| `tier`, `tier_reason` | precision tier ([below](#precision-tiers)) |
| `verification` | the model's verdict, when the verify pass ran ([below](#model-verdicts)) |

`arbiter explain <id>` prints one finding in full.

## Precision tiers

Every finding has a tier:

- `verified`: the rule is declared verified, the precision ledger admits it,
  and either a model confirmed this finding or the profile cannot ask a model
  and the rule's measured lower bound is at least 0.99.
- `advisory`: everything else. Today that is every deterministic finding,
  because no rule is declared verified yet.
- `inferred`: found by a model.

The tier never hides or re-grades a finding: an advisory critical is still
critical. It decides only what a `tier_mode: tiered` gate reads. The default
gate (`legacy`) reads every tier. Details:
[precision.md](precision.md#tiers). `scan_scope.tiers` holds the counts and
how many ledger rules are admitted.

## Model verdicts

Under the `assisted` profile, or with `--model-verify`, each finding at or
above `verify.min_severity` gets
`verification: {verdict, reason, model, prompt_version, cached, line}`.
`verdict` is `confirmed`, `rejected`, `unsure` or `not_verified`. Not
verified is not confirmed: it means no key, no budget left, or a failed
call. `scan_scope.verification` holds the counts. Details:
[precision.md](precision.md#model-verification).

## The gate block

```json
"gate": {"passed": false, "reasons": ["3 finding(s) at or above critical"],
         "considered": 23, "gate_on_inferred": false, "tier_mode": "legacy",
         "failing_ids": ["f:4fc72a8cdfaa", "f:904314e96155", "f:cf802335c8f2"]}
```

`reasons` says what failed; `failing_ids` names the findings behind the
severity and new-finding reasons; `considered` is how many findings the gate
read after suppressions, provenance and tiers. `arbiter gate` exits 1 when
`passed` is false.

## Other top-level keys

| Key | Contents |
|---|---|
| `scan_scope` | full or partial scan, cache hits and misses, tier counts, verification counts |
| `probes` | every probe: `status` (ran, skipped, error, ...), `reason`, `checks`, `finding_count` |
| `claims`, `integrity` | the claim ledger and the result of checking it against the twelve invariants; `arbiter verify` re-checks it ([evidence.md](evidence.md#claim-integrity)) |
| `controls` | per-framework control coverage summary; `arbiter controls` shows the detail |
| `learning` | the knowledge version and what calibration changed ([calibration.md](calibration.md)) |
| `rules` | every rule that fired, with its requirements and tier |
| `stacks`, `loc_by_language`, `loc_by_role`, `repos` | what was scanned |
| `fingerprint` | the finding-id scheme in use (`occurrence-v1`); a baseline cut under an older scheme needs `arbiter baseline --migrate` |
