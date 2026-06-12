# OmniContext: The Universal AI Workspace

![OmniContext architecture](assets/omni-context.svg)

OmniContext is a repo-local control plane for AI coding assistants.

Its unique job is to prevent AI context drift when a team uses more than one
assistant in the same repository. Claude Code, Cursor, GitHub Copilot, and
future tools can all read different entry files, but OmniContext routes them to
one shared `.ai/` source of truth.

The result is one set of project rules, architecture guidelines, security
boundaries, requirement IDs, validation expectations, changelog discipline, and
completion workflow across every assistant.

## The Problem

Modern software teams rarely use only one AI assistant.

Cursor may read `.cursorrules`, Claude Code may read `CLAUDE.md`, and GitHub
Copilot may read `.github/copilot-instructions.md`. If each file contains a
different version of the project instructions, assistant behavior drifts.

OmniContext solves that drift by making the root assistant files lightweight
routing hooks. They all point back to `.ai/`, where the real rules live.

The unique case this solves is not prompt storage. It is governance for
AI-assisted development across multiple tools:

- One assistant should not skip requirement IDs while another uses them.
- One assistant should not update code without the changelog rule another tool
  follows.
- One assistant should not ignore security boundaries because it reads a
  different config file.
- One assistant should not claim completion without the validation workflow the
  rest of the team expects.

OmniContext gives the repository a shared AI operating model without locking the
team into one vendor or editor.

## The Pattern

```text
[project-root]/
├── .ai/
│   ├── core-context.md
│   ├── project-configuration.md
│   ├── .ignore
│   ├── requirements/
│   │   └── requirements.json
│   ├── rules/
│   │   ├── universal-engineering-ruleset.json
│   │   ├── controlled-implementation.json
│   │   ├── completion-workflow.json
│   │   ├── data-governance.json
│   │   ├── fallback-llm-rules.json
│   │   ├── hci-ui-rules.json
│   │   └── oop-design.json
│   └── schemas/
│       ├── rulepack.schema.json
│       ├── requirements.schema.json
│       └── universal-engineering-ruleset.schema.json
│
├── CLAUDE.md
├── .cursorrules
├── .github/
│   └── copilot-instructions.md
├── CHANGELOG.md
├── omni
├── pyproject.toml
└── make_ai.py
```

The `.ai/` directory is the master context directory. The root files are
entrypoints for specific tools:

| Tool | Entry file | Role |
| --- | --- | --- |
| Claude Code | `CLAUDE.md` | Points Claude to `.ai/` |
| Cursor | `.cursorrules` | Points Cursor to `.ai/` |
| GitHub Copilot | `.github/copilot-instructions.md` | Points Copilot to `.ai/` |

## What This Workspace Enforces

This implementation goes beyond simple prompt sharing. It includes a strict
engineering workflow that can be reused across projects:

- Targeted file access before any implementation work.
- Requirement IDs for every task and code change.
- Small, isolated changes instead of broad rewrites.
- Modular, maintainable design standards.
- Security guardrails for secrets and sensitive files.
- Documentation and changelog updates as completion gates.
- Test, lint, typecheck, and build validation before claiming completion.
- Required final reporting with commit and pull request information.
- A requirement registry for tracking `REQ-###` work across assistants.
- A dependency-free doctor command for detecting workspace drift.
- Fallback rules embedded in every assistant entrypoint in case an LLM skips the
  primary `.ai/` configuration.

The controlling global ruleset is:

```text
.ai/rules/universal-engineering-ruleset.json
```

Enforceable companion rules live beside it as structured JSON rulepacks in
`.ai/rules/`. The JSON format gives each rule a stable ID, severity, scope, and
validation hints so tools can inspect more than file existence.

## If You Clone a Repo That Already Uses OmniContext

You do not need to run `make_ai.py` just to benefit from OmniContext.

