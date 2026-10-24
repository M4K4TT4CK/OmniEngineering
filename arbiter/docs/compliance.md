# Control Coverage

Ten control packs ship with Arbiter: five government frameworks, four
commercial ones and the CWE Top 25. `--framework` takes the framework id that
`arbiter controls --list` prints (for example `NIST-800-218`), not the pack
file name.

| Pack | Framework |
|---|---|
| `nist-800-53r5` | NIST SP 800-53 Rev. 5 |
| `nist-800-171r2` | NIST SP 800-171 Rev. 2 |
| `nist-800-218-ssdf` | NIST SSDF (SP 800-218) |
| `fedramp-moderate-r5` | FedRAMP Moderate Rev. 5 |
| `cmmc-l2` | CMMC Level 2 |
| `pci-dss-v4` | PCI DSS v4.0 — 32 of the 63 x.y requirement sections enumerated |
| `hipaa-security-rule` | HIPAA Security Rule (45 CFR 164 Subpart C) — 32 of 59 standards and implementation specifications |
| `soc2-tsc-2017` | SOC 2, AICPA Trust Services Criteria 2017 — 29 of 61 criteria |
| `cis-controls-v8` | CIS Controls v8 — 41 of 153 safeguards, implementation group in the title |
| `cwe-top25-2024` | 2024 CWE Top 25, with per-language applicability (see [below](#cwe-top-25-per-language)) |

Each is an **independent** pack — controls map straight to checks, never routed
through a hub framework, because chaining two approximate crosswalks produces a
compliance claim two translations removed from anything that ran.

Packs are data files in `src/arbiter/packs/controls/`, not code.

## The states

Every control resolves to one of five states, and the split between the last
three is the entire point (a sixth, `not_applicable`, exists only for packs
with per-language applicability; see [below](#cwe-top-25-per-language)):

| State | Meaning |
|---|---|
| `satisfied` | A covering check ran, applied, and found nothing. |
| `violated` | A covering check fired. |
| `not_assessed` | A check covers this on paper but did not run here — tool absent, value unknown until apply. **Not a pass.** |
| `no_coverage` | Assessable in principle; Arbiter has no check for it. |
| `not_automatable` | No static analyzer can ever assess this — personnel screening, physical access, incident-response exercises. A person must. |

Plus `not_enumerated`: controls the pack does not list at all, counted against
the framework's real published size so a pack covering twenty controls cannot
report full coverage.

## Reading a coverage report

```console
$ arbiter controls arbiter-out/report.json --framework FedRAMP-Moderate-r5

       8  violated         a check fired
       0  not_assessed     a check covers this but did not run — not a pass
       0  no_coverage      assessable in principle; Arbiter has no check for it
       5  satisfied        a check ran, applied, and found nothing
       5  not_automatable  no static analyzer can assess this; a person must
     305  not_enumerated   not in this pack; assess by other means
     323  controls in this baseline

    4.0% of the baseline carries evidence from this scan (13 of 323).
```

Four percent. No compliance product would print that number, which is why it is
the right one: the other 96% is unevidenced by this scan, and a reader of an
accreditation package needs to know which 96%.

## Residual notes

Every automatable control also carries a `residual` note saying what a person
must still check even when the automated part passes — because encryption being
switched on says nothing about who holds the key, and FedRAMP AU-11 fixes a
retention period that a check confirming "some period is set" cannot see.

## Mapping a rule to a control

Rules declare the controls they support:

```yaml
- id: unencrypted-database
  match_kinds: [database]
  assert: property_truthy
  any_of: [storage_encrypted, StorageEncrypted, encrypted, KmsKeyId]
  severity: high
  controls: [NIST-800-53r5:SC-28]
```

A **language-pack** rule's declaration (`packs/rules/ast/<lang>.yaml`,
`controls: [NIST-800-218:PW.5]`) is a check for that control, beside the
pack's own `satisfied_by`. The control then carries `declared_by` (the rules)
and `languages` (one cell per language pack, with its own state), so PW.5
reads violated by `go.http-default-client` in Go while Python ran clean, and
not assessed when the Java grammar is missing in a codebase with Java. A
declaration does not override a pack that marks the control
`not_automatable` (NIST 800-53 IA-2): the state stands and the reason says
which rules disagree. A declaration naming a control no pack enumerates
(`NIST-800-53r5:SI-10`, `OWASP-ASVS:*`) assesses nothing;
`controls.unresolved_declarations()` lists them. Declarations on the IaC rules
(`packs/rules/resource.yaml`) are not wired yet: those controls list their
checks in `satisfied_by`.

### Which probe a check needs

A control is only satisfied when the probe behind each of its checks ran.
`arbiter/<lang>.*` needs `lang_<lang>`, `arbiter/resource.*`
`resource_policy`, `<adapter>/*` that adapter, and `house/<id>` (your
`arbiter.yaml` rules) `house_rules_ast` for an `ast_query` rule and
`house_rules` for the other types. `arbiter controls` reading a saved
`report.json` does not have the configuration, so it requires both house
probes; a `house/` id the configuration does not define reads not assessed.

## CWE Top 25, per language

`cwe-top25-2024` has one control per weakness of the 2024 Top 25, and each
control one cell per language pack, transcribed from the matrix in
[`docs/cwe-top25.md`](cwe-top25.md):

| Cell `status` | Means | Cell state in a scan |
|---|---|---|
| `applies` | the weakness can occur; the language pack's rules tagged with its CWE (and the control's `satisfied_by`) assess it | `satisfied`, `violated` or `not_assessed`, or `no_coverage` when no native rule covers it (the gap) |
| `out_of_reach` | it can occur, but no single-file check can establish it | `out_of_reach` (a partial signal, Go's `stub-on-security-path` for CWE-863, can still make it `violated`; its silence is never a pass) |
| `not_applicable` | it cannot occur in this language (memory safety; 476 in Python, TypeScript and Ruby; 190 in Python, Ruby, TypeScript; 94 and 502 in Go) | `not_applicable` |

A language with no files in the codebase is `absent`. The control takes its
worst cell: violated, not assessed, gap or out of reach (`no_coverage`), then
satisfied, and `not_applicable` only when every language present is n/a for
it, a state distinct from `not_automatable` (nothing for a person to assess
either). Checks are not listed per cell: a rule tagged `cwe: [CWE-22]` in
`go.yaml` assesses the Go CWE-22 cell the day it lands, and
`tests/test_controls_cwe_top25.py` then fails until the doc matrix says N.
Only native checks count; a cell that semgrep or bandit covers (S, B) reads as
a gap, with the doc mark in the reason.

On a scan of `fixtures/planted/go` and `fixtures/planted/typescript`
(`--no-adapters`, 2026-10-23):

```console
$ arbiter controls arbiter-out/report.json --framework CWE-Top25-2024
    ...
    language     assessed violated  not_run   gap   OOR   n/a  absent
    go                 12       12        0     1     6     6       0
    typescript         12       12        0     2     5     6       0
```

## Commercial packs

PCI DSS v4.0, the HIPAA Security Rule, SOC 2 (Trust Services Criteria 2017)
and CIS Controls v8 ship as `pci-dss-v4`, `hipaa-security-rule`,
`soc2-tsc-2017` and `cis-controls-v8`, in the same format as the government
packs. Each declares the framework's full published size at its unit (PCI's
63 x.y requirement sections, HIPAA's 20 standards plus 39 implementation
specifications, the 61 TSC criteria, the 153 CIS safeguards) and enumerates
only the scanner-relevant subset, so an unlisted control reads as *not
enumerated* rather than as a pass. Physical safeguards are `not_automatable`
throughout. The identifiers were transcribed from the public framework texts;
PCI DSS and the TSC are licensed documents, so verify them against the licensed
text before an audit package cites them. CIS titles carry the implementation
group as an `(IG1)`/`(IG2)`/`(IG3)` suffix, which is Arbiter's annotation, not
part of the CIS title.
