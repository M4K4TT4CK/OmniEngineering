# Changelog

## 2026-09-20

### Completed

- `REQ-022` | Code understanding | The code graph now has five layers, and the
  history of the project (requirements, changelog, commits, tests, failures, and
  the rules that came out of them) is one traversable structure that works for any
  project.
  - **Layers.** `code`, `governance` (requirements, changelog entries),
    `history` (every commit, chained in order, tied to the changelog entry whose
    heading it added, the requirement named in its subject, and the files it
    changed; a squash commit with no id is tied through its changelog entry),
    `assurance` (the project's tests, test suites, the failure ledger) and
    `workspace` (rulepacks, rules, playbooks, checklists, and OmniEngineering's own
    code). Requirements `touch` the files they declare and the files their commits
    changed, ranked git-proven first and directory-wide last.
  - **Test suites, focused on the host project.** `.ai/test-suites.json`
    registers suites (paths as files, directories or globs; framework; run
    command; what they cover) with `omni test detect|add|remove|list|check`.
    Detection reads test-framework signals in file contents (never comments, never
    `src/main`) and the commands CI files run, so JUnit, Vitest, Jest, pytest,
    Playwright, and validation scripts are found; a registered suite wins over a
    detected one. Suites become `suite` nodes that `contain` their test files and
    `cover` code. OmniEngineering's own files (`tooling_paths`) move to the
    workspace layer and are never counted as the project's tests.
  - **Failure ledger** (`.ai/failures/failure-ledger.json`,
    `omni failure add|update|show|list|search|check`): symptom, root cause,
    regression test or suite, prevention. `omni doctor` fails an incomplete fixed
    entry; `omni requirement complete` refuses defect requirements with no entry.
  - **Traversal.** `omni graph why <file|symbol|REQ|FAIL|commit|suite>` joins the
    layers and flags code no test reaches; `omni graph timeline <node>` lists
    everything dated that is tied to it, oldest first; `show --all --layer`.
  - **Works on any project.** Optional `.ai/graph-config.json` (requirement and
    changelog locations, test globs, ledger and registry paths, extra CI files,
    history depth, tooling paths); requirement ids are matched exactly as they
    appear in the project's own registry, so `PROJ-12`, `FEAT_7` and `REQ-001`
    all work; dated, versioned (`## [1.2.0] - 2026-01-31`) and `# 2026-01-31`
    changelogs parse; no `.ai/`, no git, no commits, a shallow clone, a subfolder
    of a larger repository, unicode paths and symlink loops each degrade to a
    clear note; malformed JSON is reported by file instead of ignored.
    `omni graph sources` shows what each layer would read and what is missing;
    `--write` drafts the config.
  - **Viewer.** A larger banner; All/None on the Layers, Node kinds, Languages and
    Edge types groups; a Node kinds filter; five layer planes when stacked; and a
    new search: any name, `REQ-###`, date, file or text, limited to a kind, sorted
    by match, date, name or connections, with a scrollable result list, a browse
    mode that lists every node of a kind, and Add all to view / Only these.
  - **Rules and playbooks.** `completion.failure_ledger`, `completion.regression_test`,
    `completion.failure_becomes_rule`, `completion.register_test_suites`,
    `completion.verify_target_environment`, `completion.rebuild_graph_layers` and
    `controlled.consult_failure_history`, with matching steps in the debugging,
    testing, implementation, review, planning, handoff and release playbooks, both
    checklists and every assistant entrypoint. `omni adopt` ships an empty ledger,
    an empty suite registry and no graph config.
  - **Tests.** `tests/` holds the first automated suite (72 unittest cases: the
    layers, ledger, `why`/`timeline`, suites and detection, the CLI gates, and a
    robustness matrix of awkward projects). The ledger records eight real failures
    from this work (`FAIL-001` to `FAIL-008`), six with regression tests.
  - Verified on STEP_App (6,804 nodes, 27,378 edges: 166 requirements, 168
    changelog entries, 298 commits over three months, 6 detected test suites, 57
    rules) and on this repository, with `omni graph why`/`timeline` on real files,
    mutation-checking that tests fail when behaviour breaks, and the viewer driven
    in headless Chromium (search by kind, browse, sort, All/None, layer stacking).
    Not verified in a real Windows Chrome or with hardware WebGL. Test detection is
    signal- and convention-based, so an unusual test layout needs a registered
    suite or a `test_globs` entry; `verifies` edges are inferred from calls and
    imports at test-file granularity.

## 2026-09-18 (6)

### Completed

- `REQ-021` | Code understanding | Follow-up: the code graph is no longer limited
  to Python and JS/TS. A generic, grammar-driven tree-sitter extractor covers
  Java, Kotlin, Go, Rust, C#, C/C++, Ruby, PHP, Swift, Scala and others
  (classes, interfaces, methods, imports, inheritance/implements, typed calls,
  cross-file resolution; Java also records Spring annotations and endpoints).
  `omni graph build` now defaults to `--languages auto` and prints a
  per-language summary; a language with no installed grammar still appears as
  file-level nodes. Flyway SQL migrations are replayed in version order into a
  table/column/foreign-key schema (`table` nodes, `references` edges, JPA
  entities linked with `maps_to`), with new `omni graph schema [--table T]
  [--format text|mermaid|json]` and a Schema button in the viewer. Also fixed a
  literal `\u2264` in the viewer's name-labels checkbox. Verified against the
  STEP_App repository (5,881 nodes / 19,421 edges; Java, TypeScript, SQL, C/C++,
  Kotlin, bash) with a real build, `show --all --language sql`,
  `graph schema --table app_user` and the viewer in headless Chromium (no
  console errors). Not verified in a real Windows Chrome. Unusual DDL (functions,
  vendor extensions) is skipped by the SQL parser.

## 2026-09-18 (5)

### Completed

- `REQ-021` | Code understanding | The 3D graph viewer is now branded and readable
  at distance. It is titled "OmniEngineering CodeGraph" with the brand mark, and
  the UI chrome uses the brand palette (`#e0475c` on `#0f0f12`, `#e8e8e8` text,
  Share Tech Mono stack from `assets/brand`) instead of the gold accents it had
  drifted into. Perspective used to shrink far-away nodes to specks and edges to
  hairlines, so nodes farther from the core cluster now grow (by distance from the
  median centre) and both nodes and edge cylinders are compensated by camera
  distance; edges use brighter colours (white calls, teal imports, red inherits,
  grey defines) and a new "Edge strength" slider; while a node is selected,
  unrelated nodes shrink and its own edges turn bold white. Verified against the
  real 2,392-node graph in headless Chromium at 1600x1000 with no console errors;
  not verified in a real Windows Chrome or with hardware WebGL.

## 2026-09-18 (4)

### Completed

- `REQ-021` | Code understanding | Follow-up: `omni graph view` opens correctly
  from WSL. It printed the Linux `file:///mnt/c/...` URL as the main link, which
  a Windows browser cannot resolve, and `--open` used a Linux-side launcher. It
  now detects WSL (`wslpath -w`), prints the `file:///C:/...` URL (UNC and
  space-containing paths are handled) plus the plain Windows path, and `--open`
  launches the default Windows browser via `cmd.exe /c start`. The page itself was
  checked for JavaScript errors and layout at 1600x1000, 1280x720 and 700x900 in
  headless Chromium (none). Not verified in a real Windows Chrome or with
  hardware WebGL.

## 2026-09-18 (3)

### Completed

- `REQ-021` | Code understanding | Follow-up: `omni graph build` no longer
  dead-ends on PEP 668 systems. When tree-sitter is missing it looks for
  `~/.venvs/omni-graph/bin/python` (or the interpreter in `OMNI_GRAPH_PYTHON`)
  and, if that has the packages, re-runs itself under it (guarded by an
  environment variable so it cannot loop); otherwise it prints the
  virtualenv setup commands instead of a `pip install` the OS refuses. A
  plain `./omni graph build` now just works once the venv exists. Verified
  from a system Python without tree-sitter, with no venv available, and with
  a deliberately failing `OMNI_GRAPH_PYTHON`; two new checks added to the
  end-to-end script (60 total).

## 2026-09-18 (2)

### Completed

- `REQ-021` | Code understanding | The code graph can now be listed in full and
  explored in 3D. `omni graph show --all` prints every node grouped by file
  with in/out edge counts (filter with `--kind`, `--language`, `--file`;
  `--edges`, `--sort file|degree|name`, `--limit`, `--json`; external
  placeholders hidden unless `--include-external`). `omni graph view` writes
  `.ai/project-graph.html`, one self-contained offline page (nothing fetched
  at view time) with orbit/pan/zoom, search, language and edge-type filters,
  colouring by language/kind/top-level directory, a detail panel listing each
  symbol's callers and callees (click to jump), and double-click to expand a
  node's neighbours; it starts with the 500 best-connected symbols
  (`--max-initial`, `--all`, or `--focus <symbol> --depth N`). The 3D engine is
  the unmodified 3d-force-graph 1.80.0 bundle (MIT, Copyright (c) Vasco
  Asturiano) which renders with three.js (MIT) and contains 33 other
  permissively licensed packages; `.ai/graph-viewer/THIRD_PARTY_NOTICES.md`
  is generated from each package's own license file, the same text is
  embedded in every generated page, and `NOTICE` points at it. The vendored
  assets are copied by `adopt --include-cli`/`update`, excluded from
  assistant prompt context via `.ai/.ignore`, and never parsed by `graph
  build`. README also documents the virtualenv route for PEP 668 systems,
  where `pip install` of tree-sitter is refused. Verified by a 56-check
  end-to-end script and a headless-Chromium (software WebGL) run of search,
  select, expand and isolate; a real bug found that way (the library clears
  its host element, wiping the status bar) is fixed. Not verified on touch
  devices or with a hardware GPU.

## 2026-09-18

### Completed

- `REQ-020` | Enforcement | Completion rules are now executed instead of
  merely documented. Rulepack `validation` blocks were never run by any
  code, and `omni doctor` only checked that requirement keys existed, never
  their types -- so an adopter's assistant skipped changelog/registry updates
  at will and hand-wrote 25 schema-violating registry fields that `doctor`
  passed. New: `omni gate` executes `co_changed` rules (changed files must
  be accompanied by a `CHANGELOG.md` and requirements-registry change) over
  the working tree plus every commit since the merge-base with `origin/main`,
  so committing cannot hide a missing update; `omni waive <rule> --reason`
  records an auditable line in `.ai/gate-waivers.jsonl` (only waivers added
  in the current change set count); `omni hook install` wires a Claude Code
  Stop hook (`omni gate --hook`) that exits 2 to block finishing, at most
  once per distinct failing state and never when `stop_hook_active` is set.
  `omni requirement show|list|search|update|complete|archive` replace reading
  and hand-editing the registry JSON (an adopter's had grown to 531 KB and
  the `minimum` context profile told every session to load it); archived
  entries stay queryable and ids are never reused. `doctor` now type- and
  enum-checks the registry and its archive. `classification_banner` and
  `allowed_root_paths` are new `configuration` keys: the banner is rendered
  into every assistant shim and its absence is a `doctor` error (plain
  `sync` used to silently strip a hand-added CUI marking), and doctor no
  longer flags git-ignored root paths or IDE/assistant state directories.
  The Claude entrypoint no longer embeds a duplicate copy of the fallback
  contract. Verified by a 42-check end-to-end script in a throwaway git repo;
  the Stop hook itself was exercised by piping hook JSON, not inside a live
  Claude Code session.

## 2026-09-03 (2)

### Completed

- `REQ-019` | Code understanding | Added `omni graph render`, making the
  node-link graph visualization a permanent CLI feature instead of a
  one-off script. Renders `.ai/project-graph.json` as a force-directed SVG
  using a small pure-Python Fruchterman-Reingold layout -- no numpy/
  networkx dependency, so `render` works with just the standard library,
  same as `trace`/`show` (only `build` needs the `[graph]` extra). Nodes
  colored by language, sized by kind; edges distinguish `calls`/`imports`/
  `inherits` from de-emphasized `defines`. `--max-nodes` caps large graphs
  to the highest-degree nodes to keep the O(n^2) layout fast (~0.2s for 112
  nodes, ~1.3s for 300, measured against this repo); `--focus`/`--depth`
  renders one symbol's neighborhood instead of the whole codebase.
  `design/diagrams/omni-code-graph.svg` was regenerated using this actual
  command, replacing the prior one-off script's output.

## 2026-09-03

### Completed

- `REQ-018` | Presentation / documentation | Replaced
  `design/diagrams/omni-code-map.svg` (a hierarchical org-chart-style
  diagram) with `design/diagrams/omni-code-graph.svg` -- user feedback was
  that the first version didn't read as "a graph." The new version is a
  real force-directed node-link layout (networkx `spring_layout`) over all
  106 real symbols and 277 resolved edges from the same
  `.ai/project-graph.json` `omni graph build` output: circles sized by kind
  and colored by source module, lines distinguishing `calls` from
  `defines`. Same underlying data, correctly graph-shaped this time.

## 2026-09-02 (2)

### Completed

- `REQ-017` | CLI / template maintainability | Fixed two bugs found by
  actually adopting/updating a simulated pre-existing application with the
  new `omni graph` feature in place, instead of trusting the change in
  isolation: (1) `omni adopt --include-cli` copied `make_ai.py` (which
  unconditionally imports `omni_graph`) without copying `omni_graph.py`
  itself, crashing every `omni` command in the adopted project, not just
  `graph` -- fixed by adding `omni_graph.py` to `ADOPTION_CLI_FILES`. (2) A
  project adopted before `omni_graph.py` existed hit the same crash on its
  first `omni update` against a current source, because `omni update`'s
  file list is computed by the *currently running* (old) `make_ai.py`, not
  the newer `--source` -- a structural gap that means a target can never
  discover a brand-new template file on the very first update after it's
  introduced. Made `make_ai.py`'s `import omni_graph` defensive instead
  (falls back to `None`, with a shared guard on every `omni graph`
  subcommand and a doctor warning), so a target still missing
  `omni_graph.py` for any reason degrades to "omni graph is unavailable"
  rather than breaking every other command. Verified end to end: fresh
  adopt, a simulated pre-existing-adoption upgrade (crash gone, clear
  warning, self-heals on the next update), and that adopter customizations
  survive both update passes untouched.

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