If the repository already contains `.ai/`, `CLAUDE.md`, `.cursorrules`, and
`.github/copilot-instructions.md`, the workspace is already usable. Open the
repo in your assistant of choice and the entrypoint files should route that
assistant to the shared `.ai/` rules.

Use `make_ai.py` only when you want to maintain or verify the OmniContext
workspace itself:

- Run `./omni doctor` to check whether the workspace is healthy.
- Run `./omni sync` after editing fallback rules or assistant
  entrypoint content.
- Run `./omni validate` as an alias for `doctor`.

In other words: `.ai/` is the product. `make_ai.py` is the maintenance tool.

## Add OmniContext to a New Repo

Create the workspace structure at the root of a project:

```bash
mkdir -p .ai/rules .ai/requirements .ai/schemas .github assets
touch .ai/core-context.md
touch .ai/project-configuration.md
touch .ai/.ignore
touch .ai/requirements/requirements.json
touch CLAUDE.md
touch .cursorrules
touch .github/copilot-instructions.md
```

Then add routing text to each assistant entrypoint.

`CLAUDE.md`

```markdown
# Claude Configuration

Read and prioritize all rules, styles, and workflows located inside the `.ai/`
directory before writing code.
```

`.cursorrules`

```markdown
# Cursor Configuration

Read and prioritize all rules, styles, and workflows located inside the `.ai/`
directory before writing code.
```

`.github/copilot-instructions.md`

```markdown
# Copilot Configuration

Read and prioritize all rules, styles, and workflows located inside the `.ai/`
directory before writing code.
```

## Configure a Project

Before using OmniContext on a real project, fill in:

```text
.ai/project-configuration.md
```

At minimum, define:

- Project name.
- Repository type.
- Primary language or stack.
- Package manager.
- Build command.
- Test command.
- Lint command.
- Typecheck command.
- Changelog location.
- Documentation locations.
- Branching or pull request standard.
- Comment style.

Then customize:

```text
.ai/rules/universal-engineering-ruleset.json
```

Replace the angle-bracket placeholders with project-specific values and add
project requirements using the included `REQ-###` template.

## Daily Use

Once OmniContext is initialized, keep the root assistant files boring. Most
changes should happen inside `.ai/`.

| Need | Edit |
| --- | --- |
| Global assistant behavior | `.ai/core-context.md` |
| Project commands and placeholders | `.ai/project-configuration.md` |
| Strict operating rules | `.ai/rules/universal-engineering-ruleset.json` |
| Requirement registry | `.ai/requirements/requirements.json` |
| Machine-readable contracts | `.ai/schemas/` |
| Implementation workflow | `.ai/rules/controlled-implementation.json` |
| Completion and reporting workflow | `.ai/rules/completion-workflow.json` |
| Data contracts and transformations | `.ai/rules/data-governance.json` |
| Fallback assistant behavior | `.ai/rules/fallback-llm-rules.json` |
| Security exclusions | `.ai/.ignore` |
| Change history | `CHANGELOG.md` |

Example prompt:

```text
Implement REQ-014. Use the minimum access scope from the requirement and follow
the completion workflow in .ai/rules/completion-workflow.json.
```

## Maintenance CLI

`omni` is a small dependency-free maintenance helper. It is not required for
normal day-to-day AI usage after the files are already present in a repo.
It requires Python 3.11 or newer.

Use it when you are changing the OmniContext configuration itself.

Run it directly from the repository:

```bash
./omni doctor
./omni sync
```

Or install the command once from the repository root:

```bash
python3 -m pip install -e .
```

After that, use:

```bash
omni doctor
omni sync
omni validate
```

`sync` verifies that the required `.ai/` source-of-truth files exist, then
refreshes the assistant entrypoint files so they point to the global ruleset.

It does not overwrite the `.ai/` rules. The rules are the source of truth.

Run the doctor before publishing, copying, or adapting the workspace:

```bash
omni doctor
```

