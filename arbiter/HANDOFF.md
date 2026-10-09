# Arbiter handoff

This file no longer duplicates Arbiter's history or carries one-time landing
instructions. The previous handoff still told a session to import an already
landed git bundle, quoted obsolete test and corpus counts, and described
adjudication as the only open work.

Use the maintained context in this order:

1. `.ai/context-brief.md`
2. `.ai/project-context.md`
3. `.ai/project-configuration.md`
4. `.ai/project-map.md`
5. The active requirements via `python omni requirement list --status pending`
   (never the registry file itself)

`README.md` explains the product. The active requirements registry is the
authority for unfinished work; `CHANGELOG.md` and `.ai/project-context.md`
record completed decisions and their reasons.

## Current state

The hosted HTTPS API (ARB-018), remote CLI (ARB-019), and stdio plus HTTPS MCP
transports (ARB-010) passed their requirement-specific tests and the full Linux
completion gate, then closed in that dependency order. Their outcomes are in
`CHANGELOG.md`, `.ai/project-context.md`, and the requirement archive.

Two requirements are active. ARB-005 (licensing) requires counsel; do not
represent engineering work as resolving its licensing questions — its first
three acceptance criteria are met, and the fourth is a redistribution review
that has to be performed and signed by a person. ARB-024 (exercise the Windows
code paths in CI) is engineering work and is unblocked.

## Validation

The configured completion commands are:

```bash
PYTHONPATH=src python -m pytest -q
python omni doctor
python omni gate
```

`python omni graph why <path>` shows what a file is attached to before you
change it; `python omni failure search <text>` shows what has broken before.

Use `python tools/corpus.py --counts` for corpus totals instead of copying a
number into this file. Adjudication remains necessary before calibration can
promote rules, but it is an operational evidence track rather than the only
blocker to product work.

The product invariants, hosted custody decisions, MCP transport boundaries and
known gaps are maintained in `.ai/project-context.md`; they are deliberately
not repeated here.
