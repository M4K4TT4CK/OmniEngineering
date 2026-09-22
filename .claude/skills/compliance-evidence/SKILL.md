---
name: compliance-evidence
description: Assemble framework control-coverage evidence through Arbiter's scan, gate, and controls output, and judge the gaps it honestly leaves unassessed.
---

# Compliance evidence

Use when a task asks for compliance, control-coverage, or audit-readiness evidence against a named framework.

1. Confirm Arbiter's own repository is mounted in this session and its MCP server (`arbiter mcp`) is reachable; if not, say so and stop rather than approximating a scan yourself.
2. Run `arbiter_gate` (or `arbiter_scan` for a non-gating look) to get the current report, then `arbiter controls <report> --framework <name>` for each framework in scope.
3. Read the five-state result as Arbiter defines it, not as a pass/fail count:
   - `satisfied` -- a covering check ran and found nothing. Automated evidence.
   - `violated` -- a covering check fired. Automated evidence of a gap.
   - `not_assessed` -- a check exists but did not run here. Not a pass.
   - `no_coverage` -- assessable in principle; Arbiter has no check for it yet.
   - `not_automatable` -- no static analyzer can ever assess this control (personnel screening, physical access, incident-response exercises). A person must.
4. For every `not_automatable` or `no_coverage` control, read the repository's own non-code evidence -- security policy, incident-response plan, runbooks, CI/CD definitions, onboarding or setup docs -- before deciding whether it is addressed. Cite the exact document and passage. If nothing in the repository speaks to it, report it as unresolved; do not credit a control because the alternative is an uncomfortable gap.
5. Read every `residual` note Arbiter attaches to a control. A residual note exists specifically because the automated check cannot see the whole picture -- e.g. an encryption check passing says nothing about who holds the key.
6. Never call anything that records or applies a verdict inside Arbiter. Route a disputed or ambiguous finding to `arbiter_review_queue` instead of deciding it yourself.
7. If a framework is out of scope for the packs Arbiter ships, say so plainly rather than assessing it narratively under the same coverage vocabulary. See [references/frameworks.md](references/frameworks.md) for what is and is not covered, including STIG.

Report the coverage breakdown per framework, the findings behind each violation, the document-backed reasoning for any control marked addressed from non-code evidence, everything left genuinely unresolved, and hand confirmed gaps needing a code change to the coordinator as a precise packet rather than fixing them directly.
