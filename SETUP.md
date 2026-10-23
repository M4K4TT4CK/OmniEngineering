# Setup

Installation, optional components, and enabling the training loop.

## Requirements

| | |
|---|---|
| Python | 3.10 or later (`tomli` is pulled in automatically below 3.11) |
| Runtime dependencies | PyYAML |
| Platform | Linux, macOS, Windows |

Arbiter never executes the repository it scans, so no toolchain for the target
language is required — no `npm install`, no `terraform init`, no importing the
target's Python.

**Windows note:** the `arbiter` CLI is pure Python and runs the same in
PowerShell, cmd.exe, or a terminal as it does on Linux or macOS — everything
under **Install** and **Verify the install** below works as written. The
`.sh` scripts under `tools/` (`install_tools.sh`, `bootstrap_repo.sh`, and the
rest) are bash, not PowerShell; run those specific steps from Git Bash
(installed alongside Git for Windows) or WSL.

## Install

Arbiter is not on PyPI yet. Install from a checkout:

```bash
git clone https://github.com/paukennick/arbiter && cd arbiter
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[ast]"                             # ast: the language rule packs
./tools/install_tools.sh                            # optional: analyzers and tree-sitter grammars
arbiter scan ./my-repo
```

or straight from GitHub: `pip install "arbiter-eval[ast] @ git+https://github.com/paukennick/arbiter"`.
Without the `ast` extra Arbiter still runs; the language packs and AST
metrics report as not assessed.

### With OmniEngineering

If the repository uses the OmniEngineering workspace, let it install Arbiter
and wire the two together in one step: the `arbiter` MCP server in
`.mcp.json`, a `completion.arbiter_gate` rule so `omni gate` runs
`arbiter gate --changed`, and a starter `arbiter.yaml`.

```bash
./omni adopt --target ../your-project --include-cli --with-arbiter ../arbiter   # from the OmniEngineering checkout
./omni arbiter install --source ../arbiter                                     # in a repository adopted earlier
```

Both are safe to rerun and never overwrite a tuned `arbiter.yaml`. The source
can be a checkout, the GitHub URL, or a pip spec; `--skip-pip` writes only
the wiring.

### Optional extras

| Extra | Install | Enables |
|---|---|---|
| `ast` | `pip install -e ".[ast]"` | the language rule packs (`lang_python`, `lang_typescript`, `lang_go`, `lang_java`, `lang_ruby`: 176 of the 263 shipped rules), `ast_metrics` (function length, complexity and nesting in 11 languages), `aiapp`'s function boundaries and your own tree-sitter queries (`house_rules_ast`) |
| `dev` | `pip install -e ".[dev]"` | the test suite (pytest, hypothesis) |
| `tools` | `pip install -e ".[tools]"` | external analyzers as Python packages (ruff, bandit, checkov, semgrep; gitleaks comes from `install_tools.sh`) |
| `mcp` | `pip install -e ".[mcp]"` | `arbiter mcp`, the MCP server ([docs/mcp.md](docs/mcp.md)) |
| `api` | `pip install -e ".[api]"` | `arbiter api serve`, the hosted HTTPS API ([docs/hosted-api.md](docs/hosted-api.md)) |

Every extra is genuinely optional. A probe whose dependency is absent reports as
*not assessed* with the dependency named, and the coverage figure drops by
exactly what was not checked. Nothing silently becomes a pass.

## External analyzers

```bash
./tools/install_tools.sh
```

Installs checkov, semgrep, bandit, ruff and gitleaks at pinned versions, and
caches the tree-sitter grammars the language packs need (`ARBITER_GRAMMARS`,
default `python javascript typescript tsx go java ruby`; needs the `ast` extra
and network). Scans never download a grammar, so one not cached here reports
as not assessed — including the Rust, PHP, C, C++ and shell grammars
`ast_metrics` can use unless you add them to `ARBITER_GRAMMARS`. Everything
here is optional: a missing analyzer is recorded as not assessed with the
binary named. Nothing here fails a build.

Arbiter does not vendor or redistribute these tools. Their licenses are listed
in [NOTICE.md](NOTICE.md).

## Verify the install

```bash
arbiter probes .              # what can run here, and why anything cannot
python -m pytest -q -m "not slow"   # requires the dev extra; the fast tier
python -m pytest -q -m slow         # the analyzer-backed tier, once tools/install_tools.sh has run
```

`arbiter probes` is the fastest way to confirm which optional components the
environment actually has. The golden-fixture tests are the ones that matter:
`fixtures/legacy-platform` contains fourteen deliberately planted defects that
`.arbiter-expected.yaml` enumerates, and any rule or adapter change that
regresses on one of them fails the suite.

## Configure

Create `arbiter.yaml` at the repository root. The repository's own
`arbiter.yaml` is a working example — Arbiter evaluates itself with it.

See [docs/configuration.md](docs/configuration.md) for the full surface.

## Continuous integration

```bash
arbiter gate . --profile ci --baseline .arbiter/baseline.json
```

A GitHub Action is in `ci/github-action/` and a GitLab template in
`ci/gitlab/`. See [docs/ci.md](docs/ci.md).

## Enable the training loop

Training measures the rules against real repositories and records what it
learned. It needs a repository to persist that state into — without one, each
run starts from zero.

**1. Create the repository and push.**

```bash
./tools/bootstrap_repo.sh
```

Creates the repository, pushes this history to it, and reports what remains.
With the GitHub CLI installed and authenticated (`gh auth login`) that is the
whole job; without it, the script prints the two commands to run by hand.

It is safe to run twice. It never force-pushes and never rewrites history, and
if the repository already exists with commits in it, it stops and says so rather
than guessing which side should win. `--dry-run` prints what it would do and
changes nothing.

**2. Install the analyzers** (above), if you want the external tools measured
too.

**3. Install the Claude GitHub App**, if sessions should be able to work the
queue: <https://github.com/apps/claude>, scoped to this repository only.

**4. Nothing else.** `.github/workflows/train.yml` is already in the repository
and begins running the night after the first push.

The mechanical half of training runs unattended and only measures — it never
edits a rule. The half that needs judgement is handed off through
`training/WORKLIST.md`, which every nightly run regenerates. See
[docs/ci.md](docs/ci.md#continuous-training).
