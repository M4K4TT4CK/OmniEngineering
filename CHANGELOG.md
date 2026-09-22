# Changelog

## 2026-09-22 (8)

### Completed

- Agent tooling | Closed a real gap in REQ-023's compliance-auditor: nothing
  told it to fetch Arbiter's external analyzers (checkov, semgrep, bandit,
  ruff, gitleaks), so those five were silently never present in a session
  regardless of network policy -- Arbiter's own graceful degradation just
  reported them `not_assessed`, which read as an honest coverage fact
  rather than the missing-instruction bug it actually was. Fixed upstream
  in senior-engineering-agent's role source (`ec5ca17`) so the role checks
  PATH before its first scan and runs Arbiter's own `tools/install_tools.sh
  -y` if Arbiter's repository is mounted in-session, falling back to
  whatever coverage is available rather than stalling if it isn't. Pulled
  in here via `omni agents install --managed`.
- Agent tooling | Moved `managed-agents/environment.yaml` off
  `networking: unrestricted` to an explicit `limited` allowlist (pypi.org,
  files.pythonhosted.org, github.com, objects.githubusercontent.com,
  api.github.com), per direct user decision to keep full external-analyzer
  coverage while no longer leaving every role's Bash access open to
  anywhere -- none of the nine roles carry web_search/web_fetch, but Bash
  reaches the network directly regardless of that tool-level lockout, and
  that exposure gets real the moment a session mounts a private or
  regulated repository. Not yet verified against the live platform: the
  `limited`/`allowed_hosts` shape is only documented as one example line in
  senior-engineering-agent's own docs, with no local schema to validate
  against -- `ant apply --dry-run` would confirm it, but that is still
  blocked on the same missing GitHub fine-grained access this session
  cannot resolve on its own.

## 2026-09-22 (7)

### Completed

- CLI / template maintainability | Closed REQ-013's other deferred follow-up:
  `omni update --source` now accepts a remote git URL (`https://`, `git://`,
  `ssh://`, or SCP-style `user@host:path`) in addition to a local checkout,
  cloning it into a temporary directory for the update and cleaning up
  afterward. The clone is full, not shallow -- a `--depth 1` clone would only
  carry the tip commit, and `git_show_file` needs history back to the
  target's own recorded base_ref (an arbitrary earlier commit) to
  reconstruct the 3-way-merge base. A source string is only ever treated as
  remote when it has a URL scheme or an SCP-style `user@host:` prefix, not
  just any colon, since a bare Windows local path (`C:\...`) contains one
  too. Verified against a real clone of this repository's own GitHub remote
  (github.com/m4k4tt4ck/omniengineering) -- confirmed the update reconstructs
  its merge base from cloned history rather than just diffing tips, confirmed
  the temp clone directory is removed once the command exits, and confirmed
  a nonexistent remote fails with a clear message and exit code 1 rather than
  a traceback. No new requirement ID: this closes the second risk note
  already recorded against REQ-013 without changing its scope.

## 2026-09-22 (6)

### Completed

- CLI / template maintainability | Closed the structural gap REQ-017 left
  deferred: `omni update` was reading TEMPLATE_MANAGED_FILES from whichever
  make_ai.py was doing the asking -- the target's own, currently-running
  copy -- so a target could never discover a template file that `--source`
  added after the target's own copy was written, no matter how new
  `--source` actually was. Added a hidden `_manifest` command that prints a
  checkout's template-managed file lists as JSON, and had `run_update` shell
  out to `--source`'s own make_ai.py for it instead of trusting its own
  constants. Falls back to the pre-fix behavior with a clear warning when
  `--source` predates the command, so older sources still update, just
  without the fix's benefit until they too are updated past this point. No
  new requirement ID: this closes a risk note already recorded against
  REQ-017 without changing its scope or acceptance criteria. Verified with
  `python_compile`, `omni doctor`, and two scratch scenarios: a target whose
  own file list was missing a real template file discovered it anyway via
  the live source's manifest, and a target pointed at a source predating
  `_manifest` fell back cleanly with the warning instead of crashing.

## 2026-09-22 (5)

### Completed

- Agent tooling | The prior fix below was cosmetic: it added clarifying
  prose but left Arbiter's own compliance doc filename inside backticks,
  and Arbiter's doc-drift check is a plain regex over backtick-quoted,
  path-shaped strings -- it doesn't parse surrounding prose, so the same
  finding fired again on re-scan. Read the probe's source directly to
  confirm that before changing anything. Fixed it in the one form
  Arbiter's own check explicitly exempts: a real external link, not a
  backtick-quoted path. Verified by re-running the scan afterward rather
  than assuming it worked. No new requirement ID, same as below.

## 2026-09-22 (4)

### Completed

- Agent tooling | Corrected an ambiguous path in the compliance-evidence
  skill's frameworks.md reference file: it cited Arbiter's own compliance
  documentation as if it were a path in the consuming repository, when it
  lives in Arbiter's own source tree. Caught by validating compliance-auditor
  against a real target -- running Arbiter's own scan against this
  repository flagged the line as doc drift. No new requirement ID: this
  corrects a REQ-023 artifact without changing its scope or acceptance
  criteria.

