# Release Playbook

Use this before merge, publish, package handoff, or public release.

## Checklist

- Requirements are complete or explicitly deferred.
- README and design docs match current behavior.
- Changelog is updated when public release notes are desired.
- Ignored local session files are not staged.
- Validation passes or failures are documented.
- Testing quality checklist is satisfied for risk-bearing changes.
- Maintenance impact checklist is satisfied for changes to released behavior.
- SCM checklist is satisfied for release, dependency, build, generated, or
  configuration artifact changes.
- Quality attribute checklist is satisfied for quality-sensitive releases.
- Professional practice checklist is satisfied before final reporting.
- Economics decision checklist is satisfied for major scope, cost, or tradeoff
  decisions.
- Engineering foundations checklist is satisfied for high-impact releases.
- Known risks and follow-ups are stated.
- Commit sentence is ready.
- Pull request summary is ready.

## OmniContext Public Package Check

Run:

```bash
./omni doctor
```

Confirm that `.codex-local/` remains ignored if local session notes exist.
