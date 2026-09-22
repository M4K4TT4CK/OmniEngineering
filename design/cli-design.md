# CLI Design

## CLI Summary

The `omni` command is a dependency-free maintenance helper for the OmniContext
workspace. It exists to keep the file-based system healthy, not to replace the
`.ai/` source of truth.

The repo-local executable is:

```bash
./omni
```

The installable console script is:

```bash
omni
```

Both delegate to `make_ai.py`.

## Commands

| Command | Purpose |
| --- | --- |
| `omni sync` | Refresh `.ai/entrypoints/`, assistant shims, and synced ignore files. |
| `omni doctor` | Check workspace health and report drift. |
| `omni validate` | Alias for `omni doctor`. |
| `omni context` | Print the exact low-token file set for a context profile. |
| `omni adopt` | Copy OmniEngineering into a target project without overwriting by default. |
| `omni requirement add` | Append a requirement record without hand-editing JSON. |
| `omni requirement show <ID>` | Print one requirement (active or archived). |
| `omni requirement list` | One line per requirement; filter with `--status`, `--last N`, `--all`. |
| `omni requirement search <text>` | Case-insensitive search across active and archived requirements. |
| `omni requirement update <ID>` | Change status/title/description or append a risk note. |
| `omni requirement complete <ID>` | Mark a requirement completed. |
| `omni requirement archive` | Move older completed requirements to `requirements-archive.json`. |
| `omni graph show --all` | List every graph node (grouped by file, with in/out degree); filter by `--kind`, `--language`, `--file`; `--edges` adds the edges. |
| `omni graph view [--mode 2d\|3d\|auto]` | Write an offline, interactive HTML viewer: a flat 2D view (Canvas 2D, no GPU needed, the default) and a 3D view (vendored 3d-force-graph, MIT), with views, layouts, chain highlight, path tracing, search, filters, tooltips and a light theme. |
| `omni graph show --all --layer <code\|governance\|history\|assurance\|workspace>` | Restrict the listing to one graph layer. |
| `omni graph timeline <node>` | Chronology of the commits, changelog entries and failures tied to a file, symbol, requirement or failure. |
| `omni graph sources [--write]` | Report what each layer would read from this project and what is missing; `--write` drafts `.ai/graph-config.json`. |
| `omni graph benchmark [--json]` | Measure a targeted graph query against the naive alternative (grep for the name and read every match whole; a commit compares against `git show`), for one real requirement, commit, failure and file the current project's own graph and registries already have. Everything is measured live; nothing is canned. The file case only picks an already-parsed source module, and only a git-tracked one, so a binary asset or a gitignored build artifact can never be "the file." |
| `omni test detect\|add\|remove\|list\|check` | Register and inspect the project's test suites; detection reads test-framework signals in files and CI commands. |
| `omni graph why <node>` | Cross-layer traversal: the requirements, changelog entries, commits, tests, failures and rules that touch a file, symbol, `REQ-###` or `FAIL-###`. |
| `omni graph lineage <node> [--up\|--down] [--depth N] [--code]` | Trace everything upstream (what led to a node) and downstream (what came from it) with no second endpoint. Every edge type has a flow direction (intent, delivery, code, assurance); a walk follows only one side, nearest first, bounded by depth and a node cap. A node's contents are listed only when the walk starts inside that code; sequence links (`follows`) stop after one step; `--code` adds calls and imports. |
| `omni graph build --layers ... --max-commits N` | Choose which layers to build and how much git history feeds the governance layer. |
| `omni failure add\|update\|show\|list\|search\|check` | Maintain the failure ledger: symptom, root cause, regression tests, prevention. `check` verifies completeness and that every reference resolves. |
| `omni gate` | Execute the rulepack `co_changed` validations against the git change set. |
| `omni waive <rule-id>` | Record an explicit, auditable waiver in `.ai/gate-waivers.jsonl`. |
| `omni hook install` | Install the Claude Code Stop hook that blocks completion while the gate fails. Claude Code only. |
| `omni hook install-git [--force] [--with-graph-rebuild]` | Write `.githooks/pre-commit` and set `core.hooksPath`, so `git commit` itself runs `omni gate`. No assistant required, and no dependency beyond `git` and a `python3`/`python` on `PATH`; Git for Windows runs hooks through its own bundled `sh`, so the same script also works there unmodified. `--with-graph-rebuild` also installs a `post-commit` hook that rebuilds the graph in the background (a lock directory skips a run that overlaps one already in flight; output goes to `.ai/.graph-build.log`, never to the terminal, and a full rebuild never blocks the commit). |
| `omni requirement draft [--commit REF] [--title] [--category] [--no-changelog] [--force]` | Draft a `proposed` requirement and a `CHANGELOG.md` stub from the files a commit (or the current change set) touched, so the manual step is editing a draft rather than writing one from nothing. Refuses to draft a duplicate when the commit message already cites a requirement ID. |
| `omni rule add` | Append a structured rule to a rulepack. |
| `omni mcp serve` | Serve the graph and registries as MCP (Model Context Protocol) tools over stdio (JSON-RPC 2.0, newline-delimited), so any MCP-speaking assistant reaches `graph_why`/`graph_lineage`/`graph_trace`/`graph_timeline`/`graph_show`/`graph_sources`/`requirement_show`/`requirement_list`/`requirement_search`/`failure_show`/`gate_status` directly, without a shell tool. Hand-rolled against the wire protocol (stdlib only, no SDK dependency); every tool is read-only. |
| `omni mcp tools [--json]` | List the tools (and, with `--json`, their input schemas) without starting the server. |