## 2026-09-22 (3)

### Completed

- Agent tooling (REQ-023) | Added `compliance-auditor`, a ninth role
  wrapping Arbiter's control-coverage evidence, per direct user decision
  that compliance-package production (FedRAMP Moderate, NIST 800-53, and
  STIG) is a recurring need. Checked Arbiter's actual integration surface
  before building anything: it ships an MCP server that deliberately
  withholds any verdict-recording tool, so the new role is built to respect
  that -- it assembles evidence and routes disputes to Arbiter's own review
  queue, but never adjudicates a finding and never fixes what it finds.
  Confirmed against Arbiter's own pack data, not assumed, that it has no
  STIG pack -- its FedRAMP pack carries a residual note saying so directly
  -- so the new `compliance-evidence` skill keeps any STIG output in a
  separately labeled, unverified section using none of Arbiter's graded
  vocabulary. Verified with a dry run against the live platform before
  commit: the plan creates exactly one new skill and one new agent, and
  updates only the coordinator's roster and prompt.

## 2026-09-22 (2)

### Completed

- Agent tooling (REQ-022) | Moved `release-validator` off the lead tier to
  Sonnet at high effort, per direct user decision -- its work is largely
  evidence-mapping against a rubric, closer to the standard-tier reviewers
  than to the coordinator's synthesis work. Effort stays high; `max_turns`
  drops from 80 to 60 as a side effect of the tier change, flagged to the
  user beforehand. Verified with a dry run against the live platform, where
  it now sits alongside the still-unapplied REQ-021 changes in one combined
  plan awaiting `ant apply`.

## 2026-09-22

### Completed

- Agent tooling (REQ-021) | Reviewed each live agent's actual frontmatter,
  skills, and system prompt against what it's supposed to do, and found two
  real gaps. Infrastructure review had no skill of its own -- every other
  reviewer role carries a skill with a repeatable checklist, but
  infrastructure leaned on generic repository-mapping guidance alone. Added
  an `infrastructure-audit` skill (provider-neutral checklist plus a
  reference covering AWS, GCP, Azure, Kubernetes, and CI/CD) and wired it in.
  The coordinator's only defense against two write-capable specialists
  clobbering each other in the shared sandbox was one advisory clause with
  no procedure behind it, even though the platform has no file locking
  between roster members. Rewrote it into concrete steps: name exact
  file/directory ownership per delegation, serialize rather than
  parallelize tasks that could touch the same file, and diff the working
  tree against what was authorized before integrating. Verified with a dry
  run against the live platform before commit -- the plan touches only the
  new skill and the two intended agents.

## 2026-09-21

### Completed

- Agent tooling | Ran `ant apply --yes` against the REQ-020 agent
  definitions; all eight agents on the platform moved from version 2 to
  version 3, and `claude-lock.json` was committed to reflect it.

### Completed

- Agent tooling (REQ-020) | Ran `omni agents install --source
  ../senior-engineering-agent --managed` for the first time in this repo,
  installing `.claude/agents`, `.claude/skills`, and `managed-agents/agents`.
  That surfaced two real defects, both fixed as part of this change: the
  package's managed-agents renderer dropped the `skills:` frontmatter block
  entirely, so `ant apply` would have silently stripped skill access from
  every agent (fixed upstream in `senior-engineering-agent`); and `omni
  doctor`'s workspace-placement check never learned the `.claude/`,
  `managed-agents/`, and `claude-lock.json` paths that `omni agents install
  --managed` itself creates, so running the shipped command as documented
  tripped four warnings against its own output (fixed here by adding those
  paths to `ALLOWED_ROOT_DIRS`/`ALLOWED_ROOT_FILES`). Brought
  `claude-lock.json` and `managed-agents/environment.yaml` into the repo, and
  added `.github/workflows/sync-managed-agents.yml`, which runs `ant apply`
  against the checked-in agent definitions whenever `managed-agents/` changes
  on `main` and commits the resulting lockfile back, so the platform's agent
  objects never drift from what is committed here.

## 2026-09-18

### Completed

- Code understanding | Added `omni agents`, a driver for the
  senior-engineering-agent package. `omni agents install` renders that package's
  canonical role definitions into Claude Code subagents (and optionally Managed
  Agents definitions for `ant apply`) and copies them plus the shared skills into
  a project; `omni agents doctor` asks the package whether its rendered output is
  current. The roles stay versioned with the package rather than in `.ai/`, since
  they are identical across adopting projects while `.ai/` carries
  project-specific governance. Install is collision-safe like `sync`: a `.md` in
  `.claude/agents/` without the generated banner is treated as project-owned and
  refused unless `--force`. A new repo-local `validate_agent_team` check joins
  `omni doctor`, flagging hand-edited agent definitions and skill references with
  no `SKILL.md`; it is skipped when `.ai/agent-team.json` is absent, so projects
  without an agent team see no change. The package's renderer is standard-library
  only, so this adds no dependency.

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
