# Framework coverage

What Arbiter can and cannot check today. Verify against `docs/compliance.md` in the Arbiter repository itself (github.com/paukennick/arbiter) before relying on this list -- that file lives in Arbiter's own source, not this package, and packs are data files that can be added independently of this skill.

## Shipped, usable now

- `nist-800-53r5` -- NIST SP 800-53 Rev. 5
- `nist-800-171r2` -- NIST SP 800-171 Rev. 2
- `nist-800-218-ssdf` -- NIST SSDF (SP 800-218)
- `fedramp-moderate-r5` -- FedRAMP Moderate Rev. 5
- `cmmc-l2` -- CMMC Level 2

Run `arbiter controls <report> --framework <pack-name>` for each of these that is in scope. Each pack is independent -- controls map straight to checks, never routed through another framework as a hub -- so do not cross-reference a finding from one pack as if it satisfied a differently-worded control in another without checking that pack's own mapping.

## Not shipped

- **PCI-DSS, HIPAA, SOC 2, CIS** -- the interfaces exist in Arbiter's pack format, but no pack has been written. Treat these the same as STIG below: say plainly that Arbiter cannot check them, do not approximate.

## STIG -- explicitly out of scope, confirmed by Arbiter's own data

There is no STIG pack, and Arbiter's shipped FedRAMP Moderate pack says directly, in its own residual note on the configuration-management control family: "FedRAMP requires settings to follow a named benchmark such as a DISA STIG or CIS benchmark. Arbiter's rules are not that benchmark, and a pass here is not a benchmark pass." Do not let a `satisfied` result on a related NIST or FedRAMP control stand in for STIG compliance -- they are checking different things, and Arbiter's own maintainer has already written that distinction into the data rather than leaving it to inference.

If a task requires a STIG assessment specifically:

1. Say plainly that Arbiter has no automated STIG coverage and that any STIG finding from this role is not backed by a checked, reproducible scan.
2. Only if explicitly asked to proceed anyway, produce a separate, clearly labeled narrative section against the relevant SRG/CCI items you can identify from the repository's own configuration -- never using `satisfied`, `violated`, `not_assessed`, `no_coverage`, or `not_automatable`, since those words describe Arbiter's graded, checked output specifically. Label every line in this section "unverified -- not Arbiter-checked" so it is never read alongside the real coverage numbers as if it carried the same weight.
3. Recommend a dedicated STIG-aware scanner (e.g. OpenSCAP against the relevant SRG, or a DISA-provided checklist tool) as the actual source of truth, the same way this skill treats Arbiter as the source of truth for the frameworks it does ship.