## Completion Gate

Rulepack `validation` blocks used to be documentation only. A rule whose
`validation.type` is `co_changed` is now executed by `omni gate`:

```json
"validation": {
  "type": "co_changed",
  "when_changed": ["backend/src/**"],
  "ignore": ["**/*.md"],
  "must_also_change": ["CHANGELOG.md"]
}
```

When any changed path matches `when_changed` (and not `ignore`), at least one
changed path must match `must_also_change`. The change set is the working tree
plus every commit since the merge-base with `origin/main` (or `main`), so
committing does not hide missing governance. A rule that genuinely does not
apply is waived with `omni waive <rule-id> --reason "..."`; only waivers
added in the current change set count, and each one is a tracked, reviewable
line in `.ai/gate-waivers.jsonl`.

Two more validation types are executed the same way (`gate_rules()` selects a
rule only when `validation.type` is in `EXECUTABLE_VALIDATION_TYPES`; adding a
new type there and to `gate_evaluate()`'s dispatch is a matched pair -- a type
handled in one but not the other is either silently never selected or crashes
on the first rule that declares it):

- **`requirement_registry_entry`** -- every `REQ-###`-shaped ID cited in a
  commit message since the base, or newly added to `CHANGELOG.md`, must exist
  in the registry named by `target`. `omni doctor`'s registry-schema check
  validates the registry's own shape; this instead validates what *other*
  files claim about it, catching a typo'd or invented ID.
- **`content_forbidden`** -- changed files are scanned for `patterns`, each
  `{"pattern": "<regex>", "message": "..."}`. One match is enough to flag a
  file (line number included); a rulepack's own files are always excluded
  (their JSON literally contains the pattern source text, which would
  otherwise flag itself). This is a bounded safety net for a handful of
  common accidental leaks, not exhaustive secret scanning -- most of a
  rulepack's rules stay unvalidated on purpose, because most engineering
  judgment (naming, cognitive load, composition vs. inheritance, ...) has no
  honest mechanical proxy, and checking a shallow one would be worse than
  leaving the rule as a judgment call.

`omni gate --hook` is the Claude Code Stop-hook entry point (`omni hook
install` wires it into `.claude/settings.json`). It exits 2 to block the
assistant from finishing, at most once per distinct failing state, and never
when `stop_hook_active` is set, so it cannot loop.

The Stop hook only ever fires inside Claude Code, so it enforces nothing for another assistant, another
tool, or a person committing by hand. `omni hook install-git` closes that gap the same way any git hook
does: it writes `.githooks/pre-commit` (a plain POSIX shell script, LF-only) and sets
`core.hooksPath=.githooks` for the local clone, so `git commit` runs `./omni gate` before the commit is
made, regardless of what wrote the change. CI runs `omni gate` again on every push and pull request as
the layer nothing local can skip; the hook is there to catch it before a commit even happens.

Project configuration (`configuration` in the universal ruleset) also accepts
`classification_banner` (rendered into every assistant shim and enforced by
`omni doctor`) and `allowed_root_paths` (root names doctor should not flag).
Git-ignored root paths are never flagged.

## Sync Behavior

`sync` writes the generated `.ai/entrypoints/` source files first, verifies that
required `.ai/` source files exist, then rewrites assistant shim files so they
point to the matching `.ai/entrypoints/` source and centralized fallback
contract.

`sync` is collision-safe by default. It updates files generated by
OmniEngineering and creates missing files, but it skips existing non-Omni files
so a target project's assistant configuration is not overwritten accidentally.
Use `omni sync --force` only when replacement is intentional.

The generated entrypoints currently cover universal LLM loading, Codex, Claude
Code, Cursor, GitHub Copilot, and Kiro-style steering. The generated root files
are intentionally small compatibility shims for tools that require fixed
filenames.

It does not rewrite rulepacks, requirements, schemas, adapters, playbooks,
checklists, the manifest, or project configuration. Adapter prompts, playbooks,
and checklists are stable source files because teams may customize them for
their preferred engineering process, local model, or model-router gateway.

## Context Behavior

`context` reads `.ai/context-manifest.json` and prints the files for a named
profile. It is meant to reduce token cost by turning broad instructions such as
"load OmniEngineering" into a short, explicit file list.

Supported profiles are `minimum`, `implementation`, `review`, and
`deep_policy`.

## Adopt Behavior

`adopt` copies `.ai/` and selected root shims into another project. It is
dry-run friendly and skips existing target files unless `--force` is supplied.
The command exists so adoption can be scripted without accidentally replacing a
project's own assistant config or package metadata.

## Doctor Behavior

`doctor` checks:

- Required `.ai/` files.
- JSON parse validity.
- Universal ruleset keys.
- Rulepack structure.
- Requirement registry structure.
- Failure ledger completeness (a fixed failure needs a root cause, fix,
  regression test or reason, and prevention).
- Assistant pointer drift.
- Assistant entrypoint source drift.
- Synced ignore file drift.
- Workspace file placement.
- README architecture references.
- License, notice, and trademark policy presence.
- Generated project map presence.
- CLI entrypoint configuration.

The command prints pass, warning, and failure lines, then exits with a non-zero
status when failures exist.

## Authoring Behavior

`requirement add` and `rule add` are low-friction append commands. They are
intended for routine additions, not bulk migrations.

The CLI should remain predictable:

- No network calls.
- No runtime dependencies outside the Python standard library.
- No destructive operations.
- No automatic edits outside the known workspace files.