The doctor checks:

- Required `.ai/` source-of-truth files.
- JSON parse validity.
- Universal ruleset structure.
- Structured rulepack IDs, required keys, rule IDs, severities, and statements.
- Requirement registry structure and duplicate IDs.
- Assistant pointer drift.
- Fallback rule availability.
- README asset references.
- Changelog presence.

`validate` is an alias for `doctor`:

```bash
omni validate
```

## Low-Friction Editing

Most people should not need to hand-edit large JSON files for routine updates.
Use the CLI for small, safe additions.

Add a requirement:

```bash
omni requirement add \
  --title "Add release checklist" \
  --description "Create a repeatable release checklist for AI-assisted changes." \
  --category "Workflow" \
  --priority medium \
  --scope "README.md,.ai/rules/completion-workflow.json" \
  --acceptance "Checklist exists,Doctor passes"
```

If `--id` is omitted, OmniContext assigns the next `REQ-###` ID.

Add a rule to a rulepack:

```bash
omni rule add \
  --rulepack completion \
  --name release_checklist \
  --statement "Use the release checklist before publishing a completed change." \
  --severity recommended \
  --scope "release,completion"
```

Rulepack aliases:

| Alias | Rulepack |
| --- | --- |
| `controlled` | `.ai/rules/controlled-implementation.json` |
| `completion` | `.ai/rules/completion-workflow.json` |
| `data` | `.ai/rules/data-governance.json` |
| `fallback` | `.ai/rules/fallback-llm-rules.json` |
| `hci` or `ui` | `.ai/rules/hci-ui-rules.json` |
| `oop` | `.ai/rules/oop-design.json` |

After edits, run:

```bash
omni doctor
```

## Fallback Rules for LLMs

The root assistant files include more than a pointer to `.ai/`. They also embed
a compact fallback operating contract. This protects the workspace when an LLM
does not fully follow the original configuration instruction.

The fallback contract tells every assistant to:

- Confirm or assign a `REQ-###` ID before work begins.
- State the minimum access scope before inspecting files.
- Avoid unrelated repository scans and unrelated file changes.
- Respect `.ai/.ignore` and avoid secrets, credentials, logs, caches, and local
  environment files.
- Preserve existing behavior unless it conflicts with the active requirement.
- Avoid new dependencies, schema changes, destructive data changes, and public
  interface changes unless explicitly required.
- Update docs, changelog, and requirements when work changes behavior or status.
- Run `omni doctor` for workspace changes.
- Run `omni sync` when assistant entrypoint files change.
- Report validation, risks, commit sentence, and pull request information.

The shared source for this behavior is:

```text
.ai/rules/fallback-llm-rules.json
```

## Security Guardrails

Use `.ai/.ignore` to document files and paths the assistant must not read.
Common examples include:

```text
.env
.env.*
*.pem
*.key
node_modules/
vendor/
.venv/
dist/
build/
*.log
*.sqlite
*.db
```

This is a prompt-level guardrail, not a substitute for repository permissions,
secret scanning, or platform security controls.

## Symlink Alternative

For Unix-only teams, symlinks can route assistant files directly to shared
context:

```bash
ln -s .ai/core-context.md .cursorrules
ln -s .ai/core-context.md CLAUDE.md
mkdir -p .github
ln -s ../.ai/core-context.md .github/copilot-instructions.md
```

Markdown routing is the default recommendation because it works better across
macOS, Linux, and Windows clones.

## Why It Works

OmniContext treats AI configuration as architecture, not scattered editor
settings. Every assistant receives the same project context, but each tool still
gets its native entrypoint.

The result is a workspace where AI behavior is:

- Consistent across tools.
- Traceable to requirements.
- Safer around secrets and destructive changes.
- Easier to update.
- Easier to review.
- Less dependent on one assistant vendor or IDE.

## License

Add the license that matches your project before publishing or distributing this
workspace.
