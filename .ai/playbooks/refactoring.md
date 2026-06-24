# Refactoring Playbook

Use this when improving structure without changing behavior.

## Requirements

- State the behavior-preservation boundary.
- Use `.ai/knowledge/swebok/software-design.md` when changing structure.
- Use `.ai/knowledge/swebok/software-construction.md` when changing code.
- Use `.ai/knowledge/swebok/software-maintenance.md` when refactoring released
  or depended-on behavior.
- Identify tests or checks that prove behavior remains stable.
- Keep refactors small and reversible.
- Avoid public interface changes unless the requirement explicitly permits them.
- Do not mix refactoring with feature work unless necessary for safe delivery.

## Completion

Report what structure changed, what behavior should be unchanged, and what
validation supports that claim.
