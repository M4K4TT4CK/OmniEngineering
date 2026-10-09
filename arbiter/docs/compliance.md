# Control Coverage

Five government framework packs ship with Arbiter:

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

Each is an **independent** pack — controls map straight to checks, never routed
through a hub framework, because chaining two approximate crosswalks produces a
compliance claim two translations removed from anything that ran.

Packs are data files in `src/arbiter/packs/controls/`, not code.

## The five states

Every control resolves to one of five states, and the split between the last
three is the entire point:

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
