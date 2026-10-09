Not applicable to arbiter today: it is proprietary and ships its own
`NOTICE.md` rather than the OmniEngineering `LICENSE`/`NOTICE`/
`TRADEMARKS.md` set (see `.ai/project-configuration.md`). Keep this
checklist for the day that changes, or for an adopting project that is
already public.

- `LICENSE`, `NOTICE`, `TRADEMARKS.md`, `CONTRIBUTING.md` exist, are current,
  and name the actual license and actual trademark holder — not placeholder
  text carried over from wherever the workspace was adopted from.
- `LICENSES/` (third-party license texts, if any dependency requires
  redistributing one) is present and matches what's actually declared in
  `pyproject.toml` or the equivalent manifest.
- `assets/identity`, `assets/banners`, and `assets/omni-context.svg` (or
  their project-specific equivalents) are the ones meant for public view —
  no internal-only branding, no draft assets.
- No machine-specific or local-only files are staged: git-ignored local
  settings (this repo: `.claude/settings.local.json`), absolute paths from a
  developer's machine, anything under `.ai/.ignore`.
- Secrets scan run and clean — credentials, API keys, internal hostnames,
  internal ticket or system references that mean nothing outside the org.
- README accurately describes current public behavior: capabilities,
  installation, licensing. Internal roadmap or unreleased-feature language
  removed or clearly marked as such.
- `CHANGELOG.md` reflects what's actually shipping, each entry still citing
  its `REQ-###`.
- `python omni requirement list --status pending` shows no entries that are
  actually resolved; close them with `python omni requirement complete` and
  sweep terminal ones into `.ai/requirements/requirements-archive.json` with
  `python omni requirement archive` first.
- `python omni failure list --status open` is empty, or each open entry is
  named in the release notes.
- `python omni doctor` and `python omni gate` run clean (doctor's remaining
  WARNs are listed in `.ai/project-configuration.md`; anything else is real).
