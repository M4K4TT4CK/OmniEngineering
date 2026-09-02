# Changelog

## 2026-09-02

### Completed

- `REQ-016` | Presentation / brand identity | Restyled OmniEngineering's
  brand assets to match the visual family already shared by the two sibling
  projects on this account (context-relay-mcp's CtxRelay, PathBox): dark
  `#0f0f12` ground, single `#e0475c` accent, monospace wordmark, and a
  48x48 panel-plus-corner-nodes-plus-beacon mark under `assets/brand/`.
  Added `assets/brand/omni-{mark,wordmark,banner}.svg` -- same shared chrome
  as the siblings, with OmniEngineering's own mark content: a hub fanned out
  to five nodes, standing for one repo-local `.ai/` control plane synced out
  to many AI assistant entrypoints. Removed the old, unrelated
  `assets/banners/` and `assets/identity/` SVGs. Updated README's banner and
  presentation-assets list, `make_ai.py`'s `ADOPTION_PRESENTATION_FILES`
  (so `omni adopt --include-presentation` still works), and removed a stray
  `assets/brand/` line from `.gitignore` left over from before this became
  the real asset directory, which would have made the new files untrackable.

## 2026-09-01

### Completed

- `REQ-015` | Code understanding | Added `omni graph build/trace/show` and a
  new `omni_graph.py` module: a local, deterministic code graph parsed from
  source with tree-sitter -- explicitly not a vector index, no embeddings.
  Nodes are code entities (module/class/function/method) read straight from
  the syntax tree; edges are tagged `EXTRACTED` (a fact read directly from
  one source site -- an import, a call, a base class) or `INFERRED`
  (resolved by traversing the graph across files/scopes, or by an optional
  configured semantic API pass). `omni graph trace <a> <b>` answers "how are
  these connected" with an actual shortest path of tagged hops; `omni graph
  show <node>` lists a symbol's direct edges. Covers Python, JavaScript, and
  TypeScript. tree-sitter and its grammars ship as an optional `[graph]`
  extra -- `build` fails with an actionable install message if it's missing,
  while `trace`/`show` only read the JSON `build` wrote and need nothing
  beyond the standard library. The semantic pass is opt-in
  (`--semantic` + `OMNI_GRAPH_SEMANTIC_API_URL`) and validates every
  API-suggested relation against known graph symbol names before adding it,
  so a hallucinated relation can't be written in silently. `omni doctor`
  gained a non-blocking check that validates the graph file's shape only
  when one is present. The CLI (subcommand-per-verb, `--json` everywhere) is
  designed to be wrapped 1:1 by an MCP relay the same way `omni_map` /
  `omni_doctor` / `omni_sync` already are.

## 2026-08-27 (4)

### Completed

- `REQ-014` | CLI / template maintainability | Fixed a bug in `omni update`
  (REQ-013) found live while dogfooding it against STEP_App's genuinely
  diverged `make_ai.py`: a conflicted `make_ai.py` merge still triggered the
  automatic sync re-run, crashing with a raw `SyntaxError` traceback against
  the now-invalid file instead of a clean message. `omni update` now skips
  the sync re-run when `make_ai.py` itself has an unresolved conflict and
  tells the user to run `omni sync` themselves once it's fixed.

## 2026-08-27 (3)

### Completed

- `REQ-013` | CLI / template maintainability | Added `omni update` --
  3-way-merges every template-managed file (rulepacks, playbooks, checklists,
  SWEBOK knowledge, schemas, entrypoints, `make_ai.py`/`omni`) into an
  already-adopted, already-customized workspace via `git merge-file`, using
  the ref recorded in the new `.ai/omni-version.json` (written automatically
  by `omni adopt`) as the merge base. Adopter-untouched files that changed
  upstream fast-path update; adopter customizations the template didn't
  touch are left alone; real collisions get `<<<<<<<` conflict markers to
  resolve by hand, same as a git merge. `.ai/project-configuration.md`,
  `.ai/project-map.md`, `.ai/requirements/requirements.json`, and
  `CHANGELOG.md` are never touched -- those stay adopter-owned. If
  `make_ai.py` itself changed, `update` re-runs `sync` in a fresh process so
  entrypoint/pointer/ignore files regenerate from the new templates. Added
  `--bootstrap` for workspaces adopted before this existed (records
  `--source`'s current ref as a starting point without merging anything),
  a matching `omni doctor` check that warns when `.ai/omni-version.json` is
  missing, and a README Quick Start section covering the whole
  adopt/doctor/update loop -- this was the single biggest "clunky" gap
  raised this session: pulling template improvements into an adopted
  project previously meant a manual hand-diff-and-port, done twice by hand
  earlier in this same session before this command existed.

## 2026-08-27 (2)

### Completed

- `REQ-012` | CI / repo hygiene | Added `.github/workflows/ci.yml` running
  `python3 omni doctor` (full-history checkout) on every push/PR to `main`,
  and `.gitattributes` (`* text=auto eol=lf`) to stop future line-ending
  drift -- this repo's working tree had silently drifted to CRLF against
  an LF-committed history, masking real diffs behind whole-file noise.
  Existing committed content is untouched; a follow-up renormalization
  commit is intentionally out of scope here since it would touch every
  file in the repo.

## 2026-08-27

### Completed

- `REQ-011` | Doctor / drift detection | Added `validate_project_map_freshness`
  (warns when a top-level directory on disk is missing from
  `.ai/project-map.md`'s tree) and `validate_recent_commits_tracked` (warns
  when commits postdate the last `CHANGELOG.md`-touching commit, by commit
  timestamp rather than `git log X..HEAD` DAG reachability, which
  squash-merge PR histories make misleading) to `omni doctor`. Developed
  while auditing an adopter project (STEP) for OmniEngineering alignment,
  where both gaps were found live -- a stale map missing a whole added
  directory, and several commits merged with no requirement ID or
  changelog entry -- then ported back here so every adopter's `omni doctor`
  catches the same drift automatically.

## 2026-06-12

### Completed

- `REQ-010` | Compatibility | Removed the Python 3.11-only `tomllib`
  dependency from the maintenance CLI, restored Python 3.10 compatibility, and
  kept pyproject console-script validation with a lightweight text check.
- `REQ-009` | CLI ergonomics | Added a repo-local `omni` command shim and
  installable `pyproject.toml` console script so users can run `omni doctor`,
  `omni sync`, and other maintenance commands without typing
  `python3 make_ai.py`.
- `REQ-008` | Authoring interface | Added low-friction CLI commands for
  appending requirements and structured rulepack rules without hand-editing
  large JSON files, documented the workflow, and used the new commands to add
  the requirement and a rulepack authoring recommendation.
- `REQ-007` | Structured rulepacks | Converted enforceable companion rules
  from Markdown to schema-backed JSON rulepacks, added a rulepack schema, and
  updated doctor validation to check rulepack IDs, required keys, rule IDs,
  severities, and statements.
- `REQ-006` | Ruleset refinement | Replaced the Python-specific data rule
  file with bounded, framework-agnostic data governance rules covering data
  boundaries, contracts, validation, transformations, privacy, persistence,
  migrations, and testing.
- `REQ-005` | Documentation clarity | Repositioned OmniContext as a repo-local
  AI control plane for solving context drift across assistants, clarified that
  cloned repositories do not require `make_ai.py` for normal usage, and updated
  the CLI description to present it as maintenance tooling.
- `REQ-004` | Assistant fallback rules | Added shared fallback LLM rules and
  embedded a specific fallback operating contract into Claude, Cursor, and
  Copilot entrypoints so assistants still receive core constraints if they skip
  the primary `.ai/` configuration.

## 2026-06-11

### Completed

- `REQ-003` | Executable validation | Added a dependency-free OmniContext CLI
  with `sync`, `doctor`, and `validate` commands, introduced a requirements
  registry, added schema contracts for requirements and the global ruleset,
  documented the new workflow, and added repository hygiene ignores.
- `REQ-002` | Documentation | Added a polished OmniContext README,
  repository architecture explanation, setup guidance, daily workflow notes, and
  a supporting SVG architecture diagram.
- `REQ-001` | AI workspace ruleset | Added the universal engineering ruleset
  as the controlling global configuration, aligned controlled implementation and
  completion workflow rules to the pasted requirements, added project
  configuration placeholders, refreshed assistant entrypoints, and changed the
  bootstrap script to verify source-of-truth rule files instead of overwriting
  them.
