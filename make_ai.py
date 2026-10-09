import argparse
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import queue
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import omni_graph
except ImportError:
    # omni_graph.py should always ship alongside make_ai.py (adopt/update copy
    # both -- see ADOPTION_CLI_FILES), but if it's ever missing (a partial
    # copy, a manual deletion, an update that hasn't caught up yet) every
    # other omni command must still work. Only `omni graph ...` needs it.
    omni_graph = None

GRAPH_DEFAULT_OUTPUT = ".ai/project-graph.json"
GRAPH_SEMANTIC_API_URL_ENV = "OMNI_GRAPH_SEMANTIC_API_URL"
RENDER_DEFAULT_OUTPUT = ".ai/project-graph.svg"
RENDER_DEFAULT_MAX_NODES = 300
RENDER_DEFAULT_DEPTH = 2

VIEW_DEFAULT_OUTPUT = ".ai/project-graph.html"
VIEW_DEFAULT_MAX_INITIAL = 500
REQUIREMENTS_PATH = Path(".ai/requirements/requirements.json")
REQUIREMENTS_ARCHIVE_PATH = Path(".ai/requirements/requirements-archive.json")
FAILURE_LEDGER_PATH = Path(".ai/failures/failure-ledger.json")
TEST_SUITES_PATH = Path(".ai/test-suites.json")
GATE_WAIVERS_PATH = Path(".ai/gate-waivers.jsonl")
RULESET_PATH = Path(".ai/rules/universal-engineering-ruleset.json")
REQUIREMENT_STATUSES = ["completed", "pending", "proposed", "blocked", "needs_review", "withdrawn"]
# Only these leave the live registry: a requirement that is still pending or blocked is live work, and
# archiving it would hide it from every session that loads the registry.
TERMINAL_REQUIREMENT_STATUSES = {"completed", "withdrawn"}
MCP_REGISTRATION_PATH = Path(".mcp.json")
MCP_PROBE_TIMEOUT_SECONDS = 30.0
# REQ-050: a successful live probe is remembered in the git directory (beside the gate's own state, never
# tracked) and reused for a day while .mcp.json, PATH and each server's executable are unchanged.
MCP_PROBE_MEMO_FILE = "omni-mcp-probe.json"
MCP_PROBE_MEMO_MAX_AGE_SECONDS = 24 * 60 * 60
MCP_PROBE_NO_MEMO_ENV = "OMNI_DOCTOR_NO_MEMO"
REQUIREMENT_PRIORITIES = ["critical", "high", "medium", "low"]
REQUIREMENT_STRING_FIELDS = ("id", "category", "title", "description")
REQUIREMENT_LIST_FIELDS = (
    "minimum_access_scope",
    "acceptance_criteria",
    "validation_required",
    "documentation_required",
    "risk_notes",
)
GATE_DEFAULT_BASE_REFS = ("origin/main", "origin/master", "main", "master")
CLAUDE_STOP_HOOK_COMMAND = '"$CLAUDE_PROJECT_DIR"/omni gate --hook'
PRE_COMMIT_HOOK_RELPATH = Path(".githooks/pre-commit")
PRE_COMMIT_HOOK_MARKER = "Installed by `omni hook install-git` (OmniEngineering)"
PRE_COMMIT_HOOK_SCRIPT = (
    "#!/bin/sh\n"
    f"# {PRE_COMMIT_HOOK_MARKER}. Do not hand-edit; rerun that command to update it.\n"
    "# Runs `omni gate` against what is about to be committed. Assistant-agnostic and OS-agnostic: Linux and macOS\n"
    "# invoke it directly, and Windows's Git for Windows always runs hooks through its own bundled sh, so this\n"
    "# runs there unchanged too. If neither python3 nor python is on PATH, it warns and lets the commit through\n"
    "# rather than blocking on a missing interpreter.\n"
    "set -e\n"
    'root=$(git rev-parse --show-toplevel) || exit 0\n'
    'cd "$root"\n'
    "if command -v python3 >/dev/null 2>&1; then py=python3\n"
    "elif command -v python >/dev/null 2>&1; then py=python\n"
    "else\n"
    '  echo "omni pre-commit: no python3/python on PATH; skipping the gate." >&2\n'
    "  exit 0\n"
    "fi\n"
    '"$py" "$root/omni" gate\n'
)
POST_COMMIT_HOOK_RELPATH = Path(".githooks/post-commit")
POST_COMMIT_HOOK_MARKER = "Installed by `omni hook install-git --with-graph-rebuild` (OmniEngineering)"
POST_COMMIT_HOOK_SCRIPT = (
    "#!/bin/sh\n"
    f"# {POST_COMMIT_HOOK_MARKER}. Do not hand-edit; rerun that command to update it.\n"
    "# Rebuilds the five-layer graph in the background after each commit, so `omni graph why/lineage/timeline`\n"
    "# is never more than one commit stale. Backgrounded: a full rebuild can take well over a minute on a large\n"
    "# repo or a slow filesystem, and this must never slow down `git commit` itself. A lock directory (mkdir is\n"
    "# atomic even on Windows/NTFS) skips a rebuild that is already running instead of piling them up; output\n"
    "# goes to .ai/.graph-build.log, never to the terminal.\n"
    'root=$(git rev-parse --show-toplevel) || exit 0\n'
    'cd "$root"\n'
    "if command -v python3 >/dev/null 2>&1; then py=python3\n"
    "elif command -v python >/dev/null 2>&1; then py=python\n"
    "else exit 0\n"
    "fi\n"
    'lock="$root/.ai/.graph-build.lock"\n'
    "(\n"
    '  mkdir -p "$root/.ai"\n'
    '  if mkdir "$lock" 2>/dev/null; then\n'
    "    trap 'rmdir \"$lock\" 2>/dev/null' EXIT\n"
    '    "$py" "$root/omni" graph build >"$root/.ai/.graph-build.log" 2>&1\n'
    "  fi\n"
    ") &\n"
    "exit 0\n"
)
TOOL_STATE_ROOT_DIRS = {".claude", ".claude-code-gui", ".idea", ".serena", ".vscode"}


REQUIRED_AI_FILES = [
    ".ai/core-context.md",
    ".ai/context-brief.md",
    ".ai/project-configuration.md",
    ".ai/.ignore",
    ".ai/context-manifest.json",
    ".ai/project-map.md",
    ".ai/entrypoints/universal.md",
    ".ai/entrypoints/codex.md",
    ".ai/entrypoints/claude.md",
    ".ai/entrypoints/cursor.md",
    ".ai/entrypoints/copilot.md",
    ".ai/entrypoints/kiro.md",
    ".ai/entrypoints/fallback-contract.md",
    ".ai/adapters/README.md",
    ".ai/adapters/generic-llm.md",
    ".ai/adapters/local-model.md",
    ".ai/adapters/model-router.md",
    ".ai/adapters/openrouter.md",
    ".ai/adapters/deepseek.md",
    ".ai/adapters/kiro.md",
    ".ai/playbooks/README.md",
    ".ai/playbooks/planning.md",
    ".ai/playbooks/implementation.md",
    ".ai/playbooks/review.md",
    ".ai/playbooks/testing.md",
    ".ai/playbooks/debugging.md",
    ".ai/playbooks/refactoring.md",
    ".ai/playbooks/migration.md",
    ".ai/playbooks/release.md",
    ".ai/playbooks/handoff.md",
    ".ai/checklists/README.md",
    ".ai/checklists/pre-implementation.md",
    ".ai/checklists/pre-completion.md",
    ".ai/checklists/public-release.md",
    ".ai/checklists/security.md",
    ".ai/knowledge/swebok/README.md",
    ".ai/knowledge/swebok/software-requirements.md",
    ".ai/knowledge/swebok/software-design.md",
    ".ai/knowledge/swebok/software-construction.md",
    ".ai/knowledge/swebok/software-testing.md",
    ".ai/knowledge/swebok/software-maintenance.md",
    ".ai/knowledge/swebok/software-configuration-management.md",
    ".ai/knowledge/swebok/software-engineering-management.md",
    ".ai/knowledge/swebok/software-engineering-process.md",
    ".ai/knowledge/swebok/software-engineering-models-and-methods.md",
    ".ai/knowledge/swebok/software-quality.md",
    ".ai/knowledge/swebok/software-engineering-professional-practice.md",
    ".ai/knowledge/swebok/software-engineering-economics.md",
    ".ai/knowledge/swebok/computing-foundations.md",
    ".ai/knowledge/swebok/mathematical-foundations.md",
    ".ai/knowledge/swebok/engineering-foundations.md",
    ".ai/knowledge/swebok/requirements-quality-checklist.md",
    ".ai/knowledge/swebok/design-quality-checklist.md",
    ".ai/knowledge/swebok/construction-quality-checklist.md",
    ".ai/knowledge/swebok/testing-quality-checklist.md",
    ".ai/knowledge/swebok/maintenance-impact-checklist.md",
    ".ai/knowledge/swebok/scm-checklist.md",
    ".ai/knowledge/swebok/engineering-management-checklist.md",
    ".ai/knowledge/swebok/process-tailoring-checklist.md",
    ".ai/knowledge/swebok/model-method-selection-checklist.md",
    ".ai/knowledge/swebok/quality-attribute-checklist.md",
    ".ai/knowledge/swebok/professional-practice-checklist.md",
    ".ai/knowledge/swebok/economics-decision-checklist.md",
    ".ai/knowledge/swebok/computing-foundations-checklist.md",
    ".ai/knowledge/swebok/mathematical-reasoning-checklist.md",
    ".ai/knowledge/swebok/engineering-foundations-checklist.md",
    ".ai/knowledge/swebok/srs-template.md",
    ".ai/knowledge/swebok/design-brief-template.md",
    ".ai/knowledge/swebok/test-plan-template.md",
    ".ai/knowledge/swebok/maintenance-plan-template.md",
    ".ai/knowledge/swebok/engineering-plan-template.md",
    ".ai/knowledge/swebok/process-improvement-template.md",
    ".ai/knowledge/swebok/quality-plan-template.md",
    ".ai/knowledge/swebok/tradeoff-analysis-template.md",
    ".ai/rules/universal-engineering-ruleset.json",
    ".ai/rules/controlled-implementation.json",
    ".ai/rules/completion-workflow.json",
    ".ai/rules/data-governance.json",
    ".ai/rules/fallback-llm-rules.json",
    ".ai/rules/oop-design.json",
    ".ai/rules/hci-ui-rules.json",
    ".ai/requirements/requirements.json",
    ".ai/failures/failure-ledger.json",
    ".ai/test-suites.json",
    ".ai/schemas/rulepack.schema.json",
    ".ai/schemas/requirements.schema.json",
    ".ai/schemas/failure-ledger.schema.json",
    ".ai/schemas/test-suites.schema.json",
    ".ai/schemas/universal-engineering-ruleset.schema.json",
]

RULEPACK_FILES = [
    ".ai/rules/controlled-implementation.json",
    ".ai/rules/completion-workflow.json",
    ".ai/rules/data-governance.json",
    ".ai/rules/fallback-llm-rules.json",
    ".ai/rules/oop-design.json",
    ".ai/rules/hci-ui-rules.json",
]

RULEPACK_ALIASES = {
    Path(path).stem: path for path in RULEPACK_FILES
}
RULEPACK_ALIASES.update({
    "controlled": ".ai/rules/controlled-implementation.json",
    "completion": ".ai/rules/completion-workflow.json",
    "data": ".ai/rules/data-governance.json",
    "fallback": ".ai/rules/fallback-llm-rules.json",
    "oop": ".ai/rules/oop-design.json",
    "hci": ".ai/rules/hci-ui-rules.json",
    "ui": ".ai/rules/hci-ui-rules.json",
})

GENERATED_FILE_MARKER = "<!-- Generated by OmniEngineering. Run `./omni sync` to refresh. -->"

FALLBACK_CONTRACT = """# Fallback Operating Contract

If you cannot access, skip, or fail to follow the `.ai/` source files, apply
these fallback rules exactly:

1. Read `.ai/core-context.md`, `.ai/rules/universal-engineering-ruleset.json`,
   `.ai/rules/controlled-implementation.json`, `.ai/rules/completion-workflow.json`,
   and `.ai/project-configuration.md` before editing when they are available.
   Query the requirements registry with `omni requirement show|list|search`;
   do not read `.ai/requirements/requirements.json` in full.
2. If any required file is unavailable, say which file is unavailable and use
   this fallback contract as the controlling instruction set.
3. Assign or confirm a `REQ-###` requirement ID before work begins.
4. State the minimum access scope before inspecting files.
5. Read `.ai/project-map.md` before broad traversal when it is available. If it
   is missing or stale after structural changes, run `omni map` or `./omni map`.
6. Inspect only files needed for the active requirement. Do not scan the whole
   repository unless the task cannot be completed safely without it.
7. Do not read or expose `.env`, `.env.*`, private keys, certificates,
   credentials, database files, logs, build artifacts, dependency folders, cache
   directories, or anything listed in `.ai/.ignore`.
8. Do not modify unrelated files, unrelated deployment scripts, unrelated
   infrastructure, generated dependency folders, secrets, credentials, or local
   environment files.
9. Make the smallest safe maintainable change. Preserve existing behavior unless
   it conflicts with the active requirement.
10. Do not introduce dependencies, schema changes, destructive data changes, or
   public interface changes unless the requirement explicitly calls for them.
11. Use clear names, focused functions, explicit data contracts, and existing
    project conventions.
12. Update relevant docs when behavior, setup, commands, architecture, APIs,
    data models, or workflows change.
13. Update `CHANGELOG.md` after each completed task.
14. Update the requirements registry with `omni requirement add|update|complete`
    (never by hand-editing the JSON) when a requirement is added, completed,
    blocked, or materially changed.
15. Learn from failures. Before editing a file, run `omni graph why <path>` (or
    `omni failure search <text>`) to see the requirements, tests and earlier
    failures that touch it. When you fix a defect, a failed validation or build,
    or a regression, record it with `omni failure add|update`: the symptom, the
    root cause (why, not just what), the regression test, and the rule or
    playbook that stops it recurring. If the failure exposes a missing or unclear
    instruction, change that rulepack or playbook in the same task.
16. Run relevant validation. For OmniContext workspace changes, run
    `omni doctor` or `./omni doctor`; when assistant entrypoint files change,
    run `omni sync` or `./omni sync`. Before claiming completion, run
    `omni gate` and fix or explicitly waive every failure it reports.
17. Do not claim completion if validation was skipped. Explain why it was not
    run.
18. Final output must include requirement ID and status, files changed,
    validation performed, documentation and changelog status, risks or
    follow-ups, a commit entry sentence, and pull request information.
"""

ENTRYPOINT_SOURCES = {
    ".ai/entrypoints/universal.md": f"""# Universal LLM Context

{GENERATED_FILE_MARKER}

Read this file first when using any local model, hosted model, chat UI,
terminal wrapper, or coding tool that does not have a native OmniContext
entrypoint.

Then read `.ai/context-brief.md` first. Use it to choose the smallest relevant
context profile before loading deeper rules, styles, and workflows inside the
`.ai/` directory. Treat
`.ai/rules/universal-engineering-ruleset.json` as the controlling global
ruleset. Apply the controlled implementation workflow, security guardrails,
completion workflow, project configuration, and project-specific rules.

For machine-readable loading order, inspect `.ai/context-manifest.json`.

{FALLBACK_CONTRACT}
""",
    ".ai/entrypoints/codex.md": f"""# Codex Configuration

{GENERATED_FILE_MARKER}

Read `.ai/context-brief.md` first, then prioritize only the relevant rules,
styles, and workflows located inside the `.ai/` directory before writing code.
Treat `.ai/rules/universal-engineering-ruleset.json`
as the controlling global ruleset. Apply the controlled implementation workflow,
security guardrails, completion workflow, project configuration, and
project-specific rules.

{FALLBACK_CONTRACT}
""",
    ".ai/entrypoints/claude.md": f"""# Claude Configuration

{GENERATED_FILE_MARKER}

Read `.ai/context-brief.md` first, then prioritize only the relevant rules,
styles, and workflows located inside the `.ai/` directory before writing code.
Treat `.ai/rules/universal-engineering-ruleset.json`
as the controlling global ruleset. Apply the controlled implementation workflow,
security guardrails, completion workflow, project configuration, and
project-specific rules.

## Registry and Gate Discipline

- Never read `.ai/requirements/requirements.json` in full and never hand-edit it.
  Query with `./omni requirement show <ID>`, `list --status pending`, or
  `search <text>`; change it with `./omni requirement add|update|complete`.
- Failures are data, not noise. Before editing a file run `./omni graph why <path>`;
  when you fix a defect or a failed check, record it with `./omni failure add`
  (symptom, root cause, regression test, the rule or playbook that prevents a
  repeat). `./omni requirement complete` refuses defect requirements without an
  entry.
- Before reporting a task complete, run `./omni gate`. If it fails, fix the gap
  or record an explicit waiver with `./omni waive <rule-id> --reason "..."`.
  Do not claim completion while it fails.

If you cannot access, skip, or fail to follow the `.ai/` source files, read
`.ai/entrypoints/fallback-contract.md` and apply it as the controlling
instruction set.
""",
    ".ai/entrypoints/cursor.md": f"""# Cursor Configuration

{GENERATED_FILE_MARKER}

Read `.ai/context-brief.md` first, then prioritize only the relevant rules,
styles, and workflows located inside the `.ai/` directory before writing code.
Treat `.ai/rules/universal-engineering-ruleset.json`
as the controlling global ruleset. Apply the controlled implementation workflow,
security guardrails, completion workflow, project configuration, and
project-specific rules.

{FALLBACK_CONTRACT}
""",
    ".ai/entrypoints/copilot.md": f"""# Copilot Configuration

{GENERATED_FILE_MARKER}

Read `.ai/context-brief.md` first, then prioritize only the relevant rules,
styles, and workflows located inside the `.ai/` directory before writing code.
Treat `.ai/rules/universal-engineering-ruleset.json`
as the controlling global ruleset. Apply the controlled implementation workflow,
security guardrails, completion workflow, project configuration, and
project-specific rules.

{FALLBACK_CONTRACT}
""",
    ".ai/entrypoints/kiro.md": f"""# OmniContext Steering

{GENERATED_FILE_MARKER}

Read `.ai/context-brief.md` first, then prioritize only the relevant rules,
styles, and workflows located inside the `.ai/` directory before writing code.
Treat `.ai/rules/universal-engineering-ruleset.json`
as the controlling global ruleset. Apply the controlled implementation workflow,
security guardrails, completion workflow, project configuration, and
project-specific rules.

For portable model context, read `LLM_CONTEXT.md` and `.ai/context-manifest.json`.

{FALLBACK_CONTRACT}
""",
    ".ai/entrypoints/fallback-contract.md": f"{GENERATED_FILE_MARKER}\n\n{FALLBACK_CONTRACT}",
}

ASSISTANT_POINTER_TARGETS = {
    "LLM_CONTEXT.md": ".ai/entrypoints/universal.md",
    "AGENTS.md": ".ai/entrypoints/codex.md",
    "CLAUDE.md": ".ai/entrypoints/claude.md",
    ".cursorrules": ".ai/entrypoints/cursor.md",
    ".github/copilot-instructions.md": ".ai/entrypoints/copilot.md",
    ".kiro/steering/omnicontext.md": ".ai/entrypoints/kiro.md",
}


def render_assistant_pointer(file_path: str, source_path: str) -> str:
    title = {
        "LLM_CONTEXT.md": "Universal LLM Context",
        "AGENTS.md": "Codex Configuration",
        "CLAUDE.md": "Claude Configuration",
        ".cursorrules": "Cursor Configuration",
        ".github/copilot-instructions.md": "Copilot Configuration",
        ".kiro/steering/omnicontext.md": "OmniContext Steering",
    }[file_path]
    return f"""# {title}

{classification_block()}<!-- Generated by OmniEngineering. Run `./omni sync` to refresh. -->

This file is intentionally small so repository-root assistant files do not
clog the main workspace.

Read `{source_path}` for the full tool-specific instructions, then follow
`.ai/context-brief.md` and `.ai/context-manifest.json` for the complete loading
order.

If `{source_path}` is unavailable, read `.ai/entrypoints/fallback-contract.md`
and apply it as the controlling instruction set.
"""


def assistant_pointers() -> dict[str, str]:
    return {
        file_path: render_assistant_pointer(file_path, source_path)
        for file_path, source_path in ASSISTANT_POINTER_TARGETS.items()
    }

SYNCED_IGNORE_FILES = {
    ".cursorignore": "# Generated by omni sync from .ai/.ignore\n\n",
}

JSON_FILES = [
    ".ai/context-manifest.json",
    ".ai/rules/universal-engineering-ruleset.json",
    *RULEPACK_FILES,
    ".ai/requirements/requirements.json",
    ".ai/failures/failure-ledger.json",
    ".ai/test-suites.json",
    ".ai/schemas/rulepack.schema.json",
    ".ai/schemas/requirements.schema.json",
    ".ai/schemas/failure-ledger.schema.json",
    ".ai/schemas/test-suites.schema.json",
    ".ai/schemas/universal-engineering-ruleset.schema.json",
]

DEFAULT_MAP_EXCLUDED_DIRS = {
    ".codex-local",
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "vendor",
    "venv",
}

DEFAULT_MAP_EXCLUDED_FILES = {
    ".DS_Store",
}

PROJECT_MAP_DEFAULT_OUTPUT = ".ai/project-map.md"

ALLOWED_ROOT_FILES = {
    ".cursorignore",
    ".cursorrules",
    ".gitattributes",
    ".gitignore",
    "AGENTS.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "LLM_CONTEXT.md",
    "NOTICE",
    ".mcp.json",
    "README.md",
    "TRADEMARKS.md",
    "make_ai.py",
    "omni",
    "omni_graph.py",
    "omni_mcp.py",
    "pyproject.toml",
}

ALLOWED_ROOT_DIRS = {
    ".ai",
    ".codex-local",
    ".git",
    ".github",
    ".kiro",
    "LICENSES",
    "assets",
    "design",
    "tests",
}

ROOT_CLUTTER_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "vendor",
    "venv",
    ".venv",
}

LOCAL_ONLY_PUBLIC_PATHS = [
    "design/brand-system.md",
]

MAX_ASSISTANT_SHIM_LINES = 15

CONTEXT_PROFILE_ALIASES = {
    "min": "minimum",
    "minimum": "minimum",
    "impl": "implementation",
    "implementation": "implementation",
    "review": "review",
    "deep": "deep_policy",
    "deep-policy": "deep_policy",
    "deep_policy": "deep_policy",
}

ADOPTION_TOOL_FILES = {
    "universal": ["LLM_CONTEXT.md"],
    "codex": ["AGENTS.md"],
    "claude": ["CLAUDE.md"],
    "cursor": [".cursorrules", ".cursorignore"],
    "copilot": [".github/copilot-instructions.md"],
    "kiro": [".kiro/steering/omnicontext.md"],
}

ADOPTION_CLI_FILES = [
    "omni",
    "make_ai.py",
    "omni_graph.py",
    "omni_mcp.py",
    ".ai/graph-viewer/viewer.html",
    ".ai/graph-viewer/3d-force-graph.min.js",
    ".ai/graph-viewer/THIRD_PARTY_NOTICES.md",
]
ADOPTION_LEGAL_FILES = [
    "LICENSE",
    "NOTICE",
    "TRADEMARKS.md",
    "CONTRIBUTING.md",
    "LICENSES",
]
ADOPTION_PRESENTATION_FILES = [
    "assets/brand",
    "assets/omni-context.svg",
    "design",
]

OMNI_VERSION_FILE = ".ai/omni-version.json"

# Arbiter (github.com/paukennick/arbiter) is the repository evaluator this
# workspace pairs with: `omni gate` can run `arbiter gate` through a
# `command` rule, and `omni doctor` starts its MCP server. Adopting with
# --with-arbiter installs the package and writes the wiring files.
ARBITER_DEFAULT_SOURCE = "git+https://github.com/paukennick/arbiter"
ARBITER_PIP_EXTRAS = "mcp"
ARBITER_GATE_RULE_ID = "completion.arbiter_gate"
ARBITER_STARTER_CONFIG = """# Arbiter policy for this repository. See docs/configuration.md in the
# arbiter checkout for every key; this is the minimum `omni gate` relies on.
version: 1
profile: offline

gate:
  fail_on:
    severity: critical
    new: high
  gate_on_inferred: false
"""
# The completion rule `omni arbiter install` writes. `--baseline` makes the
# gate label findings new/existing against .arbiter/baseline.json (REQ-038);
# json is what omni reads back, sarif feeds code-scanning upload, pr-comment
# is the markdown a CI job posts. `omni arbiter update` rewrites an adopter's
# copy of `validation` to this when asked; `omni update` only warns.
ARBITER_GATE_RULE: dict[str, Any] = {
    "id": ARBITER_GATE_RULE_ID,
    "severity": "required",
    "statement": "A change passes Arbiter's own gate (`arbiter gate . --changed <base>` under arbiter.yaml) before it is reported complete.",
    "scope": ["completion", "validation"],
    "validation": {
        "type": "command",
        "run": "arbiter gate . --changed {base} --profile offline --baseline .arbiter/baseline.json --out arbiter-out/omni-gate --format json,sarif,pr-comment",
        "when_changed": ["**"],
        "ignore": [".ai/**", "CHANGELOG.md", "*.md", "docs/**"],
        "timeout": 600,
    },
}
ARBITER_BASELINE_PATH = ".arbiter/baseline.json"
ARBITER_GATE_OUT_DIR = "arbiter-out/omni-gate"
ARBITER_CONFIG_FILE = "arbiter.yaml"

# Files an adopter owns outright once copied -- omni update never touches
# these, no matter what changes upstream.
ADOPTER_OWNED_FILES = {
    ".ai/project-configuration.md",
    ".ai/project-map.md",
    ".ai/requirements/requirements.json",
    ".ai/failures/failure-ledger.json",
    ".ai/test-suites.json",
}

# Files omni update is allowed to 3-way-merge: every required .ai file that
# isn't adopter-owned and isn't purely generated output (ENTRYPOINT_SOURCES
# keys are regenerated by sync from make_ai.py's own templates, not merged
# directly), plus the CLI itself. Derived from REQUIRED_AI_FILES so a new
# rulepack/playbook/checklist automatically becomes mergeable without a
# second list to keep in sync.
TEMPLATE_MANAGED_FILES = [
    path
    for path in REQUIRED_AI_FILES
    if path not in ADOPTER_OWNED_FILES and path not in ENTRYPOINT_SOURCES
] + ADOPTION_CLI_FILES

REQUIRED_RULESET_KEYS = {
    "prompt_title",
    "version",
    "purpose",
    "agent_role",
    "configuration",
    "global_operating_principles",
    "required_startup_sequence",
    "requirement_template",
    "implementation_rules",
    "quality_standards",
    "required_output_after_each_task",
    "final_output_required",
    "engineer_modification_instructions",
}

REQUIRED_CONFIGURATION_KEYS = {
    "project_name",
    "repository_type",
    "primary_language_or_stack",
    "package_manager",
    "build_command",
    "test_command",
    "lint_command",
    "typecheck_command",
    "changelog_location",
    "documentation_locations",
    "branching_or_pr_standard",
    "comment_style",
    "requirement_id_prefix",
}

REQUIRED_REQUIREMENT_KEYS = {
    "id",
    "category",
    "title",
    "description",
    "priority",
    "status",
    "minimum_access_scope",
    "acceptance_criteria",
    "validation_required",
    "documentation_required",
    "risk_notes",
}

REQUIRED_RULEPACK_KEYS = {
    "rulepack_id",
    "version",
    "title",
    "purpose",
    "applies_to",
    "rules",
}

REQUIRED_RULE_KEYS = {
    "id",
    "severity",
    "statement",
}

REQUIRED_MANIFEST_KEYS = {
    "version",
    "purpose",
    "entrypoints",
    "entrypoint_sources",
    "indexes",
    "context_profiles",
    "adapter_prompts",
    "ignore_files",
    "project_map",
    "required_read_order",
    "task_playbooks",
    "checklists",
    "knowledge_packs",
    "security_exclusions",
    "validation_commands",
    "sync_command",
    "fallback_contract_source",
    "context_retention_strategy",
}

PLACEHOLDER_PATTERN = re.compile(r"<[^>\n]+>")


class DoctorReport:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.passed: list[str] = []
        # Workspace posture (Arbiter report state, open failures, open requirements); see compute_posture().
        self.posture: dict[str, Any] = {}

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def pass_check(self, message: str) -> None:
        self.passed.append(message)

    def print(self) -> None:
        print("OmniEngineering doctor")
        print("======================")
        for message in self.passed:
            print(f"PASS  {message}")
        for message in self.warnings:
            print(f"WARN  {message}")
        for message in self.errors:
            print(f"FAIL  {message}")
        print()
        print(
            f"Result: {len(self.passed)} passed, "
            f"{len(self.warnings)} warnings, {len(self.errors)} errors"
        )
        if self.posture:
            print(format_posture(self.posture))

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_json(self) -> dict[str, Any]:
        """The `--json` shape. `schema_version` 2 (REQ-046): `posture.arbiter` carries `wired`, `full` and `gate`;
        keys are only ever added. The schema 1 top-level keys under `posture.arbiter` (`present`, `fresh`,
        `reason`, `grade`, `coverage`, `new_high_or_above`, `gate_passed`, ...) are kept for one release,
        populated from the gate block; readers should move to `posture.arbiter.gate` before they go."""
        return {
            "schema_version": 2,
            "ok": self.ok,
            "passed": list(self.passed),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "posture": dict(self.posture),
        }


def read_json(path: Path, report: DoctorReport) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        report.error(f"Missing JSON file: {path}")
    except json.JSONDecodeError as exc:
        report.error(f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}")
    return None


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Match the file's existing style so a CLI write never re-escapes (or
    # un-escapes) every non-ASCII character already in it.
    escaped = path.is_file() and "\\u" in path.read_text(encoding="utf-8")
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(json.dumps(value, indent=2, ensure_ascii=escaped) + "\n", encoding="utf-8")
    temp_path.replace(path)


def git_current_ref(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def git_show_file(repo_root: Path, ref: str, relative_path: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "show", f"{ref}:{relative_path}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None  # file didn't exist at that ref -- treated as "new upstream"
    return result.stdout


def three_way_merge(ours: str, base: str, theirs: str) -> tuple[str, bool]:
    """Merge via `git merge-file` (ships with git, no extra dependency).

    Returns (merged_text, clean) -- clean is False when conflict markers were
    inserted into merged_text and a human needs to resolve them by hand.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        ours_path = tmp_path / "ours"
        base_path = tmp_path / "base"
        theirs_path = tmp_path / "theirs"
        ours_path.write_text(ours, encoding="utf-8")
        base_path.write_text(base, encoding="utf-8")
        theirs_path.write_text(theirs, encoding="utf-8")
        result = subprocess.run(
            [
                "git", "merge-file", "-p", "--marker-size=7",
                "-L", "ours (your customization)",
                "-L", "base (last synced template version)",
                "-L", "theirs (new template version)",
                str(ours_path), str(base_path), str(theirs_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout, result.returncode == 0


def write_omni_version_file(source_root: Path, target_root: Path) -> None:
    """Record the template source and ref. Keys this function does not own (such as
    the `arbiter` block `record_arbiter_version` writes) are read back and kept."""
    path = target_root / OMNI_VERSION_FILE
    payload: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = load_json(path)
        except (OSError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict):
            payload = existing
    payload.update({
        "source": str(source_root),
        "ref": git_current_ref(source_root),
        "last_synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    write_json(path, payload)


def read_omni_version_file(target_root: Path = Path(".")) -> dict[str, Any] | None:
    path = target_root / OMNI_VERSION_FILE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def read_ignore_patterns(paths: list[Path] | None = None) -> list[str]:
    ignore_files = paths or [Path(".ai/.ignore"), Path(".gitignore")]
    patterns: list[str] = []
    for path in ignore_files:
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and not line.startswith("!"):
                patterns.append(line)
    return patterns


def path_matches_pattern(path: Path, pattern: str, is_dir: bool) -> bool:
    normalized = path.as_posix()
    name = path.name
    if pattern.endswith("/"):
        directory_pattern = pattern.rstrip("/")
        return (
            is_dir and fnmatch.fnmatch(name, directory_pattern)
        ) or normalized == directory_pattern or normalized.startswith(f"{directory_pattern}/")
    if "/" in pattern:
        return fnmatch.fnmatch(normalized, pattern)
    return fnmatch.fnmatch(name, pattern) or any(
        fnmatch.fnmatch(part, pattern) for part in path.parts
    )


def should_skip_map_path(path: Path, is_dir: bool, ignore_patterns: list[str], include_ai: bool) -> bool:
    if path == Path("."):
        return False
    if is_dir and path.name in DEFAULT_MAP_EXCLUDED_DIRS:
        return True
    if not include_ai and (path == Path(".ai") or ".ai" in path.parts):
        return True
    if not is_dir and path.name in DEFAULT_MAP_EXCLUDED_FILES:
        return True
    return any(path_matches_pattern(path, pattern, is_dir) for pattern in ignore_patterns)


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "item"


def next_requirement_id(requirements: dict[str, Any]) -> str:
    prefix = str(requirements.get("requirement_id_prefix", "REQ"))
    highest = 0
    ids = {
        str(requirement.get("id", ""))
        for requirement in requirements.get("requirements", [])
        if isinstance(requirement, dict)
    } | all_requirement_ids()
    for requirement_id in ids:
        match = re.fullmatch(rf"{re.escape(prefix)}-(\d{{3}})", requirement_id)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}-{highest + 1:03d}"


def resolve_rulepack_path(rulepack: str) -> Path:
    normalized = rulepack.strip()
    if normalized in RULEPACK_ALIASES:
        return Path(RULEPACK_ALIASES[normalized])

    path = Path(normalized)
    if path.suffix != ".json":
        path = Path(".ai/rules") / f"{normalized}.json"
    return path


def verify_required_ai_files(report: DoctorReport) -> None:
    missing_files = [path for path in REQUIRED_AI_FILES if not Path(path).is_file()]
    if missing_files:
        for path in missing_files:
            report.error(f"Missing required source-of-truth file: {path}")
        return
    report.pass_check("Required .ai source-of-truth files exist")


def validate_json_files(report: DoctorReport) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for file_path in JSON_FILES:
        path = Path(file_path)
        value = read_json(path, report)
        if value is not None:
            parsed[file_path] = value
    if len(parsed) == len(JSON_FILES):
        report.pass_check("JSON files parse successfully")
    return parsed


def validate_ruleset(ruleset: Any, report: DoctorReport) -> None:
    if not isinstance(ruleset, dict):
        report.error("Universal ruleset must be a JSON object")
        return

    missing = REQUIRED_RULESET_KEYS - set(ruleset)
    if missing:
        report.error(f"Universal ruleset missing required keys: {sorted(missing)}")
    else:
        report.pass_check("Universal ruleset contains required top-level keys")

    configuration = ruleset.get("configuration", {})
    if not isinstance(configuration, dict):
        report.error("Universal ruleset configuration must be an object")
        return

    missing_config = REQUIRED_CONFIGURATION_KEYS - set(configuration)
    if missing_config:
        report.error(f"Ruleset configuration missing keys: {sorted(missing_config)}")
    else:
        report.pass_check("Ruleset configuration contains required keys")

    placeholders = find_placeholders(ruleset)
    if placeholders:
        preview = ", ".join(sorted(placeholders)[:8])
        report.warning(
            "Ruleset still contains project placeholders "
            f"({preview}); fill these before using the workspace in a target repo"
        )


def requirement_shape_problems(requirement: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for key in REQUIREMENT_STRING_FIELDS:
        if key in requirement and (not isinstance(requirement[key], str) or not requirement[key].strip()):
            problems.append(f"{key} must be a non-empty string")
    if "priority" in requirement and requirement["priority"] not in REQUIREMENT_PRIORITIES:
        problems.append(f"priority must be one of {REQUIREMENT_PRIORITIES}")
    if "status" in requirement and requirement["status"] not in REQUIREMENT_STATUSES:
        problems.append(f"status must be one of {REQUIREMENT_STATUSES}")
    for key in REQUIREMENT_LIST_FIELDS:
        if key not in requirement:
            continue
        value = requirement[key]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            problems.append(f"{key} must be an array of strings (found {type(value).__name__})")
    return problems


def check_requirement_entries(items: list[Any], label: str, report: DoctorReport, seen_ids: set[str]) -> None:
    for index, requirement in enumerate(items, start=1):
        if not isinstance(requirement, dict):
            report.error(f"{label} entry {index} must be an object")
            continue

        missing = REQUIRED_REQUIREMENT_KEYS - set(requirement)
        if missing:
            report.error(
                f"{label} entry {requirement.get('id', index)} missing keys: "
                f"{sorted(missing)}"
            )

        problems = requirement_shape_problems(requirement)
        if problems:
            report.error(
                f"{label} entry {requirement.get('id', index)} violates the registry "
                f"schema: {'; '.join(problems)}"
            )

        requirement_id = requirement.get("id")
        if not isinstance(requirement_id, str) or not re.fullmatch(r"[A-Z]+-\d{3}", requirement_id):
            report.error(f"{label} entry {index} has invalid id: {requirement_id}")
            continue

        if requirement_id in seen_ids:
            report.error(f"Duplicate requirement id: {requirement_id}")
        seen_ids.add(requirement_id)


def validate_requirements(requirements: Any, report: DoctorReport) -> None:
    if not isinstance(requirements, dict):
        report.error("Requirements registry must be a JSON object")
        return

    items = requirements.get("requirements")
    if not isinstance(items, list):
        report.error("Requirements registry must contain a requirements array")
        return

    errors_before = len(report.errors)
    seen_ids: set[str] = set()
    check_requirement_entries(items, "Requirement", report, seen_ids)

    if REQUIREMENTS_ARCHIVE_PATH.is_file():
        try:
            archive = load_json(REQUIREMENTS_ARCHIVE_PATH)
        except json.JSONDecodeError as exc:
            report.error(f"Invalid JSON in {REQUIREMENTS_ARCHIVE_PATH}: line {exc.lineno}, column {exc.colno}")
            archive = None
        if isinstance(archive, dict) and isinstance(archive.get("requirements"), list):
            check_requirement_entries(archive["requirements"], "Archived requirement", report, seen_ids)
        elif archive is not None:
            report.error(f"{REQUIREMENTS_ARCHIVE_PATH} must contain a requirements array")

    if len(report.errors) == errors_before:
        report.pass_check("Requirements registry is structurally valid (types and enums checked)")
    elif seen_ids:
        report.warning("Requirements registry was partially readable")


# --- Requirement ids across registries (REQ-036) ---------------------------------------------------


def validate_requirement_ids(report: DoctorReport) -> None:
    """One id names one requirement, across the root registry, its archive and every vendored workspace's
    registries. A pair inside the root registry or its archive is already reported by validate_requirements,
    so only a clash that reaches into a vendored workspace (or spans two of them) is reported here."""
    cwd = Path(".").resolve()
    root_sources = [REQUIREMENTS_PATH, REQUIREMENTS_ARCHIVE_PATH]
    sources: list[Path] = list(root_sources)
    workspaces = vendored_workspace_dirs()
    for workspace in workspaces:
        sources.extend(sorted((workspace / ".ai" / "requirements").glob("requirements*.json")))

    def display(path: Path) -> str:
        try:
            return path.resolve().relative_to(cwd).as_posix()
        except ValueError:
            return path.as_posix()

    root_labels = {display(path) for path in root_sources}
    locations: dict[str, list[str]] = {}
    for path in sources:
        if not path.is_file():
            continue
        try:
            registry = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        label = display(path)
        for item in registry.get("requirements", []) if isinstance(registry, dict) else []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                seen = locations.setdefault(item["id"], [])
                if label not in seen:
                    seen.append(label)
    duplicates = 0
    for requirement_id, labels in sorted(locations.items()):
        if len(labels) > 1 and not set(labels) <= root_labels:
            duplicates += 1
            report.error(f"Duplicate requirement id {requirement_id} in {' and '.join(labels)}")
    if not duplicates and locations:
        report.pass_check(
            f"Requirement ids are unique across the registry, the archive and {len(workspaces)} vendored workspace(s)"
        )


# --- Requirement id aliases (REQ-044) --------------------------------------------------------------


def validate_requirement_aliases(report: DoctorReport) -> None:
    """Every `id_aliases` entry must point at a requirement that exists (registry or archive), and no alias key
    may still be a live id: one id names one requirement, so it cannot be both an alias and an entry. Alias
    keys are not requirement ids, so the duplicate check (`validate_requirement_ids`) never sees them."""
    if not REQUIREMENTS_PATH.is_file():
        return
    try:
        registry = load_json(REQUIREMENTS_PATH)
    except (OSError, json.JSONDecodeError):
        return  # already reported by the JSON checks
    if not isinstance(registry, dict) or "id_aliases" not in registry:
        return
    aliases = registry["id_aliases"]
    if not isinstance(aliases, dict):
        report.error("Requirements registry: id_aliases must be an object of {old id: current id}")
        return
    live = all_requirement_ids()
    errors_before = len(report.errors)
    for old, new in sorted(aliases.items(), key=lambda pair: str(pair[0])):
        if not isinstance(new, str) or not REQUIREMENT_ID_PATTERN.fullmatch(str(old)) or not REQUIREMENT_ID_PATTERN.fullmatch(new):
            report.error(f"Requirements registry: id alias {old!r} -> {new!r} must map one PREFIX-### id to another")
            continue
        if new not in live:
            report.error(
                f"Requirements registry: id alias {old} -> {new} points at a requirement that is in neither the registry "
                f"nor the archive"
            )
        if old in live:
            report.error(
                f"Requirements registry: {old} is both a live requirement id and an alias (for {new}); an id cannot be both"
            )
    if aliases and len(report.errors) == errors_before:
        report.pass_check(f"Requirement id aliases resolve to registered ids ({len(aliases)} alias(es))")


def validate_failure_ledger(ledger: Any, report: DoctorReport) -> None:
    if ledger is None:
        return  # missing or unreadable files are already reported by the required-file and JSON checks
    if not isinstance(ledger, dict) or not isinstance(ledger.get("failures"), list):
        report.error("Failure ledger must be a JSON object with a failures array")
        return
    if omni_graph is None:
        return
    result = omni_graph.check_failure_ledger(Path("."))
    for problem in result["problems"]:
        report.error(f"Failure ledger: {problem}")
    if result["ok"]:
        report.pass_check(f"Failure ledger is complete ({result['failures']} failure(s) recorded)")


def validate_test_suites(config: Any, report: DoctorReport) -> None:
    if config is None or omni_graph is None:
        return
    if not isinstance(config, dict) or not isinstance(config.get("suites"), list):
        report.error("Test suite registry must be a JSON object with a suites array")
        return
    result = omni_graph.check_test_suites(Path("."), project_source_files())
    for problem in result["problems"]:
        report.error(f"Test suites: {problem}")
    if result["unregistered_files"]:
        report.warning(
            f"{len(result['unregistered_files'])} detected test file(s) are not in a registered suite "
            f"({', '.join(result['unregistered_suites'][:4])}); run `omni test detect --write` or `omni test add`"
        )
    if result["ok"]:
        report.pass_check(f"Test suite registry is valid ({result['suites']} suite(s) registered)")


def validate_graph_config(report: DoctorReport) -> None:
    if omni_graph is None or not Path(omni_graph.GRAPH_CONFIG_PATH).is_file():
        return
    problems: list[str] = []
    omni_graph.graph_config(Path("."), problems)
    for problem in problems:
        report.error(problem)
    if not problems:
        report.pass_check(f"{omni_graph.GRAPH_CONFIG_PATH} is valid")


def validate_rulepacks(parsed: dict[str, Any], report: DoctorReport) -> None:
    seen_rulepack_ids: set[str] = set()
    seen_rule_ids: set[str] = set()

    for file_path in RULEPACK_FILES:
        rulepack = parsed.get(file_path)
        if not isinstance(rulepack, dict):
            report.error(f"Rulepack must be a JSON object: {file_path}")
            continue

        missing = REQUIRED_RULEPACK_KEYS - set(rulepack)
        if missing:
            report.error(f"Rulepack {file_path} missing keys: {sorted(missing)}")
            continue

        rulepack_id = rulepack.get("rulepack_id")
        if not isinstance(rulepack_id, str) or not re.fullmatch(r"[a-z][a-z0-9_\\.]*", rulepack_id):
            report.error(f"Rulepack {file_path} has invalid rulepack_id: {rulepack_id}")
        elif rulepack_id in seen_rulepack_ids:
            report.error(f"Duplicate rulepack_id: {rulepack_id}")
        else:
            seen_rulepack_ids.add(rulepack_id)

        rules = rulepack.get("rules")
        if not isinstance(rules, list) or not rules:
            report.error(f"Rulepack {file_path} must contain a non-empty rules array")
            continue

        for index, rule in enumerate(rules, start=1):
            if not isinstance(rule, dict):
                report.error(f"Rule {index} in {file_path} must be an object")
                continue

            missing_rule_keys = REQUIRED_RULE_KEYS - set(rule)
            if missing_rule_keys:
                report.error(
                    f"Rule {index} in {file_path} missing keys: "
                    f"{sorted(missing_rule_keys)}"
                )
                continue

            rule_id = rule.get("id")
            if not isinstance(rule_id, str) or not re.fullmatch(r"[a-z][a-z0-9_\\.]*", rule_id):
                report.error(f"Rule {index} in {file_path} has invalid id: {rule_id}")
            elif rule_id in seen_rule_ids:
                report.error(f"Duplicate rule id across rulepacks: {rule_id}")
            else:
                seen_rule_ids.add(rule_id)

            severity = rule.get("severity")
            if severity not in {"required", "recommended", "advisory"}:
                report.error(f"Rule {rule_id} in {file_path} has invalid severity: {severity}")

            statement = rule.get("statement")
            if not isinstance(statement, str) or not statement.strip():
                report.error(f"Rule {rule_id} in {file_path} must have a non-empty statement")

    if seen_rulepack_ids and seen_rule_ids and not report.errors:
        report.pass_check("Structured JSON rulepacks are valid")


def manifest_paths(value: Any) -> list[str]:
    paths: list[str] = []
    if isinstance(value, str):
        if (
            value.startswith(".ai/")
            or value.startswith(".github/")
            or value.startswith(".kiro/")
            or value in ASSISTANT_POINTER_TARGETS
            or value in {
                "omni",
                "make_ai.py",
                "README.md",
                "CHANGELOG.md",
                "LICENSE",
                "NOTICE",
                "TRADEMARKS.md",
                "CONTRIBUTING.md",
            }
        ):
            paths.append(value)
    elif isinstance(value, list):
        for item in value:
            paths.extend(manifest_paths(item))
    elif isinstance(value, dict):
        for item in value.values():
            paths.extend(manifest_paths(item))
    return paths


def validate_context_manifest(manifest: Any, report: DoctorReport) -> None:
    if not isinstance(manifest, dict):
        report.error("Context manifest must be a JSON object")
        return

    missing = REQUIRED_MANIFEST_KEYS - set(manifest)
    if missing:
        report.error(f"Context manifest missing required keys: {sorted(missing)}")
        return

    expected_objects = [
        "entrypoints",
        "entrypoint_sources",
        "indexes",
        "context_profiles",
        "adapter_prompts",
        "ignore_files",
        "task_playbooks",
        "checklists",
        "knowledge_packs",
    ]
    for key in expected_objects:
        value = manifest.get(key)
        if not isinstance(value, dict) or not value:
            report.error(f"Context manifest {key} must be a non-empty object")

    read_order = manifest.get("required_read_order")
    if not isinstance(read_order, list) or not read_order:
        report.error("Context manifest required_read_order must be a non-empty array")

    validation_commands = manifest.get("validation_commands")
    if not isinstance(validation_commands, list) or not validation_commands:
        report.error("Context manifest validation_commands must be a non-empty array")

    missing_paths = sorted({
        path
        for path in manifest_paths(manifest)
        if path.startswith((".", "A", "C", "L", "R", "m", "o"))
        and not Path(path).is_file()
    })
    for path in missing_paths:
        report.error(f"Context manifest references missing file: {path}")

    if not missing_paths and not any(
        message.startswith("Context manifest") for message in report.errors
    ):
        report.pass_check("Context manifest references existing files")


def validate_assistant_pointers(report: DoctorReport) -> None:
    drifted: list[str] = []
    missing: list[str] = []
    for file_path, expected in assistant_pointers().items():
        path = Path(file_path)
        if not path.is_file():
            missing.append(file_path)
            continue
        if path.read_text(encoding="utf-8") != expected:
            drifted.append(file_path)

    for file_path in missing:
        report.error(f"Missing assistant pointer file: {file_path}")
    banner = classification_banner()
    for file_path in drifted:
        if banner and banner not in Path(file_path).read_text(encoding="utf-8"):
            report.error(
                f"{file_path} is missing the configured classification banner "
                f"({banner!r}); run ./omni sync"
            )
        else:
            report.warning(f"Assistant pointer drift detected: {file_path}; run sync")

    if not missing and not drifted:
        report.pass_check("Assistant pointer files match expected routing text")


def validate_entrypoint_sources(report: DoctorReport) -> None:
    drifted: list[str] = []
    missing: list[str] = []
    for file_path, expected in ENTRYPOINT_SOURCES.items():
        path = Path(file_path)
        if not path.is_file():
            missing.append(file_path)
            continue
        if path.read_text(encoding="utf-8") != expected:
            drifted.append(file_path)

    for file_path in missing:
        report.error(f"Missing assistant entrypoint source: {file_path}; run sync")
    for file_path in drifted:
        report.warning(f"Assistant entrypoint source drift detected: {file_path}; run sync")

    if not missing and not drifted:
        report.pass_check("Assistant entrypoint sources match expected content")


def validate_synced_ignore_files(report: DoctorReport) -> None:
    drifted: list[str] = []
    missing: list[str] = []
    for file_path, prefix in SYNCED_IGNORE_FILES.items():
        path = Path(file_path)
        expected = rendered_ignore_file(prefix)
        if not path.is_file():
            missing.append(file_path)
            continue
        if path.read_text(encoding="utf-8") != expected:
            drifted.append(file_path)

    for file_path in missing:
        report.error(f"Missing synced ignore file: {file_path}; run sync")
    for file_path in drifted:
        report.warning(f"Synced ignore file drift detected: {file_path}; run sync")

    if not missing and not drifted:
        report.pass_check("Synced ignore files match .ai/.ignore")


def validate_workspace_placement(report: DoctorReport) -> None:
    misplaced: list[str] = []
    root_clutter: list[str] = []
    oversized_shims: list[str] = []

    configured_allowed = configured_allowed_root_paths()
    for item in Path(".").iterdir():
        name = item.name
        if name in configured_allowed or name in TOOL_STATE_ROOT_DIRS:
            continue
        if item.is_dir():
            if name in ROOT_CLUTTER_DIRS:
                root_clutter.append(name)
            elif name not in ALLOWED_ROOT_DIRS:
                misplaced.append(name + "/")
        elif item.is_file() and name not in ALLOWED_ROOT_FILES:
            misplaced.append(name)

    root_clutter = [name for name in root_clutter if not is_git_ignored(name)]
    misplaced = [name for name in misplaced if not is_git_ignored(name.rstrip("/"))]

    for file_path in LOCAL_ONLY_PUBLIC_PATHS:
        if Path(file_path).exists():
            misplaced.append(file_path)

    for file_path in ASSISTANT_POINTER_TARGETS:
        path = Path(file_path)
        if not path.is_file():
            continue
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > MAX_ASSISTANT_SHIM_LINES:
            oversized_shims.append(f"{file_path} ({line_count} lines)")

    for path in sorted(root_clutter):
        report.warning(f"Generated or cache directory should not remain at project root: {path}")
    for path in sorted(misplaced):
        report.warning(f"Review file placement; unexpected public workspace path: {path}")
    for path in sorted(oversized_shims):
        report.warning(f"Assistant shim is larger than expected; move full content to .ai/entrypoints: {path}")

    if not root_clutter and not misplaced and not oversized_shims:
        report.pass_check("Workspace file placement looks clean")


def validate_markdown_assets(report: DoctorReport) -> None:
    readme = Path("README.md")
    changelog = Path("CHANGELOG.md")
    svg = Path("assets/omni-context.svg")
    required_legal_files = ["LICENSE", "NOTICE", "TRADEMARKS.md"]

    if readme.is_file():
        readme_text = readme.read_text(encoding="utf-8")
        if "assets/omni-context.svg" in readme_text and svg.is_file():
            report.pass_check("README references the architecture SVG asset")
        elif "assets/omni-context.svg" in readme_text:
            report.error("README references missing SVG asset: assets/omni-context.svg")
        else:
            report.warning("README does not reference the architecture SVG asset")
    else:
        report.error("Missing README.md")

    if changelog.is_file():
        report.pass_check("CHANGELOG.md exists")
    else:
        report.warning("CHANGELOG.md is missing")

    missing_legal_files = [path for path in required_legal_files if not Path(path).is_file()]
    if missing_legal_files:
        for path in missing_legal_files:
            report.error(f"Missing required license file: {path}")
    else:
        report.pass_check("License, notice, and trademark policy exist")


def validate_project_map(report: DoctorReport) -> None:
    path = Path(PROJECT_MAP_DEFAULT_OUTPUT)
    if not path.is_file():
        report.error(f"Missing generated project map: {PROJECT_MAP_DEFAULT_OUTPUT}; run ./omni map")
        return

    text = path.read_text(encoding="utf-8")
    required_phrases = ["# Project Map", "Generated at:", "## Directory Tree"]
    missing_phrases = [phrase for phrase in required_phrases if phrase not in text]
    if missing_phrases:
        report.error(
            f"Generated project map is malformed: missing {', '.join(missing_phrases)}"
        )
    else:
        report.pass_check("Generated project map exists")


def validate_project_map_freshness(report: DoctorReport) -> None:
    path = Path(PROJECT_MAP_DEFAULT_OUTPUT)
    if not path.is_file():
        return  # already reported by validate_project_map

    text = path.read_text(encoding="utf-8")
    tree_start = text.find("## Directory Tree")
    tree_text = text[tree_start:] if tree_start != -1 else text

    missing_dirs = [
        name
        for name in sorted(ALLOWED_ROOT_DIRS)
        if name != ".ai"
        and name not in DEFAULT_MAP_EXCLUDED_DIRS
        and Path(name).is_dir()
        and f"{name}/" not in tree_text
    ]

    if missing_dirs:
        report.warning(
            "Project map is stale: top-level directories exist on disk but are "
            f"missing from the map's tree: {', '.join(missing_dirs)}. Run ./omni map "
            "to regenerate."
        )
    else:
        report.pass_check("Generated project map covers current top-level directories")


def validate_project_graph(report: DoctorReport) -> None:
    if omni_graph is None and Path("make_ai.py").is_file():
        report.warning(
            "omni_graph.py is missing next to make_ai.py, so `omni graph` is unavailable. "
            "Re-run `omni adopt --include-cli` or `omni update` from a current OmniEngineering "
            "source to restore it; every other command is unaffected."
        )

    path = Path(GRAPH_DEFAULT_OUTPUT)
    if not path.is_file():
        return  # optional artifact: omni graph build is opt-in and needs the [graph] extra

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.error(f"Generated project graph is not valid JSON: {exc}")
        return

    required_keys = {"version", "nodes", "edges", "provenance_legend"}
    missing_keys = required_keys - data.keys()
    if missing_keys:
        report.error(f"Generated project graph is missing keys: {', '.join(sorted(missing_keys))}")
        return

    report.pass_check("Generated project graph is present and well-formed")


def validate_recent_commits_tracked(report: DoctorReport) -> None:
    changelog_path = Path("CHANGELOG.md")
    if not Path(".git").exists() or not changelog_path.is_file():
        return

    try:
        last_changelog_date = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", str(changelog_path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return

    if not last_changelog_date:
        return

    try:
        head_log = subprocess.run(
            ["git", "log", "--format=%h\t%cI\t%s", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip().splitlines()
    except (OSError, subprocess.CalledProcessError):
        return

    # Compare by commit date rather than DAG reachability (e.g. `X..HEAD`):
    # squash-merge PR histories put already-integrated side-branch commits
    # "after" the changelog commit in the graph even though they landed
    # chronologically earlier, which would otherwise false-positive here.
    commits_since = []
    for line in head_log:
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        short_hash, commit_date, subject = parts
        if commit_date > last_changelog_date:
            commits_since.append(f"{short_hash} {subject}")

    if commits_since:
        preview = commits_since[:5]
        suffix = "" if len(commits_since) <= 5 else f" (+{len(commits_since) - 5} more)"
        report.warning(
            f"{len(commits_since)} commit(s) postdate the last CHANGELOG.md update with "
            f"no changelog entry of their own: {'; '.join(preview)}{suffix}. Confirm each maps "
            "to a requirement ID and update CHANGELOG.md / .ai/requirements/requirements.json "
            "per the completion workflow, or state explicitly why not."
        )
    else:
        report.pass_check("No commits postdating the last CHANGELOG.md update are missing changelog coverage")


def _commit_identity_problem(email: str, configured: str) -> str | None:
    """Why a commit's email is suspect (REQ-049): it is not the configured one, or its domain is a
    machine-local placeholder (`.local`, `localhost`) that no forge can attribute. None when it is fine."""
    normalised = email.strip().lower()
    domain = normalised.rsplit("@", 1)[-1] if "@" in normalised else normalised
    if domain == "localhost" or domain.endswith(".local"):
        return f"has the machine-local domain `{domain}`"
    if normalised != configured:
        return f"differs from git config user.email ({configured})"
    return None


def validate_commit_identity(report: DoctorReport) -> None:
    """Warn (never error) when a commit since the gate base, or one of the last 20 when there is no base,
    carries an author or committer email other than `git config user.email`, or one ending in `.local`
    or `localhost`: the identity an assistant or a fresh machine commits under is easy to get wrong and
    is only noticed after the push. A repository with no configured email skips the check (REQ-049)."""
    if not Path(".git").exists():
        return
    configured = (git_run("config", "user.email") or "").strip().lower()
    if not configured:
        return
    head = (git_run("rev-parse", "--verify", "-q", "HEAD") or "").strip()
    if not head:
        return
    base = gate_base_commit()
    log_format = "--format=%h%x09%ae%x09%ce"
    if base and base != head:
        log = git_run("log", "--no-merges", log_format, f"{base}..HEAD")
        span = "since the gate base"
    else:
        log = git_run("log", "--no-merges", "-20", log_format, "HEAD")
        span = "among the last 20"
    commits = [line.split("\t") for line in (log or "").splitlines() if line.count("\t") == 2]
    if not commits:
        return
    problems: list[str] = []
    for index, (short, author, committer) in enumerate(commits):
        for role, email in (("author", author), ("committer", committer)):
            problem = _commit_identity_problem(email, configured)
            if problem is None:
                continue
            fix = (
                "git commit --amend --reset-author" if index == 0
                else f"git rebase -i {base[:12] if base and base != head else 'HEAD~' + str(len(commits))} and --reset-author it"
            )
            problems.append(f"{short} {role} {email} {problem}; fix: {fix}")
    if problems:
        preview = "; ".join(problems[:5])
        suffix = "" if len(problems) <= 5 else f" (+{len(problems) - 5} more)"
        report.warning(
            f"Commit identity: {len(problems)} email(s) on commits {span} look wrong: {preview}{suffix}. "
            "Set `git config user.email` to the address the forge knows and rewrite the commits before pushing."
        )
    else:
        report.pass_check(f"{len(commits)} commit(s) {span} carry the configured identity ({configured})")


def validate_cli_entrypoints(report: DoctorReport) -> None:
    omni_path = Path("omni")
    if not omni_path.is_file():
        report.error("Missing repo-local omni command shim")
    else:
        omni_text = omni_path.read_text(encoding="utf-8")
        if "from make_ai import main" not in omni_text:
            report.error("Repo-local omni command does not delegate to make_ai.main")
        else:
            report.pass_check("Repo-local omni command shim exists")

    pyproject_path = Path("pyproject.toml")
    if not pyproject_path.is_file():
        report.warning("pyproject.toml is absent; installable omni console script is optional")
        return

    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    if not re.search(r'(?m)^omni\s*=\s*"make_ai:main"\s*$', pyproject_text):
        report.warning("pyproject.toml does not define optional project.scripts.omni = make_ai:main")
    else:
        report.pass_check("Installable omni console script is configured")


def validate_vendored_workspaces(report: DoctorReport) -> None:
    """A vendored workspace (a subtree with its own `.ai/omni-version.json`) carries copies of the CLI.
    When those copies differ from this checkout's, the two tools are running different code in one
    repository and nobody can tell which is authoritative; the fix is the mechanism adoption already
    has, `omni update`, run from inside the vendored directory against this root."""
    for workspace in vendored_workspace_dirs():
        rel = workspace.relative_to(Path(".").resolve()).as_posix()
        drifted = [
            name for name in ADOPTION_CLI_FILES
            if (workspace / name).is_file() and Path(name).is_file()
            and (workspace / name).read_bytes() != Path(name).read_bytes()
        ]
        if drifted:
            report.warning(
                f"Vendored workspace {rel}/ carries tooling that differs from this checkout's "
                f"({', '.join(drifted[:3])}{' ...' if len(drifted) > 3 else ''}); "
                f"run `cd {rel} && python omni update --source ..` to bring it level"
            )
        else:
            report.pass_check(f"Vendored workspace {rel}/ runs the same tooling as this checkout")


def validate_omni_version_present(report: DoctorReport) -> None:
    if read_omni_version_file() is None:
        report.warning(
            f"{OMNI_VERSION_FILE} is missing; `omni update` cannot detect drift against "
            "the OmniEngineering template without it. Run `omni adopt` (writes it "
            "automatically) or create it by hand, recording the source path and git ref "
            "this workspace was last synced to."
        )
    else:
        report.pass_check(f"{OMNI_VERSION_FILE} is present")


def probe_mcp_server(
    command: str,
    args: list[str],
    env: dict[str, str] | None,
    cwd: Path,
    timeout: float = MCP_PROBE_TIMEOUT_SECONDS,
) -> tuple[list[str] | None, str]:
    """Launch a stdio MCP server the way a client would and ask it for its tools.

    Returns (tool names, server name) on success and (None, reason) on failure. The handshake is the real one
    (initialize, notifications/initialized, tools/list over newline-delimited JSON-RPC 2.0), not a grep of the
    registration file: a server whose SDK, entry point or import path has moved fails here, where a static check
    would have passed. Stdin is only closed after the answers arrive, because SDK-based servers may treat EOF as a
    shutdown and drop requests still in flight.
    """
    executable = shutil.which(command) or (command if Path(command).is_file() else None)
    if executable is None:
        return None, f"command not found on PATH: {command}"
    try:
        process = subprocess.Popen(
            [executable, *args],
            cwd=str(cwd),
            env={**os.environ, **(env or {})},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
    except OSError as exc:
        return None, f"could not start {executable}: {exc}"
    assert process.stdin is not None and process.stdout is not None

    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    deadline = time.monotonic() + timeout

    def send(message: dict[str, Any]) -> None:
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def wait_for(request_id: int) -> dict[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError from exc
            if line is None:
                raise EOFError
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # a log line on stdout; not ours to judge
            if isinstance(message, dict) and message.get("id") == request_id:
                return message

    try:
        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "omni doctor", "version": "1"}},
        })
        initialized = wait_for(1)
        if "error" in initialized:
            return None, f"initialize was rejected: {initialized['error'].get('message', initialized['error'])}"
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = wait_for(2)
        if "error" in listed:
            return None, f"tools/list was rejected: {listed['error'].get('message', listed['error'])}"
        tools = listed.get("result", {}).get("tools", []) if isinstance(listed.get("result"), dict) else []
        names = [str(tool.get("name")) for tool in tools if isinstance(tool, dict) and tool.get("name")]
        server_info = initialized.get("result", {}).get("serverInfo", {}) if isinstance(initialized.get("result"), dict) else {}
        return names, str(server_info.get("name", "unnamed server"))
    except TimeoutError:
        return None, f"no answer within {timeout:.0f}s"
    except (EOFError, BrokenPipeError, OSError):
        return None, "exited before answering initialize and tools/list"
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()


def mcp_probe_memo_file() -> Path | None:
    """Where the live-probe memo lives: inside the git directory beside `omni-gate-last.json`, which git never
    tracks; None outside a git checkout, where every probe is live."""
    git_dir = git_run("rev-parse", "--git-dir")
    return Path(git_dir.strip()) / MCP_PROBE_MEMO_FILE if git_dir else None


def mcp_probe_memo_disabled() -> bool:
    """`OMNI_DOCTOR_NO_MEMO=1` (CI may set it) turns the memo off: every probe is live and nothing is stored."""
    return os.environ.get(MCP_PROBE_NO_MEMO_ENV, "").strip().lower() not in ("", "0", "false", "no")


def mcp_executable_fingerprint(command: str) -> dict[str, Any] | None:
    """What the memo keys a server on: the resolved executable's path, mtime and size, read through any
    symlink so a retargeted `python` or a reinstalled console script invalidates it. None when the command
    does not resolve, which the live probe then reports."""
    executable = shutil.which(command) or (command if Path(command).is_file() else None)
    if executable is None:
        return None
    try:
        resolved = Path(executable).resolve()
        stat = resolved.stat()
    except OSError:
        return None
    return {"path": str(resolved), "mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


def mcp_probe_memo_key(registration_text: str) -> dict[str, str]:
    """The part of the key shared by every server: the digest of `.mcp.json` as written and PATH as seen."""
    return {
        "registration_digest": hashlib.sha256(registration_text.encode("utf-8")).hexdigest(),
        "path": os.environ.get("PATH", ""),
    }


def load_mcp_probe_memo(memo_file: Path | None, key: dict[str, str]) -> dict[str, dict[str, Any]]:
    """The remembered servers by name, or {} when the memo is missing, unreadable, or was written for another
    registration or PATH."""
    if memo_file is None:
        return {}
    try:
        memo = json.loads(memo_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(memo, dict) or any(memo.get(field) != value for field, value in key.items()):
        return {}
    servers = memo.get("servers")
    return {name: record for name, record in servers.items() if isinstance(record, dict)} if isinstance(servers, dict) else {}


def mcp_probe_memo_hit(record: dict[str, Any] | None, fingerprint: dict[str, Any] | None, now: float) -> str | None:
    """The ISO time of the remembered probe when `record` still applies: the executable is the same file
    (path, mtime, size), the probe is younger than a day and it listed tools. Otherwise None, and the server
    is launched for real."""
    if not record or fingerprint is None or record.get("executable") != fingerprint:
        return None
    probed_at = record.get("probed_at")
    stamp = _arbiter_timestamp(probed_at)
    if stamp is None or not 0 <= now - stamp < MCP_PROBE_MEMO_MAX_AGE_SECONDS:
        return None
    tools = record.get("tools")
    if not isinstance(tools, list) or not tools or not isinstance(record.get("server_name"), str):
        return None
    return str(probed_at)


def store_mcp_probe_memo(memo_file: Path | None, key: dict[str, str], servers: dict[str, dict[str, Any]]) -> None:
    """Write the memo: the shared key plus one record per server that answered. A write that fails is not a
    doctor finding; the next run simply probes live again."""
    if memo_file is None:
        return
    try:
        write_json(memo_file, {"schema_version": 1, **key, "servers": servers})
    except OSError:
        pass


def validate_mcp_registrations(report: DoctorReport, force_probe: bool = False) -> None:
    """`.mcp.json` tells assistants which MCP servers to start for this repo. A registration that looks right
    but cannot start is worse than none, because the assistant silently falls back to shelling out, so every
    stdio server listed is launched for real and must answer with at least one tool. Remote (`url`) servers
    are not probed: reaching them is a network question, not a workspace one.

    A successful probe is memoised (REQ-050): while `.mcp.json`, PATH and the server's resolved executable
    (path, mtime, size) are unchanged and the probe is younger than a day, the next run reads the memo instead
    of launching the server, and its line says when it was probed. `force_probe` (`omni doctor --probe`)
    launches every server and refreshes the memo; a failed probe is never remembered; `OMNI_DOCTOR_NO_MEMO=1`
    turns the memo off. `omni gate` runs the doctor with the memo, so a commit no longer starts the servers."""
    if not MCP_REGISTRATION_PATH.is_file():
        return
    try:
        registration_text = MCP_REGISTRATION_PATH.read_text(encoding="utf-8")
        registration = json.loads(registration_text)
    except OSError as exc:
        report.error(f"Cannot read {MCP_REGISTRATION_PATH}: {exc}")
        return
    except json.JSONDecodeError as exc:
        report.error(f"Invalid JSON in {MCP_REGISTRATION_PATH}: line {exc.lineno}, column {exc.colno}")
        return
    servers = registration.get("mcpServers") if isinstance(registration, dict) else None
    if not isinstance(servers, dict) or not servers:
        report.error(f"{MCP_REGISTRATION_PATH} must contain a non-empty mcpServers object")
        return

    memo_file = None if mcp_probe_memo_disabled() else mcp_probe_memo_file()
    memo_key = mcp_probe_memo_key(registration_text)
    memo = {} if force_probe else load_mcp_probe_memo(memo_file, memo_key)
    remembered: dict[str, dict[str, Any]] = {}
    now = time.time()

    for name, config in servers.items():
        if not isinstance(config, dict):
            report.error(f"MCP server {name!r} in {MCP_REGISTRATION_PATH} must be an object")
            continue
        if config.get("url") and not config.get("command"):
            report.pass_check(f"MCP server {name!r} is remote ({config['url']}); not probed")
            continue
        command = config.get("command")
        args = config.get("args", [])
        env = config.get("env", {})
        if not isinstance(command, str) or not command.strip():
            report.error(f"MCP server {name!r} has no command")
            continue
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            report.error(f"MCP server {name!r} args must be an array of strings")
            continue
        if not isinstance(env, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in env.items()):
            report.error(f"MCP server {name!r} env must be an object of string values")
            continue
        fingerprint = mcp_executable_fingerprint(command)
        record = memo.get(name)
        probed_at = mcp_probe_memo_hit(record, fingerprint, now)
        if probed_at is not None and record is not None:
            remembered[name] = record
            report.pass_check(
                f"MCP server {name!r} ({record['server_name']}) answers initialize and lists "
                f"{len(record['tools'])} tool(s) (probed {probed_at}, run omni doctor --probe to re-check)"
            )
            continue
        tools, detail = probe_mcp_server(command, args, env, Path.cwd())
        rendered = " ".join([command, *args])
        if tools is None:
            report.error(f"MCP server {name!r} (`{rendered}`) failed its live check: {detail}")
        elif not tools:
            report.error(f"MCP server {name!r} (`{rendered}`) started but lists no tools")
        else:
            report.pass_check(f"MCP server {name!r} ({detail}) answers initialize and lists {len(tools)} tool(s)")
            if fingerprint is not None:
                remembered[name] = {
                    "executable": fingerprint,
                    "probed_at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
                    "server_name": detail,
                    "tools": list(tools),
                }
    store_mcp_probe_memo(memo_file, memo_key, remembered)


def find_placeholders(value: Any) -> set[str]:
    placeholders: set[str] = set()
    if isinstance(value, str):
        placeholders.update(PLACEHOLDER_PATTERN.findall(value))
    elif isinstance(value, list):
        for item in value:
            placeholders.update(find_placeholders(item))
    elif isinstance(value, dict):
        for item in value.values():
            placeholders.update(find_placeholders(item))
    return placeholders


def is_generated_file(path: Path) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    return GENERATED_FILE_MARKER in text or text.startswith("# Generated by omni sync")


def write_generated_file(path: Path, content: str, force: bool) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        print(f"Unchanged: {path}")
        return True
    if path.exists() and not force and not is_generated_file(path):
        print(f"Skipped existing non-Omni file: {path} (merge manually or rerun with --force)")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"Updated: {path}")
    return True


def write_assistant_pointers(force: bool) -> list[str]:
    skipped: list[str] = []
    for file_path, content in assistant_pointers().items():
        path = Path(file_path)
        if not write_generated_file(path, content, force):
            skipped.append(file_path)
    return skipped


def write_entrypoint_sources(force: bool) -> list[str]:
    skipped: list[str] = []
    for file_path, content in ENTRYPOINT_SOURCES.items():
        path = Path(file_path)
        if not write_generated_file(path, content, force):
            skipped.append(file_path)
    return skipped


def rendered_ignore_file(prefix: str) -> str:
    source = Path(".ai/.ignore")
    body = source.read_text(encoding="utf-8") if source.is_file() else ""
    return prefix + body.rstrip() + "\n"


def write_synced_ignore_files(force: bool) -> list[str]:
    skipped: list[str] = []
    for file_path, prefix in SYNCED_IGNORE_FILES.items():
        path = Path(file_path)
        if not write_generated_file(path, rendered_ignore_file(prefix), force):
            skipped.append(file_path)
    return skipped


def collect_map_entries(
    root: Path,
    current: Path,
    depth: int,
    max_depth: int,
    max_entries: int,
    ignore_patterns: list[str],
    include_ai: bool,
    entries: list[tuple[Path, bool, int]],
) -> bool:
    if len(entries) >= max_entries:
        return True
    if depth >= max_depth:
        return False

    try:
        children = sorted(
            current.iterdir(),
            key=lambda item: (not item.is_dir(), item.name.lower()),
        )
    except OSError:
        return False

    for child in children:
        relative = child.relative_to(root)
        is_dir = child.is_dir()
        if should_skip_map_path(relative, is_dir, ignore_patterns, include_ai):
            continue
        entries.append((relative, is_dir, depth + 1))
        if len(entries) >= max_entries:
            return True
        if is_dir:
            truncated = collect_map_entries(
                root,
                child,
                depth + 1,
                max_depth,
                max_entries,
                ignore_patterns,
                include_ai,
                entries,
            )
            if truncated:
                return True
    return False


def render_project_map(args: argparse.Namespace) -> str:
    root = Path(args.root).resolve()
    output = Path(args.output)
    ignore_patterns = read_ignore_patterns()
    entries: list[tuple[Path, bool, int]] = []
    truncated = collect_map_entries(
        root,
        root,
        0,
        args.max_depth,
        args.max_entries,
        ignore_patterns,
        args.include_ai,
        entries,
    )

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    lines = [
        "# Project Map",
        "",
        "This file is generated by `omni map` for LLM navigation. It maps the",
        "adopter project's repository structure so an assistant can choose where",
        "to inspect next without reading the whole repository.",
        "",
        f"- Generated at: `{generated_at}`",
        f"- Root: `{root.name or root}`",
        f"- Max depth: `{args.max_depth}`",
        f"- Max entries: `{args.max_entries}`",
        f"- Includes `.ai/`: `{str(args.include_ai).lower()}`",
        "- File contents: not included",
        "- Sensitive paths: filtered using `.ai/.ignore`, `.gitignore`, and",
        "  default dependency, build, cache, VCS, and local-session exclusions",
        "",
        "## How Assistants Should Use This",
        "",
        "1. Read this map before broad repository traversal.",
        "2. Select the smallest relevant path set for the active requirement.",
        "3. Inspect only the selected files or directories.",
        "4. Regenerate this map with `./omni map` after large structure changes.",
        "",
        "## Key Workspace Files",
        "",
    ]

    key_files = [
        "README.md",
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "Makefile",
        "Dockerfile",
        "compose.yml",
        "docker-compose.yml",
        "CHANGELOG.md",
    ]
    found_key_files = [file_path for file_path in key_files if (root / file_path).exists()]
    if found_key_files:
        lines.extend(f"- `{file_path}`" for file_path in found_key_files)
    else:
        lines.append("- No common root project files detected.")

    lines.extend(["", "## Directory Tree", "", "```text", "./"])
    for relative, is_dir, depth in entries:
        indent = "  " * (depth - 1)
        suffix = "/" if is_dir else ""
        lines.append(f"{indent}{relative.name}{suffix}")
    if truncated:
        lines.append(f"... truncated at {args.max_entries} entries")
    lines.extend(["```", ""])
    return "\n".join(lines)


def run_map(args: argparse.Namespace) -> int:
    root = Path(args.root)
    if not root.is_dir():
        print(f"Project root not found: {root}", file=sys.stderr)
        return 1

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_project_map(args), encoding="utf-8")
    print(f"Updated: {output}")
    return 0


def require_omni_graph() -> bool:
    if omni_graph is not None:
        return True
    print(
        "omni_graph.py is missing next to make_ai.py, so `omni graph` is unavailable. "
        "Re-run `omni adopt --include-cli` or `omni update` from a current OmniEngineering "
        "source to restore it.",
        file=sys.stderr,
    )
    return False


def graph_python_candidates() -> list[str]:
    candidates = []
    explicit = os.environ.get("OMNI_GRAPH_PYTHON")
    if explicit:
        candidates.append(explicit)
    candidates.append(str(Path.home() / ".venvs" / "omni-graph" / "bin" / "python"))
    return candidates


def reexec_with_graph_python() -> None:
    """Re-run this command under a venv interpreter that has tree-sitter, if one exists."""
    if os.environ.get("OMNI_GRAPH_REEXEC"):
        return
    probe = "import tree_sitter, tree_sitter_python, tree_sitter_javascript, tree_sitter_typescript"
    for candidate in graph_python_candidates():
        if not Path(candidate).is_file() or os.path.abspath(candidate) == os.path.abspath(sys.executable):
            continue
        try:
            ok = subprocess.run([candidate, "-c", probe], capture_output=True, timeout=30).returncode == 0
        except (OSError, subprocess.SubprocessError):
            continue
        if ok:
            print(f"tree-sitter is not installed for {sys.executable}; re-running with {candidate}", file=sys.stderr)
            os.environ["OMNI_GRAPH_REEXEC"] = "1"
            os.execv(candidate, [candidate, *sys.argv])


GRAPH_INSTALL_HELP = """
Install tree-sitter into a virtualenv (system-wide pip is refused on PEP 668 "externally managed" Pythons):

  python3 -m venv ~/.venvs/omni-graph
  ~/.venvs/omni-graph/bin/pip install "tree-sitter>=0.23,<1.0" "tree-sitter-python>=0.23,<1.0" \\
    "tree-sitter-javascript>=0.23,<1.0" "tree-sitter-typescript>=0.23,<1.0"

omni then finds ~/.venvs/omni-graph on its own (or set OMNI_GRAPH_PYTHON to another interpreter).
Add a grammar for each further language you use (Java, Go, Rust, C#, C/C++, Ruby, PHP, Kotlin, Swift, Scala, ...):
  ~/.venvs/omni-graph/bin/pip install tree-sitter-java tree-sitter-go tree-sitter-rust   # etc.
Languages without a grammar still appear as file nodes; SQL migrations need no grammar.
Elsewhere, `pip install "omniengineering-workspace[graph]"` also works."""


def run_graph_build(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1

    root = Path(args.root)
    if not root.is_dir():
        print(f"Project root not found: {root}", file=sys.stderr)
        return 1

    languages = split_csv(args.languages)
    known_languages = omni_graph.language_extensions()
    unknown = [language for language in languages if language not in known_languages and language not in ("auto", "all")]
    if unknown:
        print(
            f"Unsupported language(s): {', '.join(unknown)}. "
            f"Supported: auto, {', '.join(sorted(known_languages))}",
            file=sys.stderr,
        )
        return 1

    layers = set(split_csv(args.layers)) if args.layers else set(omni_graph.LAYERS)
    bad_layers = layers - set(omni_graph.LAYERS)
    if bad_layers:
        print(f"Unknown layer(s): {', '.join(sorted(bad_layers))}. Layers: {', '.join(omni_graph.LAYERS)}", file=sys.stderr)
        return 1

    try:
        graph, semantic_info = omni_graph.build_graph(
            root.resolve(), languages, semantic=args.semantic, layers=layers, max_commits=args.max_commits
        )
    except omni_graph.GraphDependencyError as exc:
        reexec_with_graph_python()
        print(str(exc) + GRAPH_INSTALL_HELP, file=sys.stderr)
        return 1

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, graph.to_json(str(root), languages, semantic_info))

    extracted = sum(1 for edge in graph.edges if edge.provenance == omni_graph.EXTRACTED)
    inferred = sum(1 for edge in graph.edges if edge.provenance == omni_graph.INFERRED)
    summary = {
        "output": str(output),
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "extracted_edges": extracted,
        "inferred_edges": inferred,
        "languages": graph.stats,
        "layers": graph.layer_stats,
        "notes": graph.notes,
        "semantic_pass": semantic_info,
    }
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"Wrote {summary['nodes']} nodes and {summary['edges']} edges to {output}")
    print(f"  EXTRACTED edges: {extracted}   INFERRED edges: {inferred}")
    for language, stats in sorted(graph.stats.items()):
        detail = f"{stats.get('tables', 0)} tables" if stats.get("mode") == "schema" else f"{stats.get('symbols', 0)} symbols"
        print(f"  {language:<11} {stats['files']:>5} files  {detail:>12}  [{stats['mode']}]")
    stats_by_layer = graph.layer_stats
    if omni_graph.LAYER_GOVERNANCE in stats_by_layer:
        g = stats_by_layer[omni_graph.LAYER_GOVERNANCE]
        print(f"  governance  {g['requirements']} requirements, {g['changelog_entries']} changelog entries, {g['touches']} touches edges")
    if omni_graph.LAYER_HISTORY in stats_by_layer:
        h = stats_by_layer[omni_graph.LAYER_HISTORY]
        span = f", {h['first_date']} to {h['last_date']}" if h.get("first_date") else ""
        print(
            f"  history     {h['commits']} commits{span}: {h['with_requirements']} tied to requirements, "
            f"{h['logged_in']} changelog links, {h['modifies']} file links"
        )
    if omni_graph.LAYER_ASSURANCE in stats_by_layer:
        a = stats_by_layer[omni_graph.LAYER_ASSURANCE]
        print(
            f"  assurance   {a['suites']} test suites ({a['suites_manual']} registered, {a['suites_auto']} auto-detected), "
            f"{a['test_files']} test files, {a['verifies']} verifies edges, {a['failures']} ledger failures "
            f"({a['guards']} regression-test links, {a['unresolved']} unresolved refs)"
        )
    if omni_graph.LAYER_WORKSPACE in stats_by_layer:
        w = stats_by_layer[omni_graph.LAYER_WORKSPACE]
        print(
            f"  workspace   {w['rulepacks']} rulepacks, {w['rules']} rules, {w['playbooks']} playbooks, {w['checklists']} checklists"
            + (f", {w['tooling_nodes']} nodes of OmniEngineering's own code" if w.get("tooling_nodes") else "")
        )
    for note in graph.notes:
        print(f"  note: {note}")
    if semantic_info.get("enabled"):
        print(
            f"  Semantic pass: {semantic_info.get('nodes_tagged', 0)} nodes tagged, "
            f"{semantic_info.get('edges_added', 0)} related_to edges added via {semantic_info.get('api_url')}"
        )
        for error in semantic_info.get("errors", []):
            print(f"    semantic pass error: {error}", file=sys.stderr)
    else:
        print(f"  Semantic pass: skipped ({semantic_info.get('reason')})")
    return 0


def run_graph_trace(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1

    graph_path = Path(args.graph)
    if not graph_path.is_file():
        print(f"Graph file not found: {graph_path}; run ./omni graph build first", file=sys.stderr)
        return 1

    result = omni_graph.trace(graph_path, args.source, args.target)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if not result.get("ok"):
        if result.get("error") == "ambiguous_or_not_found":
            print("Could not uniquely resolve source/target.", file=sys.stderr)
            print(f"  source matches: {result.get('source_matches')}", file=sys.stderr)
            print(f"  target matches: {result.get('target_matches')}", file=sys.stderr)
        else:
            print(f"No path found between {result.get('source')} and {result.get('target')}", file=sys.stderr)
        return 1

    current = result["source"]
    print(current)
    for hop in result["hops"]:
        if hop["source"] == current:
            next_node = hop["target"]
            arrow = f"  --[{hop['type']}, {hop['provenance']}]--> "
        else:
            next_node = hop["source"]
            arrow = f"  <--[{hop['type']}, {hop['provenance']}]-- "
        print(f"{arrow}{next_node}")
        current = next_node
    return 0


def run_graph_show(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1

    graph_path = Path(args.graph)
    if not graph_path.is_file():
        print(f"Graph file not found: {graph_path}; run ./omni graph build first", file=sys.stderr)
        return 1

    if args.all:
        return run_graph_show_all(args, graph_path)
    if not args.node:
        print("Provide a node to inspect, or use --all to list every node.", file=sys.stderr)
        return 1

    result = omni_graph.show(graph_path, args.node)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if not result.get("ok"):
        print(f"Could not uniquely resolve node. Matches: {result.get('matches')}", file=sys.stderr)
        return 1

    node = result["node"]
    print(f"{node['id']} ({node['kind']})")
    if node.get("summary"):
        print(f"  summary: {node['summary']}")
    print("  outgoing:")
    for edge in result["outgoing"]:
        print(f"    --[{edge['type']}, {edge['provenance']}]--> {edge['target']}  ({edge['detail']})")
    print("  incoming:")
    for edge in result["incoming"]:
        print(f"    <--[{edge['type']}, {edge['provenance']}]-- {edge['source']}  ({edge['detail']})")
    return 0


def run_graph_show_all(args: argparse.Namespace, graph_path: Path) -> int:
    result = omni_graph.list_nodes(
        graph_path,
        kind=args.kind,
        language=args.language,
        file_pattern=args.file,
        include_external=args.include_external,
        include_edges=args.edges,
        sort=args.sort,
        limit=args.limit,
        layer=args.layer,
    )
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    nodes = result["nodes"]
    print(
        f"Graph: {result['nodes_total']} nodes, {result['edges_total']} edges"
        + (f" (root {result['root']}, built {str(result['generated_at'])[:10]})" if result.get("root") else "")
    )
    print("  layers: " + ", ".join(f"{name} {count}" for name, count in result["layers"].items()))
    print("  node kinds: " + ", ".join(f"{kind} {count}" for kind, count in result["node_kinds"].items()))
    print("  edge types: " + ", ".join(f"{kind} {count}" for kind, count in result["edge_types"].items()))
    shown = f"{len(nodes)} of {result['nodes_matched']} matching nodes"
    if result["externals_hidden"]:
        shown += f"; {result['externals_hidden']} external placeholders hidden (use --include-external)"
    print(f"  listing {shown}")
    print("")

    def describe(row: dict[str, Any]) -> str:
        span = f"L{row['start_line']}-{row['end_line']}" if row.get("start_line") else ""
        return f"in {row['in']:<3} out {row['out']:<3} {span}"

    if args.sort == "file":
        current_file = object()
        for row in nodes:
            if row["file"] != current_file:
                current_file = row["file"]
                print(f"{current_file or '(external)'}  [{row.get('language') or '-'}]")
            print(f"  {row['kind']:<9} {row['name']:<44} {describe(row)}")
    else:
        for row in nodes:
            print(f"{row['kind']:<9} {describe(row):<26} {row['id']}")

    if args.edges:
        print("")
        print(f"Edges among the listed nodes ({len(result['edges'])}):")
        for edge in result["edges"]:
            print(f"  {edge['source']} --[{edge['type']}, {edge['provenance']}]--> {edge['target']}")
    return 0


def windows_view_target(output: Path) -> tuple[str, str] | None:
    """On WSL, the Windows path and file:// URI a Windows browser can actually open."""
    try:
        windows_path = subprocess.run(
            ["wslpath", "-w", str(output.resolve())], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not windows_path:
        return None
    from urllib.parse import quote

    slashed = windows_path.replace("\\", "/")
    uri = ("file:" + quote(slashed, safe="/:")) if windows_path.startswith("\\\\") else ("file:///" + quote(slashed, safe="/:"))
    return windows_path, uri


def open_in_browser(uri: str, windows_path: str | None) -> bool:
    if windows_path:
        for command in (["cmd.exe", "/c", "start", "", windows_path], ["explorer.exe", windows_path]):
            if shutil.which(command[0]):
                try:
                    subprocess.run(command, cwd="/mnt/c", capture_output=True, timeout=15)
                    return True
                except (OSError, subprocess.SubprocessError):
                    continue
        return False
    import webbrowser

    return bool(webbrowser.open(uri))


def run_graph_view(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1

    graph_path = Path(args.graph)
    if not graph_path.is_file():
        print(f"Graph file not found: {graph_path}; run ./omni graph build first", file=sys.stderr)
        return 1

    result = omni_graph.build_view_html(
        graph_path,
        include_external=args.include_external,
        max_initial=args.max_initial,
        start_all=args.all,
        focus=args.focus,
        depth=args.depth,
        mode=args.mode,
    )
    if not result.get("ok"):
        if result.get("error") == "missing_assets":
            print(
                "The 3D viewer assets are missing: "
                + ", ".join(f".ai/graph-viewer/{name}" for name in result["missing"])
                + ". Run `./omni update --source <OmniEngineering checkout>` to restore them.",
                file=sys.stderr,
            )
        else:
            print(f"Could not resolve --focus symbol: {args.focus}", file=sys.stderr)
        return 1

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result["html"], encoding="utf-8")
    size_mb = output.stat().st_size / (1024 * 1024)
    print(
        f"Wrote {output} ({size_mb:.1f} MB): {result['nodes_initial']} of {result['nodes_total']} nodes "
        f"in the initial view, {result['edges_total']} edges available."
    )
    if result["note"]:
        print(f"  {result['note']}")
    posix_uri = output.resolve().as_uri()
    windows_target = windows_view_target(output)
    if windows_target:
        windows_path, uri = windows_target
        print(f"Open in your Windows browser (works offline): {uri}")
        print(f"  or paste this path into the address bar: {windows_path}")
    else:
        uri = posix_uri
        print(f"Open in a browser (works offline): {uri}")
    if args.open and not open_in_browser(uri, windows_target[0] if windows_target else None):
        print("  (no browser could be launched automatically; open the file by hand)")
    return 0


def run_graph_schema(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1
    graph_path = Path(args.graph)
    if not graph_path.is_file():
        print(f"Graph file not found: {graph_path}; run ./omni graph build first", file=sys.stderr)
        return 1
    report = omni_graph.schema_report(graph_path, args.table)
    if not report.get("ok"):
        if report.get("error") == "no_tables":
            print("No tables in this graph. Rebuild after adding SQL migrations (*.sql): `./omni graph build`.", file=sys.stderr)
        else:
            print(f"Unknown table {args.table!r}. Tables: {', '.join(report['tables'])}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(report, indent=2))
        return 0
    if args.format == "mermaid":
        print(omni_graph.schema_mermaid(report))
        return 0
    print(f"{len(report['tables'])} of {report['table_count']} tables (rebuilt from SQL migrations; DDL facts are EXTRACTED)")
    for table in report["tables"]:
        entity = f"   <- entity: {', '.join(table['entities'])}" if table["entities"] else ""
        print(f"\nTABLE {table['name']}  ({len(table['columns'])} columns){entity}")
        foreign = {column: fk for fk in table["foreign_keys"] for column in fk["columns"]}
        for column in table["columns"]:
            flags = []
            if column.get("pk"):
                flags.append("PK")
            if not column.get("nullable", True):
                flags.append("NOT NULL")
            if column.get("unique"):
                flags.append("UNIQUE")
            if column["name"] in foreign:
                fk = foreign[column["name"]]
                flags.append(f"FK -> {fk['table']}({', '.join(fk['ref_columns']) or '?'})")
            print(f"  {column['name']:<32} {column['type']:<28} {' '.join(flags)}")
        for index in table["indexes"]:
            print(f"  index {index['name']}{' UNIQUE' if index['unique'] else ''} ({', '.join(index['columns'])})")
        if table["referenced_by"]:
            print("  referenced by: " + ", ".join(sorted({ref["table"] for ref in table["referenced_by"]})))
    return 0


def run_graph_benchmark(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1
    graph_path = Path(args.graph)
    if not graph_path.is_file():
        print(f"Graph file not found: {graph_path}; run ./omni graph build first", file=sys.stderr)
        return 1
    result = omni_graph.benchmark(graph_path, Path(args.root))
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    if not result["cases"]:
        print("Nothing to benchmark: this graph has no requirement, commit, failure or file node to pick from.", file=sys.stderr)
        return 1
    print("A targeted graph query vs. the naive alternative (grep for the name, read every match whole; a")
    print("commit compares against `git show` instead). Both are run for real against this project right now.")
    print("Tokens are chars / 4: a rough estimate, not a real tokenizer.\n")
    for case in result["cases"]:
        print(f"{case['kind']}: {case['node']}")
        print(f"  graph query   {case['graph_chars']:>8,} chars  (~{case['graph_tokens_est']:,} tokens)")
        capped = "  [capped]" if case["naive_files_capped"] else ""
        print(f"  naive ({case['naive_method']}, {case['naive_files_read']} file(s){capped})")
        print(f"                {case['naive_chars']:>8,} chars  (~{case['naive_tokens_est']:,} tokens)")
        if case["ratio"]:
            print(f"  -> naive costs {case['ratio']}x the targeted query")
        print()
    print(result["note"])
    return 0


def run_graph_sources(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1
    root = Path(args.root).resolve()
    report = omni_graph.describe_sources(root)
    if args.write:
        target = root / omni_graph.GRAPH_CONFIG_PATH
        if target.exists() and not args.force:
            print(f"{omni_graph.GRAPH_CONFIG_PATH} already exists; pass --force to overwrite it.", file=sys.stderr)
            return 1
        draft = omni_graph.draft_graph_config(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json(target, {"_comment": "Only keys that differ from the defaults are needed. Keys: " + ", ".join(sorted(omni_graph._DEFAULT_GRAPH_CONFIG)), **draft})
        print(f"Wrote {omni_graph.GRAPH_CONFIG_PATH} with {len(draft)} setting(s) that differ from the defaults.")
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    layers = report["layers"]
    print(f"Graph sources for {root}")
    print(f"  config: {report['config_file']} ({'present' if report['config_present'] else 'not present; defaults apply'})")
    for problem in report["problems"]:
        print(f"    ! {problem}")
    g = layers["governance"]
    print("  governance")
    for r in g["requirements"]:
        state = f"{r['requirements']} requirement(s)" if r["exists"] else "not found"
        print(f"    requirements  {r['path']}: {state}")
        for problem in r["problems"]:
            print(f"      ! {problem}")
    for c in g["changelog"]:
        print(f"    changelog     {c['path']}: {c['entries']} entr{'y' if c['entries'] == 1 else 'ies'} ({c['dated']} dated, {c['versioned']} versioned)")
    if not g["changelog"]:
        print("    changelog     none found")
    h = layers["history"]
    print(f"  history       {'git repository, ' + str(h['commits']) + ' commit(s)' if h['git'] else 'not a git repository'} (scans up to {h['max_commits']})")
    a = layers["assurance"]
    print(f"  assurance     {len(a['registered_suites'])} registered suite(s), {len(a['detected_suites'])} detected but unregistered; "
          f"failure ledger {'present' if a['failure_ledger']['exists'] else 'absent'} ({a['failure_ledger']['path']}); {a['ci_commands']} CI test command(s) found")
    for s in a["detected_suites"]:
        print(f"      detected  {s['id']}  [{s['framework']}] {s['files']} file(s)  {s['command'] or '-'}")
    fr = a.get("findings") or {}  # REQ-043
    if fr.get("report"):
        print(f"      findings  {fr['report']}: {fr['findings']} unsuppressed Arbiter finding(s)")
    elif fr.get("note"):
        print(f"      {fr['note']}")
    w = layers["workspace"]
    print(f"  workspace     {w['rulepacks']} rulepack file(s), {w['playbooks']} playbook(s), {w['checklists']} checklist(s)" + ("" if w["ai_dir"] else " (no .ai/ directory)"))
    print(f"  code          {layers['code']['source_files']} source file(s)")
    if report["hints"]:
        print("\nTo improve the graph:")
        for hint in report["hints"]:
            print(f"  - {hint}")
    return 0


def run_graph_timeline(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1

    graph_path = Path(args.graph)
    if not graph_path.is_file():
        print(f"Graph file not found: {graph_path}; run ./omni graph build first", file=sys.stderr)
        return 1

    result = omni_graph.timeline(graph_path, args.node, depth=args.depth)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    if not result.get("ok"):
        print(f"Could not uniquely resolve node. Matches: {result.get('matches')}", file=sys.stderr)
        return 1

    events = result["events"]
    print(f"{result['node']['name']} ({result['node']['kind']}): {len(events)} dated event(s) within {args.depth} hop(s)")
    shown = events[-args.limit:] if args.limit else events
    if len(shown) < len(events):
        print(f"  (showing the newest {len(shown)}; --limit 0 for all)")
    last_date = None
    for event in shown:
        if event["date"] != last_date:
            print(f"\n{event['date']}")
            last_date = event["date"]
        print(f"  {event['kind']:<10} {event['name']:<16} {event['summary'][:100]}")
    return 0


def run_graph_why(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1

    graph_path = Path(args.graph)
    if not graph_path.is_file():
        print(f"Graph file not found: {graph_path}; run ./omni graph build first", file=sys.stderr)
        return 1

    result = omni_graph.why(graph_path, args.node)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    if not result.get("ok"):
        print(f"Could not uniquely resolve node. Matches: {result.get('matches')}", file=sys.stderr)
        return 1

    node = result["node"]
    print(f"{node['name']} ({node['kind']})" + (f"  {node['file']}" if node.get("file") else "") + (f"  [workspace {node['workspace']}]" if node.get("workspace") else ""))
    if node.get("summary"):
        print(f"  {node['summary']}")
    introduced = result["sections"].pop("introduced by", None)  # REQ-051: one line, right under the finding
    if introduced:
        item = introduced[0]
        print("  " + omni_graph.describe_introduced_by(
            {"commit": item["name"], "subject": item.get("summary"), "requirements": item.get("requirements")} if item.get("kind") == "commit" else {"commit": None}
        ))
    if not result["sections"]:
        print("  Nothing in the governance or assurance layers touches this node.")
        print("  (If you expected something, rebuild with `omni graph build` and check `omni graph show --all --layer governance`.)")
        return 0
    order = [
        "requirements", "requirement", "files touched", "affected code", "changelog", "changelog entries", "commits", "fix commits",
        "files modified", "files mentioned", "previous commit", "next commit", "test files", "tests", "test suites", "covers",
        "guards", "regression tests", "failures", "repeats", "repeated by", "prevented by",
        "rules and playbooks from those failures", "failures it prevents", "rules", "gaps",
    ]
    for section in sorted(result["sections"], key=lambda name: order.index(name) if name in order else len(order)):
        items = result["sections"][section]
        print(f"\n{section} ({len(items)})")
        for item in items[: args.limit]:
            extras = [item.get(key) for key in ("status", "date", "severity") if item.get(key)]
            line = f"  {item['name']}" + (f"  [{', '.join(str(e) for e in extras)}]" if extras else "")
            if item.get("summary"):
                line += f"  {item['summary'][:110]}"
            if item.get("via"):
                line += f"  <{item['via'][:80]}>"
            print(line)
        if len(items) > args.limit:
            print(f"  ... and {len(items) - args.limit} more (--limit or --json)")
    return 0


def run_graph_lineage(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1

    graph_path = Path(args.graph)
    if not graph_path.is_file():
        print(f"Graph file not found: {graph_path}; run ./omni graph build first", file=sys.stderr)
        return 1

    direction = "up" if args.up and not args.down else "down" if args.down and not args.up else "both"
    result = omni_graph.lineage(graph_path, args.node, direction=direction, depth=max(1, args.depth), include_code=args.code, limit=max(1, args.max_nodes))
    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    if not result.get("ok"):
        print(f"Could not uniquely resolve node. Matches: {result.get('matches')}", file=sys.stderr)
        return 1

    node = result["node"]
    print(f"{node['name']} ({node['kind']})" + (f"  {node['file']}" if node.get("file") else ""))
    if node.get("summary"):
        print(f"  {node['summary'][:140]}")
    for label, key, arrow in (("Upstream: what led to it", "upstream", "^"), ("Downstream: what came from it", "downstream", "v")):
        if (key == "upstream" and direction == "down") or (key == "downstream" and direction == "up"):
            continue
        items = result[key]
        dropped = result["truncated"][key]
        print(f"\n{label} ({len(items)}{'+' + str(dropped) if dropped else ''})")
        if not items:
            print("  (nothing)")
            continue
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            by_kind.setdefault(item["kind"], []).append(item)
        for kind_name, group in sorted(by_kind.items(), key=lambda pair: (min(i["depth"] for i in pair[1]), pair[0])):
            print(f"  {kind_name} ({len(group)})")
            for item in group[: args.limit]:
                extras = [item.get(k) for k in ("status", "date", "severity") if item.get(k)]
                line = f"    {arrow}{item['depth']} {item['name']}" + (f"  [{', '.join(str(e) for e in extras)}]" if extras else "")
                if item.get("summary"):
                    line += f"  {item['summary'][:90]}"
                print(line + f"  <{item['via']}>")
            if len(group) > args.limit:
                print(f"    ... and {len(group) - args.limit} more (--limit or --json)")
    if any(result["truncated"].values()):
        print(f"\nStopped at {args.max_nodes} nodes per direction, nearest first. Narrow with --depth, or raise --max-nodes.")
    return 0


def run_graph_impact(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1

    graph_path = Path(args.graph)
    if not graph_path.is_file():
        print(f"Graph file not found: {graph_path}; run ./omni graph build first", file=sys.stderr)
        return 1

    base = args.changed or gate_base_commit()
    changed = sorted(gate_changed_paths(base))
    result = omni_graph.impact(graph_path, changed, depth=max(0, args.depth))
    result["base"] = base
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"{len(changed)} changed path(s) since {base or 'the index'}: {len(result['changed'])} in the graph, {len(result['unresolved'])} unresolved")
    for path in result["unresolved"][: args.limit]:
        print(f"  ? {path}")
    for bucket in omni_graph.IMPACT_BUCKETS:
        items = result[bucket]
        if not items:
            continue
        print(f"\n{bucket} ({len(items)})")
        for item in items[: args.limit]:
            extras = [item.get(key) for key in ("status", "date", "severity") if item.get(key)]
            line = f"  {item['hops']}  {item['name']}" + (f"  [{', '.join(str(e) for e in extras)}]" if extras else "")
            if item.get("file") and item["kind"] in ("module", "file"):
                line += f"  {item['file']}"
            elif item.get("summary"):
                line += f"  {item['summary'][:90]}"
            print(line + f"  <{item['via']}>")
        if len(items) > args.limit:
            print(f"  ... and {len(items) - args.limit} more (--limit or --json)")
    print()
    print(omni_graph.impact_summary(result))
    return 0


# --------------------------------------------------------------------------
# REQ-043: omni graph findings -- Arbiter findings by directory, as a tree, JSON or the viewer
# --------------------------------------------------------------------------


def run_graph_findings(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1

    graph_path = Path(args.graph)
    if not graph_path.is_file():
        print(f"Graph file not found: {graph_path}; run ./omni graph build first", file=sys.stderr)
        return 1

    result = omni_graph.findings(graph_path, under=args.under, dimension=args.dimension, severity=args.severity, depth=max(0, args.depth))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(omni_graph.render_findings_tree(result))
        if not result["total"] and not any(result["filters"].values()):
            print("  Run `./omni gate` (which runs `arbiter gate`) and then `./omni graph build` to add the findings to the graph.")
    if not args.view:
        return 0

    view = omni_graph.build_view_html(graph_path, view="findings")  # the Findings tab seeds itself from the finding nodes
    if not view.get("ok"):
        print("The viewer assets are missing: " + ", ".join(f".ai/graph-viewer/{name}" for name in view.get("missing", [])) + ".", file=sys.stderr)
        return 1
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(view["html"], encoding="utf-8")
    print(f"\nWrote {output}: the viewer opens on the Findings tab ({result['total']} finding(s) listed above).")
    posix_uri = output.resolve().as_uri()
    windows_target = windows_view_target(output)
    uri = windows_target[1] if windows_target else posix_uri
    print(f"Open in a browser (works offline): {uri}")
    if args.open and not open_in_browser(uri, windows_target[0] if windows_target else None):
        print("  (no browser could be launched automatically; open the file by hand)")
    return 0


# --------------------------------------------------------------------------
# Failure ledger: what went wrong, why, and what now prevents it
# --------------------------------------------------------------------------

FAILURE_STATUSES = ["open", "fixed", "mitigated", "wontfix"]
FAILURE_LIST_FIELDS = ("affected", "fix_commits", "regression_tests", "prevention_rules")


def failure_ledger_path() -> Path:
    return Path(omni_graph.graph_config(Path("."))["failure_ledger"]) if omni_graph else FAILURE_LEDGER_PATH


def test_suites_path() -> Path:
    return Path(omni_graph.graph_config(Path("."))["test_suites_file"]) if omni_graph else TEST_SUITES_PATH


def load_failure_ledger() -> dict[str, Any]:
    if failure_ledger_path().is_file():
        return load_json(failure_ledger_path())
    return {"version": "1.0.0", "failure_id_prefix": "FAIL", "failures": []}


def next_failure_id(ledger: dict[str, Any]) -> str:
    prefix = str(ledger.get("failure_id_prefix", "FAIL"))
    numbers = [
        int(match.group(1))
        for item in ledger.get("failures", [])
        if isinstance(item, dict) and (match := re.fullmatch(rf"{re.escape(prefix)}-(\d+)", str(item.get("id", ""))))
    ]
    return f"{prefix}-{(max(numbers) + 1) if numbers else 1:03d}"


def normalize_failure_id(value: str, ledger: dict[str, Any]) -> str:
    value = value.strip().upper()
    if value.isdigit():
        return f"{ledger.get('failure_id_prefix', 'FAIL')}-{int(value):03d}"
    return value


def find_failure(ledger: dict[str, Any], failure_id: str) -> dict[str, Any] | None:
    wanted = normalize_failure_id(failure_id, ledger)
    for item in ledger.get("failures", []):
        if isinstance(item, dict) and item.get("id") == wanted:
            return item
    return None


def format_failure(item: dict[str, Any]) -> str:
    lines = [f"{item.get('id')}  [{item.get('status')}]  {item.get('title')}"]
    for label, key in (
        ("date", "date"), ("severity", "severity"), ("requirement", "requirement"), ("symptom", "symptom"),
        ("how detected", "how_detected"), ("root cause", "root_cause"), ("fix", "fix_summary"),
        ("no test because", "no_test_reason"), ("prevention notes", "prevention_notes"), ("repeats", "recurrence_of"),
    ):
        if item.get(key):
            lines.append(f"  {label}: {item[key]}")
    for label, key in (
        ("affected", "affected"), ("fix commits", "fix_commits"), ("regression tests", "regression_tests"),
        ("prevention rules", "prevention_rules"),
    ):
        if item.get(key):
            lines.append(f"  {label}: {', '.join(str(v) for v in item[key])}")
    return "\n".join(lines)


def run_failure_add(args: argparse.Namespace) -> int:
    ledger = load_failure_ledger()
    failure_id = args.id or next_failure_id(ledger)
    if find_failure(ledger, failure_id) is not None:
        print(f"Failure already exists: {failure_id}", file=sys.stderr)
        return 1
    if args.requirement and known_requirement_ids() and normalize_requirement_id(args.requirement) not in known_requirement_ids():
        print(f"Warning: {args.requirement} is not in the requirements registry.", file=sys.stderr)
    item: dict[str, Any] = {
        "id": failure_id,
        "date": args.date or datetime.now().strftime("%Y-%m-%d"),
        "title": args.title,
        "status": args.status,
        "severity": args.severity,
        "symptom": args.symptom,
        "how_detected": args.detected or "",
        "root_cause": args.root_cause or "",
        "requirement": normalize_requirement_id(args.requirement) if args.requirement else "",
        "affected": split_csv(args.affected),
        "fix_summary": args.fix or "",
        "fix_commits": split_csv(args.commits),
        "regression_tests": split_csv(args.tests),
        "no_test_reason": args.no_test_reason or "",
        "prevention_rules": split_csv(args.prevention_rules),
        "prevention_notes": args.prevention_notes or "",
        "recurrence_of": normalize_failure_id(args.recurrence_of, ledger) if args.recurrence_of else "",
    }
    ledger.setdefault("failures", []).append(item)
    failure_ledger_path().parent.mkdir(parents=True, exist_ok=True)
    write_json(failure_ledger_path(), ledger)
    print(f"Recorded {failure_id}: {args.title}")
    if item["recurrence_of"] and find_failure(ledger, item["recurrence_of"]) is None:
        print(f"Warning: {item['recurrence_of']} is not in the ledger.", file=sys.stderr)
    _print_failure_gaps(item)
    return 0


def _print_failure_gaps(item: dict[str, Any]) -> None:
    gaps = omni_graph_failure_gaps(item)
    for gap in gaps:
        print(f"  still needed: {gap}")


def omni_graph_failure_gaps(item: dict[str, Any]) -> list[str]:
    gaps = []
    if item.get("status") in ("fixed", "mitigated"):
        if not item.get("root_cause"):
            gaps.append("root_cause (--root-cause): why it happened, not just what broke")
        if not item.get("fix_summary"):
            gaps.append("fix_summary (--fix)")
        if not item.get("regression_tests") and not item.get("no_test_reason"):
            gaps.append("a regression test (--tests) or an honest --no-test-reason")
        if not item.get("prevention_rules") and not item.get("prevention_notes"):
            gaps.append("what stops it recurring (--prevention-rules or --prevention-notes)")
    elif item.get("status") == "open" and not item.get("root_cause"):
        gaps.append("root_cause once it is understood (omni failure update --root-cause ...)")
    return gaps


def run_failure_list(args: argparse.Namespace) -> int:
    ledger = load_failure_ledger()
    items = [i for i in ledger.get("failures", []) if isinstance(i, dict) and (not args.status or i.get("status") == args.status)]
    if not items:
        print("No failures recorded." if not args.status else f"No failures with status {args.status}.")
        return 0
    for item in items:
        tests = len(item.get("regression_tests") or [])
        print(f"{item.get('id')}  {str(item.get('status')):<9}  {str(item.get('date', '')):<10}  {tests} test(s)  {item.get('title')}")
    return 0


def run_failure_show(args: argparse.Namespace) -> int:
    ledger = load_failure_ledger()
    item = find_failure(ledger, args.id)
    if item is None:
        print(f"Failure not found: {args.id}", file=sys.stderr)
        return 1
    print(format_failure(item))
    return 0


def run_failure_search(args: argparse.Namespace) -> int:
    ledger = load_failure_ledger()
    needle = args.text.lower()
    hits = [i for i in ledger.get("failures", []) if isinstance(i, dict) and needle in json.dumps(i).lower()]
    if not hits:
        print("No matches.")
        return 0
    for item in hits:
        print(f"{item.get('id')}  [{item.get('status')}]  {item.get('title')}")
    return 0


def run_failure_update(args: argparse.Namespace) -> int:
    ledger = load_failure_ledger()
    item = find_failure(ledger, args.id)
    if item is None:
        print(f"Failure not found: {args.id}", file=sys.stderr)
        return 1
    changed: list[str] = []
    for field, value in (
        ("status", args.status), ("severity", args.severity), ("title", args.title), ("symptom", args.symptom),
        ("how_detected", args.detected), ("root_cause", args.root_cause), ("fix_summary", args.fix),
        ("no_test_reason", args.no_test_reason), ("prevention_notes", args.prevention_notes),
    ):
        if value:
            item[field] = value
            changed.append(field)
    if args.requirement:
        item["requirement"] = normalize_requirement_id(args.requirement)
        changed.append("requirement")
    if args.recurrence_of:
        item["recurrence_of"] = normalize_failure_id(args.recurrence_of, ledger)
        changed.append("recurrence_of")
    for field, value in (
        ("affected", args.affected), ("fix_commits", args.commits), ("regression_tests", args.tests),
        ("prevention_rules", args.prevention_rules),
    ):
        additions = [v for v in split_csv(value) if v not in (item.get(field) or [])]
        if additions:
            item[field] = list(item.get(field) or []) + additions
            changed.append(field)
    if not changed:
        print("Nothing to update: pass --status, --root-cause, --fix, --tests, --prevention-rules, etc.", file=sys.stderr)
        return 1
    write_json(failure_ledger_path(), ledger)
    print(f"Updated {item['id']} ({', '.join(changed)}); status is {item.get('status')}.")
    _print_failure_gaps(item)
    return 0


def run_failure_check(args: argparse.Namespace) -> int:
    """Ledger completeness, plus whether every reference resolves to something real."""
    problems: list[str] = []
    if not failure_ledger_path().is_file():
        print("No failure ledger yet; nothing to check.")
        return 0
    if omni_graph is not None:
        problems.extend(omni_graph.check_failure_ledger(Path("."))["problems"])
    ledger = load_failure_ledger()
    ids = known_requirement_ids()
    aliases = requirement_id_aliases()  # REQ-044: an entry recorded under a since-renumbered id still resolves
    known = {i.get("id") for i in ledger.get("failures", []) if isinstance(i, dict)}
    for item in ledger.get("failures", []):
        if not isinstance(item, dict):
            continue
        fid = item.get("id")
        if item.get("requirement") and ids and resolve_requirement_id(str(item["requirement"]), aliases) not in ids:
            problems.append(f"{fid}: requirement {item['requirement']} is not in the registry")
        if item.get("recurrence_of") and item["recurrence_of"] not in known:
            problems.append(f"{fid}: recurrence_of {item['recurrence_of']} is not in the ledger")
        for path in list(item.get("regression_tests") or []) + list(item.get("affected") or []):
            file_part = re.split(r"::|#", str(path))[0]
            file_part = re.sub(r":\d+(?:-\d+)?$", "", file_part)
            if ("/" in file_part or re.search(r"\.\w{1,5}$", file_part)) and not Path(file_part).exists():
                problems.append(f"{fid}: '{path}' does not exist on disk")
        rule_ids = _all_rule_ids()
        for ref in item.get("prevention_rules") or []:
            if ref not in rule_ids and not Path(str(ref)).exists():
                problems.append(f"{fid}: prevention rule '{ref}' is neither a rule id in .ai/rules nor an existing file")
    if problems:
        print(f"Failure ledger: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"Failure ledger OK ({len(known)} failure(s)).")
    return 0


def _all_rule_ids() -> set[str]:
    ids: set[str] = set()
    for file_path in RULEPACK_FILES:
        try:
            pack = load_json(Path(file_path))
        except (OSError, json.JSONDecodeError):
            continue
        for rule in pack.get("rules", []) if isinstance(pack, dict) else []:
            if isinstance(rule, dict) and rule.get("id"):
                ids.add(str(rule["id"]))
    return ids


def known_requirement_ids() -> set[str]:
    """Ids from the configured requirement files (any project's), else from the OmniEngineering registry."""
    if omni_graph is not None:
        ids = {item["id"] for item in omni_graph._load_requirement_items(Path("."))}
        if ids:
            return ids
    return all_requirement_ids()


def failures_referencing(requirement_id: str) -> list[str]:
    ledger = load_failure_ledger() if failure_ledger_path().is_file() else {"failures": []}
    return [str(i.get("id")) for i in ledger.get("failures", []) if isinstance(i, dict) and i.get("requirement") == requirement_id]


# --------------------------------------------------------------------------
# Test suites: which tests exist, where, and how to run them
# --------------------------------------------------------------------------


def project_source_files() -> list[str]:
    if omni_graph is None:
        return []
    root = Path(".").resolve()
    return sorted(
        path.relative_to(root).as_posix()
        for path, _language in omni_graph.discover_source_files(root, sorted(omni_graph.language_extensions()))
    )


def load_test_suites_config() -> dict[str, Any]:
    if test_suites_path().is_file():
        return load_json(test_suites_path())
    return {"version": "1.0.0", "suites": []}


def suite_entry(suite: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": suite["id"], "name": suite.get("name") or suite["id"], "kind": suite.get("kind") or "unit",
        "framework": suite.get("framework") or "", "paths": list(suite.get("paths") or []),
        "command": suite.get("command") or "", "covers": list(suite.get("covers") or []), "notes": suite.get("notes") or "",
    }


def run_test_detect(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1
    files = project_source_files()
    registered = omni_graph.resolve_test_suites(Path("."), files)
    proposals = registered["auto"]
    if args.json:
        print(json.dumps({"registered": [s["id"] for s in registered["manual"]], "detected": proposals}, indent=2))
    elif not proposals:
        print(f"Nothing new: every detected test file is already in a registered suite ({len(registered['manual'])} registered).")
    else:
        print(f"{len(proposals)} detected suite(s) not yet in {test_suites_path()}:")
        for suite in proposals:
            print(f"\n  {suite['id']}  [{suite['kind']}, {suite['framework']}]  {len(suite['files'])} file(s)")
            print(f"    paths:   {', '.join(suite['paths'][:6])}{' ...' if len(suite['paths']) > 6 else ''}")
            print(f"    command: {suite['command'] or '(unknown; pass --command to omni test add)'}")
            if suite["covers"]:
                print(f"    covers:  {', '.join(suite['covers'])}")
    if args.write and proposals:
        config = load_test_suites_config()
        config.setdefault("suites", []).extend(suite_entry(s) for s in proposals)
        test_suites_path().parent.mkdir(parents=True, exist_ok=True)
        write_json(test_suites_path(), config)
        print(f"\nRegistered {len(proposals)} suite(s) in {test_suites_path()}. Review the commands and paths, then `omni graph build`.")
    elif proposals and not args.json:
        print("\nRegister them with `omni test detect --write`, or add one by hand with `omni test add`.")
    return 0


def run_test_add(args: argparse.Namespace) -> int:
    config = load_test_suites_config()
    suite_id = args.id or re.sub(r"[^a-z0-9]+", "-", args.name.lower()).strip("-")
    if any(isinstance(s, dict) and s.get("id") == suite_id for s in config.get("suites", [])):
        print(f"Test suite already exists: {suite_id} (remove it first with `omni test remove {suite_id}`)", file=sys.stderr)
        return 1
    paths = split_csv(args.paths)
    if not paths:
        print("--paths is required: files, directories or globs that hold this suite's tests.", file=sys.stderr)
        return 1
    if omni_graph is not None and not args.allow_missing:
        _files, gone = omni_graph._expand_suite_paths(Path("."), paths, project_source_files())
        if gone:
            print(f"These paths match no source file: {', '.join(gone)}. Fix them, or pass --allow-missing.", file=sys.stderr)
            return 1
    config.setdefault("suites", []).append(suite_entry({
        "id": suite_id, "name": args.name, "kind": args.kind, "framework": args.framework, "paths": paths,
        "command": args.run_command, "covers": split_csv(args.covers), "notes": args.notes,
    }))
    test_suites_path().parent.mkdir(parents=True, exist_ok=True)
    write_json(test_suites_path(), config)
    print(f"Registered test suite {suite_id}: {args.name}")
    return 0


def run_test_remove(args: argparse.Namespace) -> int:
    config = load_test_suites_config()
    before = len(config.get("suites", []))
    config["suites"] = [s for s in config.get("suites", []) if not (isinstance(s, dict) and s.get("id") == args.id)]
    if len(config["suites"]) == before:
        print(f"Test suite not found: {args.id}", file=sys.stderr)
        return 1
    write_json(test_suites_path(), config)
    print(f"Removed test suite {args.id}")
    return 0


def run_test_list(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1
    resolved = omni_graph.resolve_test_suites(Path("."), project_source_files())
    if not resolved["suites"]:
        print("No test suites registered or detected.")
        return 0
    for suite in resolved["suites"]:
        tag = "registered" if suite["source"] == "manual" else "detected  "
        print(f"{tag}  {suite['id']:<32} {suite['kind']:<11} {suite['framework']:<10} {len(suite['files']):>3} file(s)  {suite['command'] or '-'}")
    return 0


def run_test_check(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1
    result = omni_graph.check_test_suites(Path("."), project_source_files())
    if not result["present"]:
        print(f"No {test_suites_path()} yet; run `omni test detect --write` to register what was found.")
    for problem in result["problems"]:
        print(f"  - {problem}")
    if result["unregistered_files"]:
        print(f"note: {len(result['unregistered_files'])} detected test file(s) are not in a registered suite: run `omni test detect`.")
    if result["problems"]:
        return 1
    print(f"Test suites OK ({result['suites']} registered).")
    return 0


# --------------------------------------------------------------------------
# omni test run: run registered suites -- all, by name, or only those the change set impacts
# --------------------------------------------------------------------------

TEST_RUN_DEFAULT_TIMEOUT = 900.0
TEST_RUN_TAIL_LINES = 5


def _suite_scope_touches(entries: list[str], impacted: set[str]) -> bool:
    """Does any file, path or coverage entry of a suite overlap an impacted path? Directory prefixes count both ways."""
    for raw in entries:
        entry = str(raw).strip().replace("\\", "/")
        if entry.startswith("./"):
            entry = entry[2:]
        entry = entry.rstrip("/")
        if not entry:
            continue
        if any(char in entry for char in "*?["):
            if any(fnmatch.fnmatch(path, entry) for path in impacted):
                return True
            continue
        for path in impacted:
            if path == entry or path.startswith(entry + "/") or entry.startswith(path + "/"):
                return True
    return False


def select_suites(resolved: dict[str, Any], names: list[str], impacted: set[str] | None) -> list[dict[str, Any]]:
    """Which suites to run: the named ones; else those whose files, paths or coverage meet an impacted path; else all."""
    suites = [s for s in resolved.get("suites", []) if isinstance(s, dict) and s.get("id")]
    if names:
        wanted = {str(n).strip() for n in names if str(n).strip()}
        return [s for s in suites if s["id"] in wanted or str(s.get("name") or "") in wanted]
    if impacted is None:
        return suites
    paths = {str(p).strip().replace("\\", "/").rstrip("/") for p in impacted if str(p).strip()}
    return [
        s for s in suites
        if _suite_scope_touches(list(s.get("files") or []) + list(s.get("paths") or []) + list(s.get("covers") or []), paths)
    ]


def _output_tail(*chunks: Any) -> list[str]:
    lines: list[str] = []
    for chunk in chunks:
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="replace")
        lines.extend(line for line in str(chunk or "").splitlines() if line.strip())
    return lines[-TEST_RUN_TAIL_LINES:]


def _kill_process_tree(process: subprocess.Popen) -> None:
    """Kill a suite command and everything it spawned: taskkill /T on Windows, the process group elsewhere."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False)
        else:
            import signal

            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError, subprocess.SubprocessError):
        pass
    try:
        process.kill()
    except OSError:
        pass


def run_test_suite(suite: dict[str, Any], root: Path, timeout: float) -> dict[str, Any]:
    """Run one suite's registered command through the shell from the project root and report PASS, FAIL, SKIP or TIMEOUT."""
    name = str(suite.get("name") or suite.get("id") or "")
    command = str(suite.get("command") or "").strip()
    result: dict[str, Any] = {"name": name, "id": suite.get("id"), "command": command, "status": "SKIP", "exit": None, "duration_s": 0.0, "tail": []}
    if not command:
        result["tail"] = ["no run command registered; set one with `omni test add --command` or edit the suite registry"]
        return result
    started = time.monotonic()
    # Start the command in its own process group (POSIX) or process group (Windows) so a timeout can
    # kill the whole tree. `subprocess.run(timeout=...)` only kills the shell; on Windows the pipes then
    # stay open until the grandchild exits, and a 0.5 s timeout waited the command's full run time.
    popen_kwargs: dict[str, Any] = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    try:
        process = subprocess.Popen(
            command, shell=True, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", **popen_kwargs,
        )
    except OSError as exc:
        result.update(status="FAIL", duration_s=round(time.monotonic() - started, 2), tail=[f"could not start: {exc}"])
        return result
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        result.update(status="TIMEOUT", duration_s=round(time.monotonic() - started, 2), tail=_output_tail(stdout, stderr) or [f"no output within {timeout:.0f}s"])
        return result
    completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    result.update(
        status="PASS" if completed.returncode == 0 else "FAIL", exit=completed.returncode,
        duration_s=round(time.monotonic() - started, 2), tail=_output_tail(completed.stdout, completed.stderr),
    )
    return result


def run_test_run(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1
    root = Path(".").resolve()
    resolved = omni_graph.resolve_test_suites(root, project_source_files())
    names = [str(n) for n in (getattr(args, "names", None) or [])]
    notes: list[str] = []
    impacted: set[str] | None = None
    reached_suites: set[str] = set()
    base = None
    if args.impacted and not names:
        base = args.changed or gate_base_commit()
        changed = sorted(gate_changed_paths(base))
        graph_path = Path(GRAPH_DEFAULT_OUTPUT)
        if graph_path.is_file():
            impact = omni_graph.impact(graph_path, changed)
            impacted = set(changed) | {str(t["file"]) for t in impact["tests"] if t.get("file")}
            reached_suites = {str(s["name"]) for s in impact["suites"]}
        else:
            notes.append(f"note: no graph at {graph_path} (run `omni graph build`), so the change set cannot be narrowed; running every suite")
    selected = select_suites(resolved, names, impacted)
    if reached_suites:
        chosen = {s["id"] for s in selected}
        selected += [s for s in resolved["suites"] if s["id"] in reached_suites and s["id"] not in chosen]
    if names:
        found = {s["id"] for s in selected} | {str(s.get("name") or "") for s in selected}
        unknown = [n for n in names if n not in found]
        if unknown:
            print(f"Unknown test suite(s): {', '.join(unknown)}. Registered: {', '.join(s['id'] for s in resolved['suites']) or 'none'}", file=sys.stderr)
            return 1

    for note in notes:
        print(note)
    if not selected:
        if args.json:
            print(json.dumps({"base": base, "impacted": sorted(impacted) if impacted is not None else None, "results": [], "summary": {}, "ok": True}, indent=2))
        elif impacted is not None:
            print(f"No registered suite covers the {len(impacted)} impacted path(s); nothing to run.")
        else:
            print("No test suites registered or detected; run `omni test detect --write` first.")
        return 0

    results: list[dict[str, Any]] = []
    for suite in selected:
        outcome = run_test_suite(suite, root, max(1.0, float(args.timeout)))
        results.append(outcome)
        if not args.json:
            print(f"{outcome['status']:<8} {suite['id']:<32} {outcome['duration_s']:>7.1f}s  {outcome['command'] or '-'}")
            if outcome["status"] != "PASS":
                for line in outcome["tail"]:
                    print(f"    {line}")
    summary = {status: sum(1 for r in results if r["status"] == status) for status in ("PASS", "FAIL", "SKIP", "TIMEOUT")}
    ok = not (summary["FAIL"] or summary["TIMEOUT"])
    if args.json:
        print(json.dumps({
            "base": base, "impacted": sorted(impacted) if impacted is not None else None,
            "selected": [s["id"] for s in selected], "results": results, "summary": summary, "ok": ok,
        }, indent=2))
    else:
        print(f"\n{len(results)} suite(s): {summary['PASS']} passed, {summary['FAIL']} failed, {summary['TIMEOUT']} timed out, {summary['SKIP']} skipped")
    return 0 if ok else 1


def run_graph_render(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1

    graph_path = Path(args.graph)
    if not graph_path.is_file():
        print(f"Graph file not found: {graph_path}; run ./omni graph build first", file=sys.stderr)
        return 1

    result = omni_graph.render(
        graph_path,
        include_external=args.include_external,
        max_nodes=args.max_nodes,
        focus=args.focus,
        depth=args.depth,
        all_layers=args.all_layers,
    )
    if not result.get("ok"):
        print(f"Could not resolve --focus symbol: {args.focus}", file=sys.stderr)
        return 1

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result["svg"], encoding="utf-8")

    summary = {key: value for key, value in result.items() if key != "svg"}
    summary["output"] = str(output)
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"Wrote {summary['nodes_rendered']} nodes / {summary['edges_rendered']} edges to {output}")
    if summary["truncated"]:
        print(
            f"  Truncated to the {args.max_nodes} highest-degree nodes out of "
            f"{summary['nodes_total']} total; use --max-nodes or --focus to change what's shown."
        )
    return 0


def resolve_context_profile_name(profile: str) -> str:
    key = profile.strip().lower()
    return CONTEXT_PROFILE_ALIASES.get(key, key)


def run_context(args: argparse.Namespace) -> int:
    manifest_path = Path(".ai/context-manifest.json")
    if not manifest_path.is_file():
        print("Missing .ai/context-manifest.json", file=sys.stderr)
        return 1

    manifest = load_json(manifest_path)
    profiles = manifest.get("context_profiles")
    if not isinstance(profiles, dict):
        print("Manifest does not define context_profiles", file=sys.stderr)
        return 1

    profile_name = resolve_context_profile_name(args.profile)
    selected = profiles.get(profile_name)
    if not isinstance(selected, list):
        print(f"Unknown context profile: {args.profile}", file=sys.stderr)
        print(f"Available profiles: {', '.join(sorted(profiles))}", file=sys.stderr)
        return 1

    files = [str(item) for item in selected if isinstance(item, str)]
    if args.extra:
        files.extend(args.extra)

    if args.json:
        payload = {
            "profile": profile_name,
            "files": [
                {
                    "path": file_path,
                    "exists": Path(file_path).is_file(),
                    "lines": len(Path(file_path).read_text(encoding="utf-8").splitlines())
                    if Path(file_path).is_file()
                    else None,
                }
                for file_path in files
            ],
            "notes": [
                "Read these files before task-specific project files.",
                "Use .ai/project-map.md to choose the smallest relevant project path set.",
                "Escalate to deep_policy only when policy or workflow uncertainty requires it.",
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0

    print(f"OmniEngineering context profile: {profile_name}")
    print("")
    for file_path in files:
        path = Path(file_path)
        if path.is_file():
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            print(f"- {file_path} ({line_count} lines)")
        else:
            print(f"- {file_path} (missing)")
    print("")
    print("Requirements: never read the registry in full. Use `./omni requirement list --status pending`,")
    print("`./omni requirement show <ID>`, or `./omni requirement search <text>`.")
    print("Then inspect only the task-relevant project files selected from .ai/project-map.md.")
    return 0


def build_doctor_report(probe: bool = False) -> DoctorReport:
    """Every doctor check. `probe` forces the MCP servers to be launched (`omni doctor --probe`, REQ-050);
    `omni gate` never passes it, so a commit reuses the day's memo."""
    report = DoctorReport()
    verify_required_ai_files(report)
    parsed = validate_json_files(report)
    validate_context_manifest(parsed.get(".ai/context-manifest.json"), report)
    validate_ruleset(parsed.get(".ai/rules/universal-engineering-ruleset.json"), report)
    validate_rulepacks(parsed, report)
    validate_requirements(parsed.get(".ai/requirements/requirements.json"), report)
    validate_requirement_ids(report)
    validate_requirement_aliases(report)  # REQ-044
    validate_failure_ledger(parsed.get(".ai/failures/failure-ledger.json"), report)
    validate_test_suites(parsed.get(".ai/test-suites.json"), report)
    validate_graph_config(report)
    validate_assistant_pointers(report)
    validate_entrypoint_sources(report)
    validate_synced_ignore_files(report)
    validate_workspace_placement(report)
    validate_markdown_assets(report)
    validate_project_map(report)
    validate_project_map_freshness(report)
    validate_project_graph(report)
    validate_recent_commits_tracked(report)
    validate_commit_identity(report)  # REQ-049
    validate_cli_entrypoints(report)
    validate_mcp_registrations(report, force_probe=probe)
    validate_vendored_workspaces(report)
    validate_arbiter_baseline(report)
    validate_arbiter_version(report)
    validate_omni_version_present(report)
    report.posture = compute_posture()
    return report


def run_doctor(args: argparse.Namespace | None = None) -> int:
    report = build_doctor_report(probe=bool(getattr(args, "probe", False)))
    if getattr(args, "json", False):
        print(json.dumps(report.to_json(), indent=2))
    else:
        report.print()
    return 0 if report.ok else 1


def run_sync(args: argparse.Namespace) -> int:
    force = bool(getattr(args, "force", False))
    skipped: list[str] = []
    skipped.extend(write_entrypoint_sources(force))

    report = DoctorReport()
    verify_required_ai_files(report)
    if not report.ok:
        report.print()
        return 1

    skipped.extend(write_assistant_pointers(force))
    skipped.extend(write_synced_ignore_files(force))
    if skipped:
        print("")
        print("Sync skipped existing non-Omni files to avoid overwriting project-owned configuration:")
        for file_path in skipped:
            print(f"- {file_path}")
        print("Merge those files manually, or rerun `./omni sync --force` if replacement is intentional.")
        return 1
    print("Done. Assistant shims, .ai entrypoints, and ignore files are synced.")
    return 0


def selected_adoption_files(args: argparse.Namespace) -> list[str]:
    files = [".ai"]
    tool_names: list[str]
    if args.tools == "all":
        tool_names = list(ADOPTION_TOOL_FILES)
    elif args.tools == "none":
        tool_names = []
    else:
        tool_names = [tool.strip() for tool in args.tools.split(",") if tool.strip()]

    unknown = sorted(tool for tool in tool_names if tool not in ADOPTION_TOOL_FILES)
    if unknown:
        raise ValueError(
            f"Unknown tool(s): {', '.join(unknown)}. "
            f"Available: {', '.join(sorted(ADOPTION_TOOL_FILES))}, all, none"
        )

    for tool in tool_names:
        files.extend(ADOPTION_TOOL_FILES[tool])
    if args.include_cli:
        files.extend(ADOPTION_CLI_FILES)
    if args.include_legal:
        files.extend(ADOPTION_LEGAL_FILES)
    if args.include_presentation:
        files.extend(ADOPTION_PRESENTATION_FILES)
    return list(dict.fromkeys(files))


def copy_adoption_path(source_root: Path, target_root: Path, relative_path: str, force: bool, dry_run: bool) -> str:
    source = source_root / relative_path
    target = target_root / relative_path
    if not source.exists():
        return f"missing source: {relative_path}"
    if target.exists() and not force:
        return f"skip existing: {relative_path}"
    if dry_run:
        action = "replace" if target.exists() else "copy"
        return f"{action}: {relative_path}"

    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(
            source,
            target,
            ignore=shutil.ignore_patterns(
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
                ".DS_Store",
            ),
        )
    else:
        shutil.copy2(source, target)
    if relative_path == ".ai":
        # The source repository's own failure history and test suites are not the adopter's; ship empty ones.
        for reset_path, empty in (
            (FAILURE_LEDGER_PATH, {"version": "1.0.0", "failure_id_prefix": "FAIL", "failures": []}),
            (TEST_SUITES_PATH, {"version": "1.0.0", "suites": []}),
        ):
            if (target_root / reset_path).is_file():
                write_json(target_root / reset_path, empty)
        # graph-config.json in this repository marks OmniEngineering itself as the project; an adopter's differs.
        (target_root / ".ai/graph-config.json").unlink(missing_ok=True)
    return f"copied: {relative_path}"


def run_adopt(args: argparse.Namespace) -> int:
    source_root = Path(__file__).resolve().parent
    target_root = Path(args.target).resolve()
    if target_root == source_root:
        print("Refusing to adopt into the source repository itself.", file=sys.stderr)
        return 1
    if not target_root.exists():
        if args.dry_run:
            print(f"Target does not exist yet: {target_root}")
        else:
            target_root.mkdir(parents=True)
    if target_root.exists() and not target_root.is_dir():
        print(f"Adoption target is not a directory: {target_root}", file=sys.stderr)
        return 1

    try:
        files = selected_adoption_files(args)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1

    print(f"OmniEngineering adoption target: {target_root}")
    print(f"Mode: {'dry-run' if args.dry_run else 'copy'}")
    print(f"Force: {str(args.force).lower()}")
    print("")

    # A path inside a directory this same run just copied whole (the graph-viewer files --include-cli
    # lists live under `.ai`) is already in place: it is reported with its directory rather than as
    # "skip existing", which used to make every --include-cli adoption into an empty target exit 1.
    results: list[str] = []
    copied_dirs: dict[str, str] = {}  # relative directory path -> the verb its own result line used
    for relative_path in files:
        parent = next((d for d in copied_dirs if relative_path.startswith(d + "/")), None)
        if parent is not None:
            results.append(f"{copied_dirs[parent]}: {relative_path} (with {parent})")
            continue
        result = copy_adoption_path(source_root, target_root, relative_path, args.force, args.dry_run)
        results.append(result)
        verb = result.split(":", 1)[0]
        if verb in {"copied", "copy", "replace"} and (source_root / relative_path).is_dir():
            copied_dirs[relative_path] = verb
    for result in results:
        print(f"- {result}")

    skipped = [result for result in results if result.startswith("skip existing")]
    missing = [result for result in results if result.startswith("missing source")]
    if skipped:
        print("")
        print("Existing target files were skipped. Merge manually or rerun with --force if replacement is intentional.")

    if not args.dry_run and (target_root / ".ai").is_dir():
        write_omni_version_file(source_root, target_root)
        print("")
        print(
            f"Recorded adoption source and ref in {target_root / OMNI_VERSION_FILE} "
            "-- future template improvements can be pulled in with `omni update`."
        )

    with_arbiter = getattr(args, "with_arbiter", None)
    if with_arbiter is not None:
        print("")
        print("Arbiter alongside the workspace:")
        if arbiter_install(target_root, with_arbiter, skip_pip=getattr(args, "skip_pip", False), dry_run=args.dry_run) != 0:
            return 1

    if missing:
        return 1
    return 1 if skipped and not args.dry_run else 0


def _arbiter_pip_command(source: str) -> list[str]:
    """`pip install` for a local checkout (editable, so a developer's fixes
    land immediately) or for a git/PyPI spec, both with the extra the MCP
    server needs."""
    local = Path(source).expanduser()
    if local.is_dir() and (local / "pyproject.toml").is_file():
        return [sys.executable, "-m", "pip", "install", "-e", f"{local.resolve()}[{ARBITER_PIP_EXTRAS}]"]
    if source.startswith(("git+", "http://", "https://")):
        return [sys.executable, "-m", "pip", "install", f"arbiter-eval[{ARBITER_PIP_EXTRAS}] @ {source}"]
    return [sys.executable, "-m", "pip", "install", f"{source}[{ARBITER_PIP_EXTRAS}]"]


def arbiter_install(target_root: Path, source: str, skip_pip: bool = False, dry_run: bool = False) -> int:
    """Install Arbiter beside the workspace and wire the two together.

    Four things, each skipped when already present so the command is safe to
    rerun: the package (pip), the `arbiter` entry in `.mcp.json`, the
    `completion.arbiter_gate` command rule in the completion rulepack, and a
    starter `arbiter.yaml`. Nothing is overwritten; a project that tuned any
    of them keeps its version. The installed version is then recorded in
    `.ai/omni-version.json` and, with `arbiter` on PATH, a baseline is cut
    (`arbiter_write_baseline`), itself a no-op when one exists.
    """
    verb = "would " if dry_run else ""
    status = 0

    if skip_pip:
        print("- pip: skipped (--skip-pip); make sure `arbiter` is on PATH before `omni gate` runs")
    else:
        command = _arbiter_pip_command(source)
        print(f"- pip: {verb}run {' '.join(command)}")
        if not dry_run:
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if completed.returncode != 0:
                tail = "\n".join((completed.stdout + "\n" + completed.stderr).strip().splitlines()[-5:])
                print(f"  pip failed (exit {completed.returncode}):\n{tail}", file=sys.stderr)
                status = 1
            elif shutil.which("arbiter") is None:
                print("  installed, but `arbiter` is not on PATH in this shell; open a new one or check pip's script directory")

    if dry_run:
        print(f"- {OMNI_VERSION_FILE}: would record the installed Arbiter version and its source")
    else:
        record_arbiter_version(target_root, source)

    mcp_path = target_root / MCP_REGISTRATION_PATH
    registration: dict[str, Any] = {"mcpServers": {}}
    if mcp_path.is_file():
        try:
            loaded = load_json(mcp_path)
            if isinstance(loaded, dict):
                registration = loaded
        except json.JSONDecodeError:
            print(f"- {MCP_REGISTRATION_PATH}: not valid JSON; fix it by hand, then rerun", file=sys.stderr)
            return 1
    servers = registration.setdefault("mcpServers", {})
    if "arbiter" in servers:
        print(f"- {MCP_REGISTRATION_PATH}: `arbiter` already registered")
    else:
        servers["arbiter"] = {"command": "arbiter", "args": ["mcp"]}
        print(f"- {MCP_REGISTRATION_PATH}: {verb}register `arbiter mcp`")
        if not dry_run:
            write_json(mcp_path, registration)

    rulepack_path = target_root / ".ai" / "rules" / "completion-workflow.json"
    if not rulepack_path.is_file():
        print(f"- {rulepack_path.relative_to(target_root)}: missing; adopt the workspace first, then rerun")
        status = 1
    else:
        rulepack = load_json(rulepack_path)
        rules = rulepack.setdefault("rules", [])
        if any(isinstance(r, dict) and r.get("id") == ARBITER_GATE_RULE_ID for r in rules):
            print(f"- completion rulepack: `{ARBITER_GATE_RULE_ID}` already present")
        else:
            rules.append(json.loads(json.dumps(ARBITER_GATE_RULE)))  # a deep copy; the constant stays pristine
            print(f"- completion rulepack: {verb}add `{ARBITER_GATE_RULE_ID}` (type command)")
            if not dry_run:
                write_json(rulepack_path, rulepack)

    config_path = target_root / "arbiter.yaml"
    if config_path.is_file():
        print("- arbiter.yaml: already present, left alone")
    else:
        print(f"- arbiter.yaml: {verb}write a starter policy (fail on critical, fail on new high)")
        if not dry_run:
            config_path.write_text(ARBITER_STARTER_CONFIG, encoding="utf-8")

    # Only scan output and Arbiter's scan cache are ignored; .arbiter/baseline.json
    # (and knowledge.json) are meant to be committed, so `.arbiter/` as a whole
    # is never added here.
    gitignore = target_root / ".gitignore"
    ignored = gitignore.read_text(encoding="utf-8", errors="replace") if gitignore.is_file() else ""
    if "arbiter-out" in ignored and ".arbiter/cache.json" in ignored:
        print("- .gitignore: arbiter-out/ and .arbiter/cache.json already ignored")
    else:
        print(f"- .gitignore: {verb}ignore arbiter-out/ (scan output) and .arbiter/cache.json (scan cache)")
        if not dry_run:
            with gitignore.open("a", encoding="utf-8") as handle:
                if "arbiter-out" not in ignored:
                    handle.write("\n# Arbiter scan output\narbiter-out/\n")
                if ".arbiter/cache.json" not in ignored:
                    handle.write("# Arbiter scan cache (the baseline beside it is committed)\n.arbiter/cache.json\n")

    if dry_run:
        print(f"- {ARBITER_BASELINE_PATH}: would cut a baseline from a full offline scan (`omni arbiter baseline`)")
    elif shutil.which("arbiter") is None:
        print(f"- {ARBITER_BASELINE_PATH}: skipped, `arbiter` is not on PATH; run `omni arbiter baseline` once it is")
    elif arbiter_write_baseline(target_root) != 0:
        status = 1

    print("")
    print("Next: `omni doctor` starts the registered MCP server for real, and `omni gate` now runs")
    print("`arbiter gate --changed` whenever source changes. For CI, see ci/github-action in the")
    print("arbiter checkout, or run `arbiter gate .` after the test step.")
    return status


def run_arbiter_install(args: argparse.Namespace) -> int:
    target_root = Path(args.target).resolve()
    if not target_root.is_dir():
        print(f"Target is not a directory: {target_root}", file=sys.stderr)
        return 1
    print(f"Arbiter alongside OmniEngineering in {target_root}")
    print(f"Mode: {'dry-run' if args.dry_run else 'apply'}")
    print("")
    return arbiter_install(target_root, args.source, skip_pip=args.skip_pip, dry_run=args.dry_run)


# --------------------------------------------------------------------------
# Arbiter report helpers (REQ-036): the newest report under the gate rule's
# --out directory, whether it still describes HEAD, and the posture that
# `omni doctor` prints from it.
# --------------------------------------------------------------------------

ARBITER_DEFAULT_OUT_DIR = Path("arbiter-out")
ARBITER_HIGH_OR_ABOVE = {"high", "critical"}
# Arbiter writes `started_at` to the second; a file touched within that same second is not "after" the scan.
ARBITER_FRESHNESS_TOLERANCE_S = 1.0


def arbiter_gate_rule() -> dict[str, Any] | None:
    """The `completion.arbiter_gate` rule from whichever rulepack carries it, or None when Arbiter is not wired."""
    for file_path in RULEPACK_FILES:
        try:
            rulepack = load_json(Path(file_path))
        except (OSError, json.JSONDecodeError):
            continue
        for rule in rulepack.get("rules", []) if isinstance(rulepack, dict) else []:
            if isinstance(rule, dict) and rule.get("id") == ARBITER_GATE_RULE_ID:
                return rule
    return None


def arbiter_out_dir(rule: dict[str, Any] | None) -> Path:
    """Where the rule's `validation.run` tells Arbiter to write: the value after `--out`, else `arbiter-out`."""
    validation = rule.get("validation") if isinstance(rule, dict) else None
    run = str(validation.get("run", "")) if isinstance(validation, dict) else ""
    try:
        argv = shlex.split(run)
    except ValueError:
        argv = []
    for index, token in enumerate(argv):
        if token == "--out" and index + 1 < len(argv):
            return Path(argv[index + 1])
        if token.startswith("--out="):
            return Path(token[len("--out="):])
    return ARBITER_DEFAULT_OUT_DIR


def arbiter_report_mode(path: Path) -> str | None:
    """The `scan_scope.mode` of the report at `path`: "full" (a baseline or a plain scan) or "partial" (the
    gate's `--changed` run, whose grade is withheld by design). A report without the key is full, which is how
    Arbiter itself reads it; a report that cannot be parsed is None."""
    try:
        report = load_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict):
        return None
    scope = report.get("scan_scope")
    mode = scope.get("mode") if isinstance(scope, dict) else None
    return mode.strip() if isinstance(mode, str) and mode.strip() else "full"


def arbiter_reports_root(out_dir: Path) -> Path:
    """The directory the posture reads reports from. The gate writes under its `--out` (`arbiter-out/omni-gate/`)
    and `omni arbiter baseline` beside it (`arbiter-out/baseline/`), so the root is the `--out` directory's
    parent when it has one, read together with everything one level below it; a top-level `--out` is read
    as is."""
    parent = out_dir.parent
    return out_dir if parent in (Path("."), out_dir) else parent


def newest_arbiter_report(out_dir: Path, mode: str | None = None) -> Path | None:
    """The most recently written `report.json` directly in `out_dir` or one level below it (baseline/,
    omni-gate/), by mtime. With `mode` ("full" or "partial"), the newest whose `scan_scope.mode` is that one;
    a report that cannot be parsed matches either mode, so a corrupt newest report is shown, not skipped."""
    candidates = [path for path in [out_dir / "report.json", *out_dir.glob("*/report.json")] if path.is_file()]
    for path in sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True):
        if mode is None or arbiter_report_mode(path) in (mode, None):
            return path
    return None


def _arbiter_timestamp(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def arbiter_report_freshness(report: dict[str, Any], head: str, changed: set[str]) -> tuple[bool, str]:
    """A report is fresh when it scanned the commit HEAD is at and nothing in `changed` (the gate's changed
    paths) was modified after it started. A path that no longer exists cannot be newer than the scan."""
    repos = report.get("repos") if isinstance(report, dict) else None
    first = repos[0] if isinstance(repos, list) and repos and isinstance(repos[0], dict) else {}
    commit = str(first.get("commit") or "").strip()
    if not commit:
        return False, "report names no commit"
    if not head or not head.startswith(commit):
        return False, f"scanned {commit}, HEAD is {head[:7] or 'unknown'}"
    started = _arbiter_timestamp(report.get("started_at"))
    if started is None:
        return False, "report has no readable started_at"
    newest_path, newest_mtime = None, started + ARBITER_FRESHNESS_TOLERANCE_S
    for path in sorted(changed):
        try:
            mtime = Path(path).stat().st_mtime
        except OSError:
            continue
        if mtime > newest_mtime:
            newest_path, newest_mtime = path, mtime
    if newest_path is not None:
        return False, f"{newest_path} changed after the scan started"
    return True, "fresh"


def arbiter_report_summary(report: dict[str, Any]) -> dict[str, Any]:
    """The headline numbers of a report: Arbiter's scorecard carries `overall` (None when the grade was
    withheld) and `coverage`; findings carry `severity`, `status` (new|existing) and `suppressed`."""
    scorecard = report.get("scorecard") if isinstance(report.get("scorecard"), dict) else {}
    gate = report.get("gate") if isinstance(report.get("gate"), dict) else {}
    findings = [item for item in (report.get("findings") or []) if isinstance(item, dict)]
    live = [
        item for item in findings
        if not item.get("suppressed") and str(item.get("severity", "")).lower() in ARBITER_HIGH_OR_ABOVE
    ]
    score = scorecard.get("overall")
    withheld = bool(scorecard.get("withheld")) or not isinstance(score, (int, float))
    passed = gate.get("passed")
    return {
        "grade": "withheld" if withheld else f"{float(score):g}",
        "score": None if withheld else score,
        "coverage": scorecard.get("coverage") if isinstance(scorecard.get("coverage"), (int, float)) else None,
        "new_high_or_above": sum(1 for item in live if item.get("status") == "new"),
        "existing_high_or_above": sum(1 for item in live if item.get("status") != "new"),
        "gate_passed": passed if isinstance(passed, bool) else None,
        "gate_reasons": [str(reason) for reason in (gate.get("reasons") or []) if isinstance(gate.get("reasons"), list)],
    }


def _path_under(path: str, directory: Path) -> bool:
    parts = Path(path).parts
    return parts[: len(directory.parts)] == directory.parts


def arbiter_rule_scope(rule: dict[str, Any], changed: set[str]) -> set[str]:
    """The changed paths the Arbiter rule would actually scan: those matching its `when_changed`
    globs minus its `ignore` globs. Freshness is judged against these alone, so editing the
    registry, the changelog or a document after the last gate run (which completing a requirement
    always does) never marks a report stale; a newer source file still does."""
    validation = rule.get("validation") if isinstance(rule, dict) else None
    validation = validation if isinstance(validation, dict) else {}
    when = [str(p) for p in validation.get("when_changed", ["**"])]
    ignore = [str(p) for p in validation.get("ignore", [])]
    return {p for p in changed if matches_any(p, when) and not matches_any(p, ignore)}


def _arbiter_report_block(path: Path | None, out_dir: Path, head: str, changed: set[str]) -> dict[str, Any]:
    """One report's share of the posture: whether it exists, where, when it started, its headline numbers and
    whether it still describes HEAD (judged against `changed`, the rule's scope). A malformed report never
    raises; it is recorded as present but not fresh, with the reason."""
    block: dict[str, Any] = {
        "present": False, "path": None, "started_at": None,
        "fresh": False, "reason": f"no report under {out_dir.as_posix()}",
        "grade": None, "score": None, "coverage": None,
        "new_high_or_above": 0, "existing_high_or_above": 0,
        "gate_passed": None, "gate_reasons": [],
    }
    if path is None:
        return block
    block["present"] = True
    block["path"] = path.as_posix()
    try:
        report = load_json(path)
        if not isinstance(report, dict):
            raise ValueError("report is not a JSON object")
        block.update(arbiter_report_summary(report))
        started = report.get("started_at")
        block["started_at"] = started if isinstance(started, str) and started.strip() else None
        block["fresh"], block["reason"] = arbiter_report_freshness(report, head, changed)
    except (OSError, ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
        block["fresh"], block["reason"] = False, f"unreadable: {exc}"
    return block


def arbiter_report_state() -> dict[str, Any]:
    """The Arbiter part of the posture (REQ-046). Two reports are read, not one: `full` is the newest report
    whose `scan_scope.mode` is full (`omni arbiter baseline`, a plain `arbiter scan`), the only kind that
    carries a grade, real coverage and the existing high+ count; `gate` is the newest partial report (the
    gate's `--changed` run), which carries the verdict on the change, the new high+ count and the freshness
    judged against the rule's scope. Each block is present/absent on its own, so a missing full scan never
    hides a fresh gate and vice versa.

    Compatibility: the schema_version 1 keys (`present`, `fresh`, `reason`, `grade`, `coverage`,
    `new_high_or_above`, `gate_passed`, ...) stay at the top level for one release, filled from the gate
    block, which is what they always described; `wired` is unchanged."""
    rule = arbiter_gate_rule()
    root = arbiter_reports_root(arbiter_out_dir(rule))
    empty = _arbiter_report_block(None, root, "", set())
    state: dict[str, Any] = {"wired": rule is not None, "full": dict(empty), "gate": dict(empty)}
    if rule is None:
        state.update(empty)
        state["reason"] = "not wired"
        return state
    head = (git_run("rev-parse", "HEAD") or "").strip()
    changed = arbiter_rule_scope(rule, {p for p in gate_changed_paths(gate_base_commit()) if not _path_under(p, root)})
    state["full"] = _arbiter_report_block(newest_arbiter_report(root, "full"), root, head, changed)
    state["gate"] = _arbiter_report_block(newest_arbiter_report(root, "partial"), root, head, changed)
    state.update(state["gate"])  # schema_version 1 compatibility, one release (REQ-046)
    return state


def _failures_open_count() -> int:
    try:
        ledger = load_json(FAILURE_LEDGER_PATH)
    except (OSError, json.JSONDecodeError):
        return 0
    items = ledger.get("failures", []) if isinstance(ledger, dict) else []
    return sum(1 for item in items if isinstance(item, dict) and item.get("status") in ("open", "mitigated"))


def _requirements_open_counts() -> dict[str, int]:
    counts = {"pending": 0, "proposed": 0, "blocked": 0, "needs_review": 0}
    try:
        registry = load_json(REQUIREMENTS_PATH)
    except (OSError, json.JSONDecodeError):
        registry = {}
    for item in registry.get("requirements", []) if isinstance(registry, dict) else []:
        status = item.get("status") if isinstance(item, dict) else None
        if status in counts:
            counts[status] += 1
    counts["total"] = sum(counts.values())
    return counts


def compute_posture() -> dict[str, Any]:
    """What `omni doctor` prints on its Posture line and returns under `--json`."""
    return {
        "arbiter": arbiter_report_state(),
        "failures_open": _failures_open_count(),
        "requirements_open": _requirements_open_counts(),
    }


def _arbiter_scan_time_text(started_at: Any) -> str:
    """A report's `started_at` as `YYYY-MM-DD HH:MM` in local time, or `?` when unreadable."""
    stamp = _arbiter_timestamp(started_at)
    if stamp is None:
        return "?"
    return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")


def _format_posture_full(full: dict[str, Any]) -> str:
    """The full scan's share of the Posture line: its grade and coverage, and when it ran."""
    if not full.get("present"):
        return "arbiter no full scan (run ./omni arbiter baseline)"
    reason = str(full.get("reason") or "")
    if full.get("grade") is None and reason.startswith("unreadable:"):
        return f"arbiter full scan {reason}"
    coverage = full.get("coverage")
    coverage_text = f"{round(float(coverage) * 100)}%" if isinstance(coverage, (int, float)) else "?"
    grade = full.get("grade")
    grade_text = "grade withheld" if grade in (None, "withheld") else f"score {grade}"
    return f"arbiter {grade_text} (coverage {coverage_text}, full scan {_arbiter_scan_time_text(full.get('started_at'))})"


def _format_posture_gate(gate: dict[str, Any]) -> str:
    """The gate report's share of the Posture line: its verdict on the change and whether it is still fresh."""
    if not gate.get("present"):
        return "gate no report (run ./omni gate)"
    if not gate.get("fresh"):
        return f"gate stale: {gate.get('reason')}"
    passed = gate.get("gate_passed")
    verdict = "gate passed" if passed is True else ("gate failed" if passed is False else "gate unknown")
    return f"{verdict} (new high+ {gate.get('new_high_or_above', 0)}, fresh)"


def format_posture(posture: dict[str, Any]) -> str:
    """One line: the full scan's grade, the gate report's verdict and freshness, open failures, open
    requirements (REQ-046). Each missing report names the command that produces it."""
    arbiter = posture.get("arbiter") if isinstance(posture.get("arbiter"), dict) else {}
    if not arbiter.get("wired"):
        part = "arbiter not wired"
    else:
        full = arbiter.get("full") if isinstance(arbiter.get("full"), dict) else {}
        gate = arbiter.get("gate") if isinstance(arbiter.get("gate"), dict) else {}
        part = f"{_format_posture_full(full)} \u00b7 {_format_posture_gate(gate)}"
    open_requirements = posture.get("requirements_open") if isinstance(posture.get("requirements_open"), dict) else {}
    buckets = ", ".join(
        f"{status} {open_requirements[status]}"
        for status in ("pending", "proposed", "blocked", "needs_review")
        if open_requirements.get(status)
    )
    requirement_text = f"requirements open {open_requirements.get('total', 0)}" + (f" ({buckets})" if buckets else "")
    return f"Posture: {part} \u00b7 failures open {posture.get('failures_open', 0)} \u00b7 {requirement_text}"


# --- Completion needs a fresh, passing report (REQ-037) ------------------------------------------

ARBITER_GATE_COMMAND = "./omni gate"


def arbiter_executable() -> str | None:
    return shutil.which("arbiter")


def arbiter_completion_block() -> str | None:
    """Why `omni requirement complete` must not proceed yet, or None when it may. Nothing blocks a workspace
    without the Arbiter rule; with it, the first of: `arbiter` not installed, no report under the rule's
    --out directory, a report that no longer describes HEAD, a report whose gate failed. Every message ends
    with the command that produces a fresh report, because `omni gate` is what runs the rule.

    The report judged is the gate's own (the newest partial scan, REQ-046); when there is none, the newest
    full scan stands in, since a full scan at HEAD carries a gate verdict of its own."""
    rule = arbiter_gate_rule()
    if rule is None:
        return None
    if arbiter_executable() is None:
        return f"arbiter is not installed; `omni arbiter install` puts it on PATH, then run {ARBITER_GATE_COMMAND}"
    reports = arbiter_report_state()
    state = reports["gate"] if reports["gate"]["present"] else reports["full"]
    if not state["present"]:
        return f"no Arbiter report under {arbiter_out_dir(rule).as_posix()}; run {ARBITER_GATE_COMMAND}"
    if not state["fresh"]:
        return f"the Arbiter report {state['path']} is stale ({state['reason']}); run {ARBITER_GATE_COMMAND}"
    if state["gate_passed"] is None:
        return f"the Arbiter report {state['path']} carries no gate result; run {ARBITER_GATE_COMMAND}"
    if state["gate_passed"] is False:
        reasons = "; ".join(state["gate_reasons"]) or "no reason recorded"
        return f"the Arbiter gate failed in {state['path']}: {reasons}. Fix the findings, then run {ARBITER_GATE_COMMAND}"
    return None




# ---------------------------------------------------------------------------
# REQ-038: the Arbiter baseline. `arbiter gate --baseline` labels every finding
# new or existing against .arbiter/baseline.json; without the file the gate
# treats everything as new. Adoption cuts one from a full offline scan, and
# `omni arbiter baseline --refresh` re-cuts it once the gate is green.
# ---------------------------------------------------------------------------


def _arbiter_run(command: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    """Run `command` (an argv list, never a shell) in `cwd`; the exit code and the last
    lines of its combined output. A missing binary or a timeout reads as a failure."""
    try:
        completed = subprocess.run(
            command, cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except OSError as exc:
        return 1, str(exc)
    except subprocess.TimeoutExpired:
        return 1, f"timed out after {timeout}s"
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    return completed.returncode, "\n".join(output.strip().splitlines()[-8:])


def _git_short_head(repo_root: Path) -> str | None:
    """`git rev-parse --short HEAD`, or None outside a git checkout."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return (completed.stdout or "").strip() or None


def _git_ignored(repo_root: Path, relative_path: str) -> bool:
    """True when git would ignore the path (exit 0 from `git check-ignore`)."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "check-ignore", "-q", relative_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _arbiter_config_hash(target_root: Path) -> str | None:
    """sha256 of arbiter.yaml's bytes, or None when the project has none."""
    config = target_root / ARBITER_CONFIG_FILE
    return hashlib.sha256(config.read_bytes()).hexdigest() if config.is_file() else None


def _arbiter_gate_rule(root: Path) -> dict[str, Any] | None:
    """The `completion.arbiter_gate` rule in root's completion rulepack, or None."""
    path = root / ".ai" / "rules" / "completion-workflow.json"
    if not path.is_file():
        return None
    try:
        rulepack = load_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    rules = rulepack.get("rules", []) if isinstance(rulepack, dict) else []
    for rule in rules if isinstance(rules, list) else []:
        if isinstance(rule, dict) and rule.get("id") == ARBITER_GATE_RULE_ID:
            return rule
    return None


def _newest_arbiter_gate_report(target_root: Path) -> Path | None:
    """The newest report.json the gate rule wrote under arbiter-out/omni-gate."""
    out_dir = target_root / ARBITER_GATE_OUT_DIR
    if not out_dir.is_dir():
        return None
    reports = sorted(out_dir.rglob("report.json"), key=lambda p: p.stat().st_mtime)
    return reports[-1] if reports else None


def arbiter_write_baseline(
    target_root: Path,
    refresh: bool = False,
    scan_out: str = "arbiter-out/baseline",
    force: bool = False,
    scan_args: tuple[str, ...] = (),
) -> int:
    """Cut `.arbiter/baseline.json` from a full offline scan of `target_root`.

    The baseline is the set of finding ids the project has accepted as known; with
    it, `arbiter gate --baseline` reports only what is new since. It must come from
    a *full* scan (a `--changed` partial report would baseline a slice, so one is
    refused) and is augmented with the commit it was cut at and a sha256 of
    arbiter.yaml, so `omni doctor` can tell when either moved on. The file is meant
    to be committed.

    `refresh` re-cuts an existing baseline and prunes it: `arbiter baseline` writes
    exactly the ids in the report it is given, so a baseline cut from a fresh full
    scan holds only findings that still exist -- anything fixed since the last cut
    drops out. Because a refresh also absorbs every finding currently open, it is
    only allowed once the newest gate report under arbiter-out/omni-gate passed
    (`force` overrides). `scan_args` are appended to the scan command (tests use
    them to skip slow adapters).
    """
    baseline_path = target_root / ARBITER_BASELINE_PATH
    if baseline_path.is_file() and not refresh:
        print(f"- {ARBITER_BASELINE_PATH}: already present (pass --refresh to re-cut it after a green gate)")
        return 0

    if refresh:
        report_path = _newest_arbiter_gate_report(target_root)
        passed = None
        if report_path is not None:
            try:
                gate = load_json(report_path).get("gate")
                passed = gate.get("passed") if isinstance(gate, dict) else None
            except (OSError, json.JSONDecodeError, AttributeError):
                passed = None
        if passed is not True:
            where = report_path.relative_to(target_root).as_posix() if report_path else f"{ARBITER_GATE_OUT_DIR}/report.json"
            state = "is missing" if report_path is None else ("did not pass" if passed is False else "has no gate verdict")
            if not force:
                print(
                    f"- {ARBITER_BASELINE_PATH}: refusing to refresh, the last gate report ({where}) {state}. "
                    "A refresh absorbs every open finding as known, so get the gate green first "
                    "(`omni gate`), or pass --force to accept them anyway."
                )
                return 1
            print(f"- {ARBITER_BASELINE_PATH}: the last gate report ({where}) {state}; refreshing anyway (--force)")

    if shutil.which("arbiter") is None:
        print(f"- {ARBITER_BASELINE_PATH}: `arbiter` is not on PATH; `omni arbiter install`, then rerun")
        return 1

    scan = ["arbiter", "scan", ".", "--profile", "offline", "--out", scan_out, "--format", "json", *scan_args]
    print(f"- scan: {' '.join(scan)} (a full offline scan; this can take a while)")
    code, tail = _arbiter_run(scan, target_root, 1800)
    if code != 0:
        print(f"  scan failed (exit {code}):\n{tail}", file=sys.stderr)
        return 1
    report_path = target_root / scan_out / "report.json"
    if not report_path.is_file():
        print(f"  scan wrote no {scan_out}/report.json", file=sys.stderr)
        return 1
    try:
        report = load_json(report_path)
    except json.JSONDecodeError as exc:
        print(f"  {scan_out}/report.json is not valid JSON: {exc}", file=sys.stderr)
        return 1
    scope = report.get("scan_scope") if isinstance(report, dict) else None
    mode = scope.get("mode") if isinstance(scope, dict) else None
    if mode != "full":
        print(
            f"- {ARBITER_BASELINE_PATH}: refusing to baseline a report whose scan_scope.mode is "
            f"{mode!r}; a baseline must come from a full scan, not a --changed slice",
            file=sys.stderr,
        )
        return 1

    write = ["arbiter", "baseline", f"{scan_out}/report.json", "--out", ARBITER_BASELINE_PATH]
    print(f"- baseline: {' '.join(write)}")
    code, tail = _arbiter_run(write, target_root, 120)
    if code != 0 or not baseline_path.is_file():
        print(f"  arbiter baseline failed (exit {code}):\n{tail}", file=sys.stderr)
        return 1
    try:
        baseline = load_json(baseline_path)
    except json.JSONDecodeError as exc:
        print(f"  {ARBITER_BASELINE_PATH} is not valid JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(baseline, dict):
        print(f"  {ARBITER_BASELINE_PATH} is not a JSON object", file=sys.stderr)
        return 1
    commit = _git_short_head(target_root)
    if commit:
        baseline["commit"] = commit
    config_hash = _arbiter_config_hash(target_root)
    if config_hash:
        baseline["config_hash"] = config_hash
    write_json(baseline_path, baseline)

    ids = baseline.get("ids") if isinstance(baseline.get("ids"), list) else []
    pruned = " (refreshed: only ids the fresh full scan still reports)" if refresh else ""
    print(f"- {ARBITER_BASELINE_PATH}: {len(ids)} known finding id(s) at {commit or 'no commit'}{pruned}")
    if _git_ignored(target_root, ARBITER_BASELINE_PATH):
        print(f"  warning: git ignores {ARBITER_BASELINE_PATH}; remove the `.arbiter/` ignore so it can be committed")
    print(f"  commit {ARBITER_BASELINE_PATH} so the gate means new since this baseline")
    return 0


def run_arbiter_baseline(args: argparse.Namespace) -> int:
    target_root = Path(args.target).resolve()
    if not target_root.is_dir():
        print(f"Target is not a directory: {target_root}", file=sys.stderr)
        return 1
    print(f"Arbiter baseline for {target_root}")
    print(f"Mode: {'refresh' if args.refresh else 'initial'}{' (force)' if args.force else ''}")
    print("")
    return arbiter_write_baseline(target_root, refresh=args.refresh, force=args.force)


def _newest_fixed_failure() -> tuple[str, str] | None:
    """(id, date) of the most recently dated `fixed` entry in the failure ledger."""
    if not failure_ledger_path().is_file():
        return None
    try:
        ledger = load_failure_ledger()
    except (OSError, json.JSONDecodeError):
        return None
    newest: tuple[str, str] | None = None
    for entry in ledger.get("failures", []) if isinstance(ledger, dict) else []:
        if not isinstance(entry, dict) or entry.get("status") != "fixed":
            continue
        date = entry.get("date")
        if isinstance(date, str) and date and (newest is None or date > newest[1]):
            newest = (str(entry.get("id") or "?"), date)
    return newest


def validate_arbiter_baseline(report: DoctorReport) -> None:
    """Once the gate rule is wired, the baseline must exist and still describe this
    project: cut before the newest fixed failure it may carry ids that no longer exist,
    and cut under a different arbiter.yaml it assumed a different policy. Both are
    warnings, since the gate still runs; a refresh on a green gate clears them."""
    if _arbiter_gate_rule(Path(".")) is None:
        return
    baseline_path = Path(ARBITER_BASELINE_PATH)
    if not baseline_path.is_file():
        report.warning(
            f"{ARBITER_BASELINE_PATH} is missing, so the gate treats every finding as new; "
            "run `omni arbiter baseline` and commit the file"
        )
        return
    try:
        baseline = load_json(baseline_path)
    except json.JSONDecodeError as exc:
        report.warning(f"{ARBITER_BASELINE_PATH} is not valid JSON ({exc}); re-cut it with `omni arbiter baseline --refresh`")
        return
    if not isinstance(baseline, dict):
        report.warning(f"{ARBITER_BASELINE_PATH} is not a JSON object; re-cut it with `omni arbiter baseline --refresh`")
        return
    problems = 0
    created = str(baseline.get("created") or "")
    newest = _newest_fixed_failure()
    if created and newest and created[:10] < newest[1][:10]:
        report.warning(
            f"{ARBITER_BASELINE_PATH} was cut on {created[:10]}, before {newest[0]} was fixed on {newest[1]}; "
            "it may still list findings that no longer exist. Refresh it on a green gate: "
            "`omni arbiter baseline --refresh`"
        )
        problems += 1
    recorded = baseline.get("config_hash")
    current = _arbiter_config_hash(Path("."))
    if isinstance(recorded, str) and current and recorded != current:
        report.warning(
            f"{ARBITER_CONFIG_FILE} changed since {ARBITER_BASELINE_PATH} was cut "
            f"(config_hash {recorded[:12]} recorded, {current[:12]} now), so the baseline assumed a "
            "different policy than the gate runs. Refresh it on a green gate: `omni arbiter baseline --refresh`"
        )
        problems += 1
    if not problems:
        ids = baseline.get("ids") if isinstance(baseline.get("ids"), list) else []
        report.pass_check(f"{ARBITER_BASELINE_PATH} is present and current ({len(ids)} known finding ids)")


# ---------------------------------------------------------------------------
# REQ-042: Arbiter version tracking and `omni arbiter update`. The installed
# version and its source are recorded beside the template ref in
# .ai/omni-version.json; doctor warns when the installed package drifts from
# the record, `omni update` warns when the adopter's gate rule drifts from the
# template, and `omni arbiter update` brings all of it level in one go.
# ---------------------------------------------------------------------------

_VERSION_TOKEN = re.compile(r"\d+(?:\.\d+)+[0-9A-Za-z.+-]*")


def installed_arbiter_version() -> str | None:
    """The installed Arbiter's version: the last version-shaped token `arbiter --version`
    prints, else the `arbiter-eval` distribution's metadata, else None."""
    if shutil.which("arbiter") is not None:
        try:
            completed = subprocess.run(
                ["arbiter", "--version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        if completed is not None and completed.returncode == 0:
            for token in reversed(((completed.stdout or "") + " " + (completed.stderr or "")).split()):
                if _VERSION_TOKEN.fullmatch(token):
                    return token
    # Imported here so the module's import block stays as it is; stdlib either way.
    from importlib import metadata
    try:
        return metadata.version("arbiter-eval")
    except metadata.PackageNotFoundError:
        return None


def record_arbiter_version(target_root: Path, source: str) -> None:
    """Merge `{"arbiter": {source, version, recorded_at}}` into .ai/omni-version.json.
    The file is only written when it exists: `omni update` refuses one without a
    template `ref`, so creating it here would leave the adopter worse off."""
    path = target_root / OMNI_VERSION_FILE
    if not path.is_file():
        print(f"- {OMNI_VERSION_FILE}: absent, Arbiter version not recorded (`omni adopt` writes the file)")
        return
    try:
        payload = load_json(path)
    except json.JSONDecodeError:
        print(f"- {OMNI_VERSION_FILE}: not valid JSON, Arbiter version not recorded")
        return
    if not isinstance(payload, dict):
        print(f"- {OMNI_VERSION_FILE}: not a JSON object, Arbiter version not recorded")
        return
    version = installed_arbiter_version() or "unknown"
    payload["arbiter"] = {
        "source": source,
        "version": version,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    write_json(path, payload)
    print(f"- {OMNI_VERSION_FILE}: recorded arbiter {version} from {source}")


def validate_arbiter_version(report: DoctorReport) -> None:
    """When the version file records an Arbiter and one is installed, the two must agree."""
    info = read_omni_version_file()
    recorded = info.get("arbiter") if isinstance(info, dict) else None
    if not isinstance(recorded, dict) or shutil.which("arbiter") is None:
        return
    installed = installed_arbiter_version()
    if installed is None:
        return
    wanted = str(recorded.get("version") or "unknown")
    if wanted != installed:
        report.warning(
            f"Arbiter drift: {OMNI_VERSION_FILE} recorded {wanted}, installed {installed}: "
            "run `omni arbiter sync` (levels the subtree or package, the record and the baseline) "
            "or `omni arbiter update` (upgrades, refreshes the gate rule and re-records the version)"
        )
    else:
        report.pass_check(f"Installed Arbiter {installed} matches {OMNI_VERSION_FILE}")


def warn_arbiter_rule_drift(target_root: Path) -> bool:
    """Print a note when the adopter's `completion.arbiter_gate` runs a different command
    than the current template. Warn only: the rule is adopter-owned, `omni update` never
    rewrites it; `omni arbiter update` does on request. Returns True when it warned."""
    rule = _arbiter_gate_rule(target_root)
    if rule is None:
        return False
    validation = rule.get("validation")
    current = validation.get("run") if isinstance(validation, dict) else None
    template = ARBITER_GATE_RULE["validation"]["run"]
    if current == template:
        return False
    print("")
    print(
        f"Note: `{ARBITER_GATE_RULE_ID}` in .ai/rules/completion-workflow.json runs a different "
        "command than the current template; the rule is yours, so it was left alone. "
        "`omni arbiter update` rewrites it to the template (`--keep-rule` keeps yours):"
    )
    print(f"  yours:    {current}")
    print(f"  template: {template}")
    return True


def _arbiter_git_source(source: str) -> str:
    """What `git subtree pull` fetches from: a pip `git+URL` minus the prefix, a local
    checkout as its absolute path, anything else as given."""
    if source.startswith("git+"):
        return source[4:]
    local = Path(source).expanduser()
    if local.is_dir():
        return str(local.resolve())
    return source


def arbiter_subtree_mode(target_root: Path, prefix: str = "arbiter") -> str | None:
    """How `<prefix>/` was brought in: "unsquashed", "squash", or None without a subtree.

    `git subtree` (contrib/subtree/git-subtree.sh) marks commits with a
    `git-subtree-dir: <prefix>` trailer in two shapes. An unsquashed `add` (and
    `split --rejoin`) writes a merge commit carrying `git-subtree-mainline: <sha>`
    beside `git-subtree-split: <sha>`; a later unsquashed `pull` is a plain
    `git merge -Xsubtree` with no trailers at all, so the newest marker commit stays
    that add. `--squash` instead writes a synthetic commit titled
    "Squashed '<prefix>/' content from commit X" (add) or
    "Squashed '<prefix>/' changes from X..Y" (pull) that carries `git-subtree-dir`
    and `git-subtree-split` but never `git-subtree-mainline`. So the newest commit
    whose message holds the exact `git-subtree-dir: <prefix>` line decides: with a
    mainline trailer the history is unsquashed, without one it is squashed. Mixing
    the two modes makes later pulls replay or conflict on the whole subtree history,
    which is why `omni arbiter update` refuses a mismatch without --force.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(target_root), "log", "-1", f"--grep=^git-subtree-dir: {prefix}$", "--format=%B"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    lines = [line.strip() for line in (completed.stdout or "").splitlines()]
    if f"git-subtree-dir: {prefix}" not in lines:
        return None
    if any(line.startswith("git-subtree-mainline:") for line in lines):
        return "unsquashed"
    if any(line.startswith("git-subtree-split:") for line in lines):
        return "squash"
    return None


def run_arbiter_update(args: argparse.Namespace) -> int:
    """Bring an adopted workspace's Arbiter level: pip --upgrade from the recorded source,
    the gate rule's `validation` to the template, a vendored arbiter/ subtree pulled in
    the mode it was added with, and the version re-recorded. --dry-run prints every
    command and writes nothing."""
    target_root = Path(args.target).resolve()
    if not target_root.is_dir():
        print(f"Target is not a directory: {target_root}", file=sys.stderr)
        return 1
    info = read_omni_version_file(target_root)
    recorded = info.get("arbiter") if isinstance(info, dict) else None
    recorded = recorded if isinstance(recorded, dict) else {}
    source = args.source or str(recorded.get("source") or "") or ARBITER_DEFAULT_SOURCE
    dry_run = args.dry_run
    verb = "would " if dry_run else ""
    status = 0

    print(f"Arbiter update in {target_root}")
    print(f"Source: {source}{' (recorded at install)' if not args.source and recorded.get('source') else ''}")
    print(f"Recorded version: {recorded.get('version') or 'none'}")
    print(f"Mode: {'dry-run' if dry_run else 'apply'}")
    print("")

    if args.skip_pip:
        print("- pip: skipped (--skip-pip)")
    else:
        command = _arbiter_pip_command(source) + ["--upgrade"]
        print(f"- pip: {verb}run {' '.join(command)}")
        if not dry_run:
            code, tail = _arbiter_run(command, target_root, 1800)
            if code != 0:
                print(f"  pip failed (exit {code}):\n{tail}", file=sys.stderr)
                status = 1

    rulepack_path = target_root / ".ai" / "rules" / "completion-workflow.json"
    if args.keep_rule:
        print(f"- completion rulepack: `{ARBITER_GATE_RULE_ID}` kept as it is (--keep-rule)")
    elif not rulepack_path.is_file():
        print(f"- {rulepack_path.relative_to(target_root).as_posix()}: missing; adopt the workspace first")
        status = 1
    else:
        rulepack = load_json(rulepack_path)
        rules = rulepack.setdefault("rules", []) if isinstance(rulepack, dict) else []
        rule = next((r for r in rules if isinstance(r, dict) and r.get("id") == ARBITER_GATE_RULE_ID), None)
        if rule is None:
            print(f"- completion rulepack: `{ARBITER_GATE_RULE_ID}` is not wired; run `omni arbiter install`")
        elif rule.get("validation") == ARBITER_GATE_RULE["validation"]:
            print(f"- completion rulepack: `{ARBITER_GATE_RULE_ID}` already runs the template command")
        else:
            rule["validation"] = json.loads(json.dumps(ARBITER_GATE_RULE["validation"]))
            print(f"- completion rulepack: {verb}rewrite `{ARBITER_GATE_RULE_ID}` validation to the template (--keep-rule keeps yours)")
            if not dry_run:
                write_json(rulepack_path, rulepack)

    vendored = target_root / "arbiter"
    if (vendored / "pyproject.toml").is_file() and (vendored / ".ai" / "omni-version.json").is_file():
        mode = arbiter_subtree_mode(target_root)
        wanted = "squash" if args.squash else "unsquashed"
        command = ["git", "subtree", "pull", "--prefix", "arbiter", _arbiter_git_source(source), "main"]
        if args.squash:
            command.append("--squash")
        if mode is not None and mode != wanted and not args.force:
            fix = "add --squash" if mode == "squash" else "drop --squash"
            print(
                f"- subtree arbiter/: refusing to pull, its history is {mode} but this run asks for {wanted}; "
                f"mixing the two replays the whole subtree history. {fix[0].upper()}{fix[1:]}, or --force to run anyway:"
            )
            print(f"    {' '.join(command)}")
            status = 1
        else:
            note = f" (history is {mode})" if mode else " (no subtree marker commit found)"
            print(f"- subtree arbiter/: {verb}run {' '.join(command)}{note}")
            if not dry_run:
                code, tail = _arbiter_run(command, target_root, 600)
                if code != 0:
                    print(f"  git subtree pull failed (exit {code}):\n{tail}", file=sys.stderr)
                    status = 1
    else:
        print("- subtree arbiter/: not vendored here, skipped")

    if dry_run:
        print(f"- {OMNI_VERSION_FILE}: would re-record the installed Arbiter version and its source")
    else:
        record_arbiter_version(target_root, source)

    print("")
    print("Next: run `omni arbiter baseline --refresh` after the gate is green, so the baseline")
    print("matches what the upgraded Arbiter reports; `omni doctor` confirms the versions agree.")
    return status


def _omni_cli_argv() -> list[str]:
    """How a subprocess runs this CLI: the `omni` script beside this module under the interpreter running
    now (so an adopted repository, where `omni` is not on PATH, still works), else this module itself."""
    here = Path(__file__).resolve().parent
    script = here / "omni"
    return [sys.executable, str(script if script.is_file() else Path(__file__).resolve())]


def _arbiter_sync_git_source(target_root: Path, source: str) -> str:
    """What `git subtree pull` fetches from: the pip source minus its `git+`, unless that is a local
    path inside the target (the vendored subtree itself, which `adopt --with-arbiter ./arbiter` records)
    or no directory at all; then Arbiter's upstream."""
    git_source = _arbiter_git_source(source)
    local = Path(git_source)
    if local.is_dir():
        resolved = local.resolve()
        if resolved == target_root or target_root in resolved.parents:
            return _arbiter_git_source(ARBITER_DEFAULT_SOURCE)
    elif not source.startswith(("git+", "http://", "https://", "ssh://", "git@")):
        return _arbiter_git_source(ARBITER_DEFAULT_SOURCE)
    return git_source


def run_arbiter_sync(args: argparse.Namespace) -> int:
    """Level the vendored subtree, the installed package, the recorded version and the baseline in one
    go (REQ-049). Subtree mode (arbiter/pyproject.toml present): git subtree pull, pip install -e
    ./arbiter[mcp], the version record, `omni arbiter baseline` (--refresh when one exists) unless
    --skip-baseline, then `omni doctor`. Pip mode: pip --upgrade from the recorded source, then the
    same tail. Every step is printed before it runs; the first non-zero exit stops the sequence naming
    the step and what to do; --dry-run prints the commands only."""
    target_root = Path(args.target).resolve()
    if not target_root.is_dir():
        print(f"Target is not a directory: {target_root}", file=sys.stderr)
        return 1
    info = read_omni_version_file(target_root)
    recorded = info.get("arbiter") if isinstance(info, dict) else None
    recorded = recorded if isinstance(recorded, dict) else {}
    recorded_source = str(recorded.get("source") or "")
    source = args.source or recorded_source or ARBITER_DEFAULT_SOURCE
    origin = "given" if args.source else ("recorded at install" if recorded_source else "default")
    branch = str(getattr(args, "branch", None) or "main")
    subtree = (target_root / "arbiter" / "pyproject.toml").is_file()
    dry_run = bool(args.dry_run)
    omni = _omni_cli_argv()

    steps: list[dict[str, Any]] = []
    if subtree:
        git_source = _arbiter_sync_git_source(target_root, source)
        pull = ["git", "subtree", "pull", "--prefix=arbiter", git_source, branch, "-m", f"Pull Arbiter {branch} into arbiter/"]
        mode = arbiter_subtree_mode(target_root)
        if mode == "squash":
            pull.append("--squash")
        steps.append({
            "name": "subtree pull", "argv": pull, "timeout": 600,
            "advice": "Resolve the conflict in arbiter/ (`git status` lists the files), commit the merge, and rerun `omni arbiter sync`.",
        })
        steps.append({
            "name": "pip install", "argv": [sys.executable, "-m", "pip", "install", "-e", f"./arbiter[{ARBITER_PIP_EXTRAS}]"], "timeout": 1800,
            "advice": "Fix the install error above (the interpreter running omni must be able to install into its environment), then rerun `omni arbiter sync`.",
        })
        # The record names the upstream, never the subtree path, so the next sync knows where to pull from.
        record_source = source if source.startswith(("git+", "http://", "https://", "ssh://", "git@")) else ARBITER_DEFAULT_SOURCE
    else:
        steps.append({
            "name": "pip upgrade", "argv": _arbiter_pip_command(source) + ["--upgrade"], "timeout": 1800,
            "advice": "Fix the install error above, or pass --source with the checkout or URL to install from, then rerun `omni arbiter sync`.",
        })
        record_source = source
    steps.append({"name": "record version", "call": lambda: record_arbiter_version(target_root, record_source) or 0,
                  "describe": f"re-record the installed Arbiter version and its source in {OMNI_VERSION_FILE}"})
    if getattr(args, "skip_baseline", False):
        steps.append({"name": "baseline", "skipped": "--skip-baseline"})
    else:
        baseline = omni + ["arbiter", "baseline"] + (["--refresh"] if (target_root / ARBITER_BASELINE_PATH).is_file() else [])
        steps.append({
            "name": "baseline", "argv": baseline, "timeout": 2400,
            "advice": "Get the gate green (`omni gate`), then `omni arbiter baseline --refresh` (`--force` accepts the open findings as known), then `omni doctor`.",
        })
    steps.append({
        "name": "doctor", "argv": omni + ["doctor"], "timeout": 600,
        "advice": "Fix the errors doctor lists above, then rerun `omni doctor`; the other steps are done and need not be repeated.",
    })

    print(f"Arbiter sync in {target_root} ({'subtree mode: arbiter/pyproject.toml present' if subtree else 'pip mode: no vendored arbiter/'})")
    print(f"Source: {source} ({origin})" + (f"; subtree pulled from {steps[0]['argv'][4]}" if subtree else ""))
    if subtree:
        print(f"Branch: {branch}")
    print(f"Mode: {'dry-run (commands printed, nothing run or written)' if dry_run else 'apply'}")
    print("")
    total = len(steps)
    for number, step in enumerate(steps, 1):
        label = f"[{number}/{total}] {step['name']}"
        if step.get("skipped"):
            print(f"{label}: skipped ({step['skipped']})")
            continue
        if "call" in step:
            if dry_run:
                print(f"{label}: would {step['describe']}")
                continue
            print(f"{label}: {step['describe']}")
            step["call"]()
            continue
        command = " ".join(shlex.quote(part) for part in step["argv"])
        if dry_run:
            print(f"{label}: would run {command}")
            continue
        print(f"{label}: {command}")
        started = time.monotonic()
        code, tail = _arbiter_run(step["argv"], target_root, step["timeout"])
        elapsed = time.monotonic() - started
        if code != 0:
            print(f"omni arbiter sync: step {number}/{total} ({step['name']}) failed with exit {code} after {elapsed:.1f}s:", file=sys.stderr)
            for line in tail.splitlines():
                print(f"    {line}", file=sys.stderr)
            print(step["advice"], file=sys.stderr)
            return 1
        print(f"    done ({elapsed:.1f}s)")
    print("")
    print("Arbiter sync complete." if not dry_run else "Dry run complete; rerun without --dry-run to apply.")
    return 0


def run_update(args: argparse.Namespace) -> int:
    source_root = Path(args.source).resolve()
    target_root = Path(".").resolve()

    if not source_root.is_dir():
        print(f"Update source is not a directory: {source_root}", file=sys.stderr)
        return 1
    if source_root == target_root:
        print("Refusing to update a workspace from itself.", file=sys.stderr)
        return 1

    if args.bootstrap:
        current_ref = git_current_ref(source_root)
        if current_ref is None:
            print(f"Update source is not a git checkout: {source_root}", file=sys.stderr)
            return 1
        write_omni_version_file(source_root, target_root)
        print(f"Bootstrapped {target_root / OMNI_VERSION_FILE} against {source_root} @ {current_ref[:12]}.")
        print(
            "No files were merged -- this only records today as the starting point for "
            "future merges. Run `omni update --source ...` (without --bootstrap) next "
            "time to pull in template changes made after this point."
        )
        return 0

    version_info = read_omni_version_file(target_root)
    if version_info is None:
        print(
            f"No {OMNI_VERSION_FILE} found in this workspace -- it was adopted before "
            "update-tracking existed. Run this once to start tracking (merges nothing, "
            "just records today as the baseline), then use `omni update` normally from "
            f"now on:\n\n  omni update --source {args.source} --bootstrap\n",
            file=sys.stderr,
        )
        return 1

    base_ref = version_info.get("ref")
    if not base_ref:
        print(
            f"{OMNI_VERSION_FILE} has no recorded ref (the adoption source wasn't a git "
            "checkout at last sync), so omni update cannot reconstruct a merge base. "
            "Set the ref by hand or re-adopt from a git checkout of OmniEngineering.",
            file=sys.stderr,
        )
        return 1

    current_ref = git_current_ref(source_root)
    if current_ref is None:
        print(f"Update source is not a git checkout: {source_root}", file=sys.stderr)
        return 1

    files = list(TEMPLATE_MANAGED_FILES)
    if args.include_legal:
        files.extend(ADOPTION_LEGAL_FILES)
    if args.include_presentation:
        files.extend(ADOPTION_PRESENTATION_FILES)

    print(f"OmniEngineering update source: {source_root} @ {current_ref[:12]}")
    print(f"Base ref (last sync): {base_ref[:12]}")
    print(f"Mode: {'dry-run' if args.dry_run else 'apply'}")
    print("")

    conflicts: list[str] = []
    make_ai_changed = False
    for relative_path in files:
        source_path = source_root / relative_path
        target_path = target_root / relative_path

        theirs = source_path.read_text(encoding="utf-8") if source_path.is_file() else None
        base = git_show_file(source_root, base_ref, relative_path)
        ours = target_path.read_text(encoding="utf-8") if target_path.is_file() else None

        if theirs is None and ours is None:
            continue
        if theirs is None:
            print(f"- removed upstream (review manually, not auto-deleted): {relative_path}")
            continue

        if ours is None:
            outcome = "added"
        elif ours == theirs:
            outcome = "already matches"
        elif base is not None and ours == base:
            outcome = "updated"
        elif base is not None and theirs == base:
            outcome = "unchanged upstream"
        else:
            merged, clean = three_way_merge(ours, base or "", theirs)
            theirs = merged
            if clean:
                outcome = "merged"
            else:
                outcome = "conflict: resolve manually"
                conflicts.append(relative_path)

        print(f"- {outcome}: {relative_path}")

        if args.dry_run or outcome in {"already matches", "unchanged upstream"}:
            continue

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(theirs, encoding="utf-8")
        if relative_path == "make_ai.py" and outcome in {"added", "updated", "merged"}:
            make_ai_changed = True

    if args.dry_run:
        print("")
        print("Dry run: no files written.")
        return 1 if conflicts else 0

    write_omni_version_file(source_root, target_root)
    warn_arbiter_rule_drift(target_root)

    make_ai_path = target_root / "make_ai.py"
    if make_ai_changed and make_ai_path.is_file():
        print("")
        print("make_ai.py changed -- re-running sync in a fresh process so the new")
        print("templates regenerate entrypoint/pointer/ignore files correctly.")
        subprocess.run([sys.executable, str(make_ai_path), "sync"], cwd=target_root)

    if conflicts:
        print("")
        print("Conflicts need manual resolution (look for <<<<<<< markers):")
        for relative_path in conflicts:
            print(f"- {relative_path}")
        if "make_ai.py" in conflicts:
            print(
                "make_ai.py has an unresolved conflict, so sync was not re-run -- "
                "the file isn't valid until you resolve it. Run `omni sync` yourself "
                "once it's fixed."
            )
        return 1

    print("")
    print("Update complete.")
    return 0


def run_requirement_add(args: argparse.Namespace) -> int:
    path = Path(".ai/requirements/requirements.json")
    requirements = load_json(path)
    requirement_id = args.id or next_requirement_id(requirements)

    new_requirement = {
        "id": requirement_id,
        "category": args.category,
        "title": args.title,
        "description": args.description,
        "priority": args.priority,
        "status": args.status,
        "minimum_access_scope": split_csv(args.scope),
        "acceptance_criteria": split_csv(args.acceptance),
        "validation_required": split_csv(args.validation),
        "documentation_required": split_csv(args.docs),
        "risk_notes": split_csv(args.risks),
    }

    existing_ids = all_requirement_ids()
    if requirement_id in existing_ids:
        print(f"Requirement already exists: {requirement_id}", file=sys.stderr)
        return 1

    requirements.setdefault("requirements", []).append(new_requirement)
    write_json(path, requirements)
    print(f"Added requirement {requirement_id}: {args.title}")
    return 0


def infer_draft_category(paths: list[str]) -> str:
    """A deterministic, no-model guess at a requirement category from the files a change touched.

    It only has to be a reasonable starting point: `omni requirement draft` always leaves the entry in
    `proposed` status, so a human corrects the category (and everything else) before it is trusted."""
    lowered = [p.lower() for p in paths]
    if not lowered:
        return "Process"
    if all(p.endswith(".md") or p.endswith(".txt") for p in lowered):
        return "Documentation"
    if any(p.startswith(("tests/", "test/")) or "/tests/" in p or "/test/" in p or Path(p).name.startswith(("test_", "test-")) for p in lowered):
        return "Testing"
    if any(p.startswith((".github/workflows/", ".githooks/")) or Path(p).name in ("dockerfile", "docker-compose.yml", "docker-compose.yaml") for p in lowered):
        return "Process"
    if any("fix" in p or "bug" in p or "hotfix" in p for p in lowered):
        return "Defect"
    return "Feature"


def draft_changed_paths(commit: str | None) -> list[str] | None:
    """The files a draft should be scoped to: one named commit, or everything `omni gate` would currently check."""
    if commit:
        top = git_run("rev-parse", "--show-toplevel")
        if top is None:
            return None
        # --root: without it, diff-tree shows nothing for a repository's first commit (it has no parent to diff against).
        listing = git_run("diff-tree", "--no-commit-id", "--name-only", "-r", "--root", commit)
        return sorted({line.strip() for line in (listing or "").splitlines() if line.strip()})
    base = gate_base_commit()
    return sorted(gate_changed_paths(base))


def draft_existing_requirement_ids(text: str) -> list[str]:
    prefix = str(load_json(REQUIREMENTS_PATH).get("requirement_id_prefix", "REQ")) if REQUIREMENTS_PATH.is_file() else "REQ"
    return sorted(set(re.findall(rf"\b{re.escape(prefix)}-\d+\b", text)))


def insert_changelog_draft(req_id: str, category: str, title: str) -> None:
    path = Path("CHANGELOG.md")
    today = datetime.now().strftime("%Y-%m-%d")
    bullet = (
        f"- `{req_id}` | {category} | DRAFT: {title}\n"
        f"  - Generated by `omni requirement draft`. Replace this line and the requirement's description, "
        f"acceptance criteria and validation, then move the requirement out of `proposed` status.\n"
    )
    if not path.is_file():
        path.write_text(f"# Changelog\n\n## {today}\n\n### Proposed\n\n{bullet}\n", encoding="utf-8")
        return
    text = path.read_text(encoding="utf-8")
    heading_match = re.search(r"^## (\S+)", text, re.MULTILINE)
    if heading_match and heading_match.group(1) == today:
        section_match = re.search(r"^### Proposed\s*\n\n", text[heading_match.end():], re.MULTILINE)
        if section_match:
            insert_at = heading_match.end() + section_match.end()
            text = text[:insert_at] + bullet + text[insert_at:]
        else:
            insert_at = heading_match.end()
            # right after today's date heading, before whatever subsection (usually "### Completed") comes next
            text = text[:insert_at] + f"\n\n### Proposed\n\n{bullet}" + text[insert_at:].lstrip("\n")
    else:
        insert_at = heading_match.start() if heading_match else len(text.split("\n", 1)[0]) + 1
        text = text[:insert_at] + f"## {today}\n\n### Proposed\n\n{bullet}\n" + text[insert_at:]
    path.write_text(text, encoding="utf-8")


def run_requirement_draft(args: argparse.Namespace) -> int:
    paths = draft_changed_paths(args.commit)
    if paths is None:
        print("omni requirement draft: not inside a git repository.", file=sys.stderr)
        return 1
    if not paths:
        print("Nothing to draft: no changed paths " + (f"in {args.commit}" if args.commit else "against the gate's base commit") + ".", file=sys.stderr)
        return 1

    scan_text = ""
    if args.commit:
        scan_text = git_run("show", "-s", "--format=%B", args.commit) or ""
    existing = draft_existing_requirement_ids(scan_text) if scan_text else []
    if existing and not args.force:
        print(f"Already recorded under {', '.join(existing)}; not drafting a duplicate (use --force to draft anyway).", file=sys.stderr)
        return 0

    title = args.title
    if not title and args.commit:
        title = (git_run("show", "-s", "--format=%s", args.commit) or "").strip()
    if not title:
        title = f"{len(paths)} file(s) changed -- replace this title"
    category = args.category or infer_draft_category(paths)
    requirements = load_json(REQUIREMENTS_PATH) if REQUIREMENTS_PATH.is_file() else {"requirement_id_prefix": "REQ", "requirements": []}
    requirement_id = args.id or next_requirement_id(requirements)

    source = f"commit {args.commit}" if args.commit else "the current change set"
    add_args = argparse.Namespace(
        id=requirement_id, category=category, title=title,
        description=(
            f"DRAFT -- generated by `omni requirement draft` from {source} on {datetime.now().strftime('%Y-%m-%d')}. "
            "Replace this description, the acceptance criteria and the validation steps, then set --status once reviewed."
        ),
        priority="low", status="proposed", scope=",".join(paths[:60]) + (f",... and {len(paths) - 60} more" if len(paths) > 60 else ""),
        # no commas in these: split_csv() below would fragment a plain sentence into several list items
        acceptance="Reviewed and rewritten by a person rather than left as the auto-generated draft",
        validation="", docs="",
        risks="Auto-drafted: category and scope are a starting point rather than a verified claim",
    )
    result = run_requirement_add(add_args)
    if result != 0:
        return result
    if not args.no_changelog:
        insert_changelog_draft(requirement_id, category, title)
        print(f"Drafted a Proposed entry in CHANGELOG.md for {requirement_id}.")
    print(f"Review and edit {requirement_id} (`omni requirement show {requirement_id}`), then `omni requirement update {requirement_id} --status ...`.")
    return 0


# --------------------------------------------------------------------------
# Project configuration helpers
# --------------------------------------------------------------------------


def project_configuration() -> dict[str, Any]:
    try:
        data = load_json(RULESET_PATH)
    except (OSError, json.JSONDecodeError):
        return {}
    config = data.get("configuration") if isinstance(data, dict) else None
    return config if isinstance(config, dict) else {}


def classification_banner() -> str | None:
    value = project_configuration().get("classification_banner")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def classification_block() -> str:
    banner = classification_banner()
    return f"**Classification:** {banner}\n\n" if banner else ""


def configured_allowed_root_paths() -> set[str]:
    value = project_configuration().get("allowed_root_paths")
    if not isinstance(value, list):
        return set()
    return {str(item).rstrip("/") for item in value if isinstance(item, str) and item.strip()}


def is_git_ignored(path: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", path],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


# --------------------------------------------------------------------------
# Requirements registry: query, update, archive
# --------------------------------------------------------------------------


def normalize_requirement_id(value: str) -> str:
    value = value.strip().upper()
    if value.isdigit():
        prefix = "REQ"
        if REQUIREMENTS_PATH.is_file():
            try:
                prefix = str(load_json(REQUIREMENTS_PATH).get("requirement_id_prefix", "REQ"))
            except (OSError, json.JSONDecodeError):
                pass
        return f"{prefix}-{int(value):03d}"
    # REQ-044: an id the registry has since renumbered resolves to its current one (REQ-001 -> ARB-001).
    return resolve_requirement_id(value)


def all_requirement_ids() -> set[str]:
    ids: set[str] = set()
    for path in (REQUIREMENTS_PATH, REQUIREMENTS_ARCHIVE_PATH):
        if not path.is_file():
            continue
        try:
            registry = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        for item in registry.get("requirements", []) if isinstance(registry, dict) else []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                ids.add(item["id"])
    return ids


def vendored_workspace_dirs(root: Path | None = None) -> list[Path]:
    """Directories below the root that carry their own OmniEngineering workspace: a vendored subtree, a
    monorepo package, an adopter checked in beside the template. Recognised by `.ai/omni-version.json`,
    which `omni adopt` writes and nothing else does. The root itself is never listed."""
    base = (root or Path(".")).resolve()
    found: list[Path] = []
    for marker in sorted(base.glob("*/.ai/omni-version.json")) + sorted(base.glob("*/*/.ai/omni-version.json")):
        workspace = marker.parent.parent
        if workspace != base and not any(part in DEFAULT_MAP_EXCLUDED_DIRS for part in workspace.relative_to(base).parts):
            found.append(workspace)
    return found


def vendored_requirement_ids() -> set[str]:
    """Requirement ids owned by vendored workspaces. Their commits cite their own registries, and a
    subtree pull brings those messages here; the gate must not read them as typos in this registry."""
    ids: set[str] = set()
    for workspace in vendored_workspace_dirs():
        for path in sorted((workspace / ".ai" / "requirements").glob("requirements*.json")):
            try:
                registry = load_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            for item in registry.get("requirements", []) if isinstance(registry, dict) else []:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    ids.add(item["id"])
    return ids


# --- Requirement id aliases (REQ-044) --------------------------------------------------------------
#
# `omni requirement renumber` changes the prefix of every id (REQ-012 -> ARB-012) and records the old
# ids under the registry's optional top-level `id_aliases` object, {"REQ-012": "ARB-012"}. Commit
# messages, waivers and other history that cite the old id stay valid: every reader resolves an alias
# before deciding an id is unknown. An alias is followed once (an alias never points at another alias;
# renumber rewrites the targets of the aliases it inherits).

REQUIREMENT_ID_PATTERN = re.compile(r"[A-Z]+-\d{3}")


def requirement_id_aliases(registry: dict[str, Any] | None = None) -> dict[str, str]:
    """`id_aliases` of the root registry (or of the registry object given): old id -> current id. Malformed
    entries are skipped here and reported by `validate_requirement_aliases` in the doctor."""
    if registry is None:
        if not REQUIREMENTS_PATH.is_file():
            return {}
        try:
            registry = load_json(REQUIREMENTS_PATH)
        except (OSError, json.JSONDecodeError):
            return {}
    aliases = registry.get("id_aliases") if isinstance(registry, dict) else None
    if not isinstance(aliases, dict):
        return {}
    return {old: new for old, new in aliases.items() if isinstance(old, str) and isinstance(new, str)}


def vendored_requirement_id_aliases() -> dict[str, str]:
    """The `id_aliases` of every vendored workspace's registries: a subtree's commits cite the ids its registry
    carried at the time, and a renumber there must not turn that history into gate failures here."""
    aliases: dict[str, str] = {}
    for workspace in vendored_workspace_dirs():
        for path in sorted((workspace / ".ai" / "requirements").glob("requirements*.json")):
            try:
                registry = load_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            aliases.update(requirement_id_aliases(registry))
    return aliases


def resolve_requirement_id(requirement_id: str, aliases: dict[str, str] | None = None) -> str:
    """Follow an alias once (REQ-038 -> ARB-038). An id with no alias passes through unchanged, so callers
    can resolve every cited id without first asking whether it was renamed."""
    if aliases is None:
        aliases = requirement_id_aliases()
    return aliases.get(requirement_id, requirement_id)


def find_requirement(requirement_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]] | None:
    wanted = normalize_requirement_id(requirement_id)
    for path in (REQUIREMENTS_PATH, REQUIREMENTS_ARCHIVE_PATH):
        if not path.is_file():
            continue
        registry = load_json(path)
        for item in registry.get("requirements", []):
            if isinstance(item, dict) and item.get("id") == wanted:
                return path, registry, item
    return None


def load_requirement_items(include_archive: bool) -> tuple[list[dict[str, Any]], int]:
    active = load_json(REQUIREMENTS_PATH).get("requirements", [])
    archived: list[dict[str, Any]] = []
    if REQUIREMENTS_ARCHIVE_PATH.is_file():
        archived = load_json(REQUIREMENTS_ARCHIVE_PATH).get("requirements", [])
    items = list(active) + (list(archived) if include_archive else [])
    return [item for item in items if isinstance(item, dict)], len(archived)


def format_requirement(item: dict[str, Any]) -> str:
    lines = [
        f"{item.get('id')}  [{item.get('status')}]  {item.get('priority')}  {item.get('category', '')}",
        str(item.get("title", "")),
        "",
        str(item.get("description", "")),
    ]
    sections = (
        ("Minimum access scope", "minimum_access_scope"),
        ("Acceptance criteria", "acceptance_criteria"),
        ("Validation required", "validation_required"),
        ("Documentation required", "documentation_required"),
        ("Risk notes", "risk_notes"),
    )
    for label, key in sections:
        values = item.get(key) or []
        if isinstance(values, str):
            values = [values]
        if values:
            lines.extend(["", f"{label}:"])
            lines.extend(f"  - {value}" for value in values)
    return "\n".join(lines)


def run_requirement_show(args: argparse.Namespace) -> int:
    found = find_requirement(args.id)
    if found is None:
        print(f"Requirement not found: {args.id}", file=sys.stderr)
        return 1
    path, _registry, item = found
    if args.json:
        print(json.dumps(item, indent=2))
        return 0
    print(format_requirement(item))
    if path == REQUIREMENTS_ARCHIVE_PATH:
        print("\n(archived)")
    return 0


def run_requirement_list(args: argparse.Namespace) -> int:
    items, archived_count = load_requirement_items(include_archive=args.all)
    if args.status:
        items = [item for item in items if item.get("status") in args.status]
    if args.last:
        items = items[-args.last:]
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    for item in items:
        print(f"{item.get('id')}  {str(item.get('status')):<12}  {item.get('title')}")
    note = f"{len(items)} shown"
    if archived_count and not args.all:
        note += f"; {archived_count} archived requirement(s) not listed (use --all or `show <ID>`)"
    print(note, file=sys.stderr)
    return 0


def run_requirement_search(args: argparse.Namespace) -> int:
    needle = " ".join(args.text).lower()
    items, _ = load_requirement_items(include_archive=not args.active_only)
    matches = 0
    for item in items:
        haystacks = {
            "id": str(item.get("id", "")),
            "title": str(item.get("title", "")),
            "category": str(item.get("category", "")),
            "description": str(item.get("description", "")),
            "risk_notes": " ".join(str(v) for v in (item.get("risk_notes") or [])),
            "acceptance_criteria": " ".join(str(v) for v in (item.get("acceptance_criteria") or [])),
        }
        fields = [name for name, text in haystacks.items() if needle in text.lower()]
        if not fields:
            continue
        matches += 1
        print(f"{item.get('id')}  {str(item.get('status')):<12}  {item.get('title')}  (matched: {', '.join(fields)})")
    print(f"{matches} match(es)", file=sys.stderr)
    return 0 if matches else 1


def run_requirement_update(args: argparse.Namespace) -> int:
    found = find_requirement(args.id)
    if found is None:
        print(f"Requirement not found: {args.id}", file=sys.stderr)
        return 1
    path, registry, item = found
    changed: list[str] = []
    for field in ("status", "title", "description"):
        value = getattr(args, field, None)
        if value:
            item[field] = value
            changed.append(field)
    if args.note:
        notes = item.get("risk_notes")
        if not isinstance(notes, list):
            notes = [notes] if isinstance(notes, str) and notes else []
        notes.append(args.note)
        item["risk_notes"] = notes
        changed.append("risk_notes")
    if not changed:
        print("Nothing to update: pass --status, --title, --description, or --note.", file=sys.stderr)
        return 1
    write_json(path, registry)
    print(f"Updated {item['id']} ({', '.join(changed)}); status is now {item.get('status')}.")
    return 0


DEFAULT_DEFECT_CATEGORY_PATTERN = r"defect|bug|fix|regress|incident|failure"
DEFECT_CATEGORY = re.compile(DEFAULT_DEFECT_CATEGORY_PATTERN, re.IGNORECASE)


def defect_category_pattern() -> re.Pattern[str]:
    """Which requirement categories count as defect work, and so need a failure-ledger entry to complete.
    Projects name their categories as they like (`developer-tooling`, `Defect`, `incident`), so the ruleset's
    `configuration.defect_category_pattern` overrides the default; an invalid pattern falls back to it."""
    value = project_configuration().get("defect_category_pattern")
    if isinstance(value, str) and value.strip():
        try:
            return re.compile(value, re.IGNORECASE)
        except re.error:
            pass
    return DEFECT_CATEGORY


def run_requirement_complete(args: argparse.Namespace) -> int:
    found = find_requirement(args.id)
    if found is not None and defect_category_pattern().search(str(found[2].get("category", ""))):
        requirement_id = str(found[2].get("id"))
        reason = (getattr(args, "no_failure_entry", None) or "").strip()
        if not failures_referencing(requirement_id):
            if len(reason) < 10:
                print(
                    f"{requirement_id} is a {found[2].get('category')} requirement, so completing it needs a failure-ledger entry\n"
                    f"recording what broke, why (root cause), the regression test, and what prevents a repeat:\n"
                    f'  ./omni failure add --requirement {requirement_id} --title "..." --symptom "..." --root-cause "..." --status fixed \\\n'
                    f'      --fix "..." --tests <test path or name> --prevention-rules <rule id>\n'
                    f'If this really was not a failure worth recording: --no-failure-entry "<why, 10+ chars>".',
                    file=sys.stderr,
                )
                return 1
            args.note = f"No failure-ledger entry: {reason}" + (f" | {args.note}" if getattr(args, "note", None) else "")
    # REQ-037: a fresh, passing Arbiter report is part of "complete" wherever the rule is wired.
    block = arbiter_completion_block()
    if block is not None:
        skip_reason = (getattr(args, "no_arbiter_check", None) or "").strip()
        if len(skip_reason) < 10:
            print(
                f"Refusing to complete {normalize_requirement_id(args.id)}: {block}\n"
                f'If Arbiter genuinely cannot run here: --no-arbiter-check "<why, 10+ chars>" (recorded as a risk note).',
                file=sys.stderr,
            )
            return 1
        args.note = f"arbiter check skipped: {skip_reason}" + (f" | {args.note}" if getattr(args, "note", None) else "")
    args.status = "completed"
    args.title = None
    args.description = None
    return run_requirement_update(args)


def run_requirement_archive(args: argparse.Namespace) -> int:
    """Sweep terminal requirements out of the live registry.

    By default every `withdrawn` entry and every `completed` entry older than the `--keep-recent` most recent
    ones moves to the archive. `--id` picks specific entries instead; a pending, blocked or proposed one is
    refused rather than silently moved, because archiving live work hides it from every session.
    """
    active = load_json(REQUIREMENTS_PATH)
    items = active.get("requirements", [])
    if not isinstance(items, list):
        print(f"{REQUIREMENTS_PATH} must contain a requirements array", file=sys.stderr)
        return 1

    # REQ-044: an old (aliased) id names the entry it was renumbered to.
    wanted = {normalize_requirement_id(rid) for rid in split_csv(getattr(args, "id", None))}
    if wanted:
        by_id = {str(item.get("id")): item for item in items if isinstance(item, dict)}
        unknown = sorted(wanted - set(by_id))
        if unknown:
            print(f"Not in the active registry: {', '.join(unknown)}", file=sys.stderr)
            return 1
        refused = sorted(rid for rid in wanted if by_id[rid].get("status") not in TERMINAL_REQUIREMENT_STATUSES)
        if refused:
            for rid in refused:
                print(
                    f"Refusing to archive {rid}: status is '{by_id[rid].get('status')}'. "
                    f"Only {' or '.join(sorted(TERMINAL_REQUIREMENT_STATUSES))} requirements may be archived.",
                    file=sys.stderr,
                )
            return 1
        archive_positions = [
            index for index, item in enumerate(items) if isinstance(item, dict) and str(item.get("id")) in wanted
        ]
        keep_note = ""
    else:
        completed_positions = [
            index for index, item in enumerate(items) if isinstance(item, dict) and item.get("status") == "completed"
        ]
        keep_positions = set(completed_positions[-args.keep_recent:]) if args.keep_recent > 0 else set()
        archive_positions = [index for index in completed_positions if index not in keep_positions] + [
            index for index, item in enumerate(items) if isinstance(item, dict) and item.get("status") == "withdrawn"
        ]
        archive_positions.sort()
        keep_note = f" (all non-terminal + the {args.keep_recent} most recent completed)"
    if not archive_positions:
        print("Nothing to archive.")
        return 0

    to_archive = [items[index] for index in archive_positions]
    remaining = [item for index, item in enumerate(items) if index not in set(archive_positions)]
    print(f"Archiving {len(to_archive)} requirement(s); {len(remaining)} stay active{keep_note}.")
    for item in to_archive:
        print(f"  {item.get('id')}  [{item.get('status')}]  {item.get('title', '')}")
    if args.dry_run:
        print("Dry run: no files written.")
        return 0

    if REQUIREMENTS_ARCHIVE_PATH.is_file():
        archive = load_json(REQUIREMENTS_ARCHIVE_PATH)
    else:
        archive = {
            "version": active.get("version", "1.0.0"),
            "requirement_id_prefix": active.get("requirement_id_prefix", "REQ"),
            "requirements": [],
        }
    known = {item.get("id") for item in archive.get("requirements", []) if isinstance(item, dict)}
    archive.setdefault("requirements", []).extend(item for item in to_archive if item.get("id") not in known)
    write_json(REQUIREMENTS_ARCHIVE_PATH, archive)
    active["requirements"] = remaining
    write_json(REQUIREMENTS_PATH, active)
    print(f"Wrote {REQUIREMENTS_ARCHIVE_PATH} and {REQUIREMENTS_PATH}.")
    return 0


# --- omni requirement renumber (REQ-044) ----------------------------------------------------------
#
# Change the prefix of every requirement id, keeping the number (REQ-012 -> ARB-012), in the registry,
# the archive, the failure ledger, the gate waivers and the governance text that cites them. Code,
# tests, `.git`, vendored workspaces and Arbiter output are never touched: git history keeps the old
# ids, and the `id_aliases` the command records keep that history valid for the gate and the CLI.

RENUMBER_DEFAULT_PATHS = [
    ".ai/**/*.md",
    ".ai/**/*.json",
    "*.md",
    "docs/**/*.md",
    ".claude/**/*.md",
    ".claude/skills/**",
]
RENUMBER_EXCLUDED_DIRS = DEFAULT_MAP_EXCLUDED_DIRS | {"arbiter-out"}


def _renumber_strings(value: Any, pattern: re.Pattern[str], replacement: str) -> tuple[Any, int]:
    """The JSON value with every matching id inside its strings rewritten, and how many were. Object keys
    are left alone: a key is never a requirement id a reader resolves (alias keys must stay the old ids)."""
    if isinstance(value, str):
        return pattern.subn(replacement, value)
    if isinstance(value, list):
        items: list[Any] = []
        total = 0
        for item in value:
            rewritten, count = _renumber_strings(item, pattern, replacement)
            items.append(rewritten)
            total += count
        return items, total
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        total = 0
        for key, item in value.items():
            rewritten, count = _renumber_strings(item, pattern, replacement)
            result[key] = rewritten
            total += count
        return result, total
    return value, 0


def _renumber_text_files(
    patterns: list[str], skip: set[Path], pattern: re.Pattern[str], replacement: str
) -> list[tuple[Path, str, int]]:
    """(path, rewritten text, change count) for every allow-listed text file the rewrite changes. Files the
    command handles structurally (`skip`), anything under `.git`, a vendored workspace, `arbiter-out` or
    another excluded directory, and files that are not UTF-8 text are left out whatever the globs say."""
    root = Path(".")
    vendored = [workspace.resolve() for workspace in vendored_workspace_dirs()]
    seen: set[Path] = set()
    results: list[tuple[Path, str, int]] = []
    for glob_pattern in patterns:
        try:
            matches = sorted(root.glob(glob_pattern))
        except (ValueError, NotImplementedError):
            continue
        for path in matches:
            if not path.is_file() or any(part in RENUMBER_EXCLUDED_DIRS for part in path.parts):
                continue
            resolved = path.resolve()
            if resolved in seen or resolved in skip or any(workspace in resolved.parents for workspace in vendored):
                continue
            seen.add(resolved)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rewritten, count = pattern.subn(replacement, text)
            if count:
                results.append((path, rewritten, count))
    return sorted(results, key=lambda entry: entry[0].as_posix())


def run_requirement_renumber(args: argparse.Namespace) -> int:
    new_prefix = str(getattr(args, "prefix", "") or "").strip()
    if not re.fullmatch(r"[A-Z]+", new_prefix):
        print(f"Refusing to renumber: the prefix must be upper-case letters only (got {new_prefix!r}).", file=sys.stderr)
        return 1
    if not REQUIREMENTS_PATH.is_file():
        print(f"Missing {REQUIREMENTS_PATH}", file=sys.stderr)
        return 1
    registry = load_json(REQUIREMENTS_PATH)
    if not isinstance(registry, dict) or not isinstance(registry.get("requirements"), list):
        print(f"{REQUIREMENTS_PATH} must contain a requirements array", file=sys.stderr)
        return 1
    old_prefix = str(registry.get("requirement_id_prefix", "REQ"))
    if new_prefix == old_prefix:
        print(f"Requirement ids already use the prefix {new_prefix}; nothing to renumber.")
        return 0
    archive = load_json(REQUIREMENTS_ARCHIVE_PATH) if REQUIREMENTS_ARCHIVE_PATH.is_file() else None
    if archive is not None and (not isinstance(archive, dict) or not isinstance(archive.get("requirements"), list)):
        print(f"{REQUIREMENTS_ARCHIVE_PATH} must contain a requirements array", file=sys.stderr)
        return 1

    pattern = re.compile(rf"\b{re.escape(old_prefix)}-(\d{{3}})\b")
    replacement = f"{new_prefix}-\\1"
    mapping: dict[str, str] = {}
    counts = {"active": 0, "archived": 0}
    for label, source in (("active", registry), ("archived", archive)):
        for item in source.get("requirements", []) if isinstance(source, dict) else []:
            rid = item.get("id") if isinstance(item, dict) else None
            if isinstance(rid, str) and pattern.fullmatch(rid):
                mapping[rid] = pattern.sub(replacement, rid)
                counts[label] += 1
    # An alias this registry already carries keeps its key (the oldest id) and follows the rename.
    aliases = {old: pattern.sub(replacement, new) for old, new in requirement_id_aliases(registry).items()}
    aliases.update(mapping)

    json_writes: list[tuple[Path, Any, int]] = []
    body = {key: value for key, value in registry.items() if key != "id_aliases"}
    rewritten, count = _renumber_strings(body, pattern, replacement)
    new_registry: dict[str, Any] = {}
    for key, value in rewritten.items():
        new_registry[key] = new_prefix if key == "requirement_id_prefix" else value
        if key == "requirement_id_prefix":
            new_registry["id_aliases"] = dict(sorted(aliases.items()))
    new_registry.setdefault("requirement_id_prefix", new_prefix)
    new_registry.setdefault("id_aliases", dict(sorted(aliases.items())))
    json_writes.append((REQUIREMENTS_PATH, new_registry, count + 1 + len(aliases)))
    if archive is not None:
        rewritten, count = _renumber_strings(archive, pattern, replacement)
        rewritten["requirement_id_prefix"] = new_prefix
        json_writes.append((REQUIREMENTS_ARCHIVE_PATH, rewritten, count + 1))
    ledger_path = failure_ledger_path()
    if ledger_path.is_file():
        try:
            rewritten, count = _renumber_strings(load_json(ledger_path), pattern, replacement)
        except (OSError, json.JSONDecodeError):
            count = 0
        if count:
            json_writes.append((ledger_path, rewritten, count))

    text_writes: list[tuple[Path, str, int]] = []
    if GATE_WAIVERS_PATH.is_file():
        lines: list[str] = []
        count = 0
        for line in GATE_WAIVERS_PATH.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line) if line.strip() else None
            except json.JSONDecodeError:
                entry = None
            if isinstance(entry, dict):
                rewritten, changed = _renumber_strings(entry, pattern, replacement)
                lines.append(json.dumps(rewritten) if changed else line)
            else:
                rewritten, changed = pattern.subn(replacement, line)
                lines.append(rewritten)
            count += changed
        if count:
            text_writes.append((GATE_WAIVERS_PATH, "\n".join(lines) + "\n", count))
    skip = {path.resolve() for path in (REQUIREMENTS_PATH, REQUIREMENTS_ARCHIVE_PATH, ledger_path, GATE_WAIVERS_PATH)}
    text_writes.extend(_renumber_text_files(list(getattr(args, "paths", None) or RENUMBER_DEFAULT_PATHS), skip, pattern, replacement))

    print(
        f"Renumbering {old_prefix}-### -> {new_prefix}-###: {len(mapping)} requirement id(s) "
        f"({counts['active']} active, {counts['archived']} archived); {len(aliases)} alias(es) recorded in {REQUIREMENTS_PATH}."
    )
    for path, _value, count in json_writes:
        print(f"  {path.as_posix()}: {count} change(s)")
    for path, _text, count in text_writes:
        print(f"  {path.as_posix()}: {count} change(s)")
    if getattr(args, "dry_run", False):
        print("Dry run: no files written.")
        return 0
    for path, value, _count in json_writes:
        write_json(path, value)
    for path, text, _count in text_writes:
        path.write_text(text, encoding="utf-8")
    print(f"Wrote {len(json_writes) + len(text_writes)} file(s). Commit messages keep the old ids; the aliases keep them valid.")
    return 0


# --------------------------------------------------------------------------
# Completion gate: turns rulepack `co_changed` validations into real checks
# --------------------------------------------------------------------------


def git_run(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def glob_regex(pattern: str) -> "re.Pattern[str]":
    out: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("^" + "".join(out) + "$")


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(glob_regex(pattern).match(path) for pattern in patterns)


def gate_base_commit() -> str | None:
    head = git_run("rev-parse", "--verify", "-q", "HEAD")
    if head is None:
        return None
    for ref in GATE_DEFAULT_BASE_REFS:
        if git_run("rev-parse", "--verify", "-q", ref) is None:
            continue
        base = git_run("merge-base", "HEAD", ref)
        if base and base.strip():
            return base.strip()
    return head.strip()


def gate_changed_paths(base: str | None) -> set[str]:
    paths: set[str] = set()
    if base:
        diff = git_run("diff", "--name-only", "--no-renames", base)
    else:
        diff = git_run("diff", "--name-only", "--no-renames", "--cached")
    for line in (diff or "").splitlines():
        if line.strip():
            paths.add(line.strip())
    for line in (git_run("ls-files", "--others", "--exclude-standard") or "").splitlines():
        if line.strip():
            paths.add(line.strip())
    return paths


# Kept in step with every validation type a check function below actually implements. A rule can declare a
# validation the gate does not (yet) execute; gate_rules() silently skips those rather than crashing on them,
# but "declared and silently never checked" is exactly the trap this set exists to avoid falling into by accident.
EXECUTABLE_VALIDATION_TYPES = {"co_changed", "requirement_registry_entry", "content_forbidden", "command"}


def gate_rules() -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for file_path in RULEPACK_FILES:
        try:
            rulepack = load_json(Path(file_path))
        except (OSError, json.JSONDecodeError):
            continue
        for rule in rulepack.get("rules", []) if isinstance(rulepack, dict) else []:
            validation = rule.get("validation") if isinstance(rule, dict) else None
            if (
                isinstance(validation, dict)
                and validation.get("type") in EXECUTABLE_VALIDATION_TYPES
                and rule.get("severity") == "required"
            ):
                rules.append(rule)
    return rules


def _gate_check_co_changed(rule: dict[str, Any], validation: dict[str, Any], changed: set[str]) -> str | None:
    when = [str(p) for p in validation.get("when_changed", ["**"])]
    ignore = [str(p) for p in validation.get("ignore", [])]
    must = [str(p) for p in validation.get("must_also_change", [])]
    triggers = sorted(p for p in changed if matches_any(p, when) and not matches_any(p, ignore))
    if not triggers or not must or any(matches_any(p, must) for p in changed):
        return None
    rule_id = str(rule["id"])
    example = ", ".join(triggers[:2]) + (f" (+{len(triggers) - 2} more)" if len(triggers) > 2 else "")
    return (
        f"{rule_id}: {len(triggers)} changed file(s) [{example}] need a matching change to "
        f"{' or '.join(must)}. If genuinely not applicable: "
        f'./omni waive {rule_id} --reason "<why>"'
    )


def _gate_check_requirement_registry_entry(rule: dict[str, Any], validation: dict[str, Any], changed: set[str], base: str | None) -> str | None:
    """Every requirement id cited by this change (in a commit message since the base, or newly added to
    CHANGELOG.md) must actually exist in the requirements registry. The registry's own schema validation
    (which `omni doctor`, and so every `omni gate` call, already runs) only checks the registry's own
    shape; it cannot see a typo'd or invented REQ-### that some other file merely claims about it."""
    target = Path(str(validation.get("target", REQUIREMENTS_PATH)))
    try:
        registry = load_json(target) if target.is_file() else {}
        prefix = str(registry.get("requirement_id_prefix", "REQ")) if isinstance(registry, dict) else "REQ"
    except (OSError, json.JSONDecodeError):
        return None  # a malformed registry is already reported by doctor; do not double up here
    # REQ-044: history cites the prefix the registry carried at the time, so every alias prefix is matched
    # too, and a cited id is resolved through the aliases (this registry's and the vendored ones') first.
    aliases = {**vendored_requirement_id_aliases(), **requirement_id_aliases(registry)}
    prefixes = sorted({prefix} | {old.split("-", 1)[0] for old in aliases if REQUIREMENT_ID_PATTERN.fullmatch(old)})
    pattern = re.compile(rf"\b(?:{'|'.join(re.escape(p) for p in prefixes)})-\d+\b")
    cited: set[str] = set()
    if base:
        cited.update(pattern.findall(git_run("log", f"{base}..HEAD", "--format=%B") or ""))
    if "CHANGELOG.md" in changed and Path("CHANGELOG.md").is_file():
        cited.update(pattern.findall(Path("CHANGELOG.md").read_text(encoding="utf-8", errors="replace")))
    if not cited:
        return None
    cited = {resolve_requirement_id(rid, aliases) for rid in cited}
    unknown = sorted(cited - all_requirement_ids() - vendored_requirement_ids())
    if not unknown:
        return None
    rule_id = str(rule["id"])
    return (
        f"{rule_id}: {', '.join(unknown)} cited (in a commit message or CHANGELOG.md) but not found in "
        f"{target}. Fix the typo, or add it: ./omni requirement add --id {unknown[0]} ..."
    )


def _gate_check_content_forbidden(rule: dict[str, Any], validation: dict[str, Any], changed: set[str]) -> str | None:
    """A lightweight, honest safety net -- a handful of regexes for the most common accidental leaks (a
    private-key header, an AWS-shaped access key, an obviously hardcoded credential) -- not a claim of
    exhaustive secret scanning. Skips this rulepack's own files, whose JSON literally contains the pattern
    source text and would otherwise flag itself."""
    when = [str(p) for p in validation.get("when_changed", ["**"])]
    ignore = [str(p) for p in validation.get("ignore", [])] + list(RULEPACK_FILES)
    compiled: list[tuple[re.Pattern[str], str]] = []
    for entry in validation.get("patterns", []):
        try:
            compiled.append((re.compile(str(entry["pattern"])), str(entry.get("message", "matches a forbidden pattern"))))
        except (KeyError, re.error):
            continue
    if not compiled:
        return None
    hits: list[str] = []
    for path_str in sorted(changed):
        if not matches_any(path_str, when) or matches_any(path_str, ignore):
            continue
        path = Path(path_str)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for regex, message in compiled:
            found = regex.search(text)
            if found:
                line = text.count("\n", 0, found.start()) + 1
                hits.append(f"{path_str}:{line} {message}")
                break  # one hit is enough to flag the file; this is a net, not a full audit
    if not hits:
        return None
    rule_id = str(rule["id"])
    example = "; ".join(hits[:3]) + (f" (+{len(hits) - 3} more)" if len(hits) > 3 else "")
    return f"{rule_id}: {example}"


def _gate_command_argv(argv: list[str]) -> list[str] | None:
    """The argv a `command` rule actually runs. `omni` in a run text is this CLI: the `omni` script beside
    this module, run by the interpreter running the gate, so a rule such as `omni test run --impacted`
    works where `omni` is not on PATH (an adopted repository runs `python omni ...`) and never picks up
    some other omni that happens to be installed. Anything else resolves through PATH; None when it
    cannot (REQ-047)."""
    if argv[0] == "omni":
        sibling = Path(__file__).resolve().parent / "omni"
        if sibling.is_file():
            return [sys.executable, str(sibling), *argv[1:]]
    executable = shutil.which(argv[0])
    return None if executable is None else [executable, *argv[1:]]


def _gate_check_command(rule: dict[str, Any], validation: dict[str, Any], changed: set[str], base: str | None) -> str | None:
    """Run the project's own check -- a scanner, a test suite, a linter -- as a gate. The command runs only
    when a changed path matches `when_changed` (minus `ignore`), `{base}` in `run` is the gate's base commit,
    and a non-zero exit, a timeout, or an executable that is not on PATH all fail the rule: an unrunnable
    check is not a pass. The last lines of its output ride along so the failure says why, not just that."""
    if not arbiter_rule_scope(rule, changed):
        return None
    rule_id = str(rule["id"])
    run = str(validation.get("run", "")).strip()
    if not run:
        return f"{rule_id}: command validation has no `run` to execute"
    rendered = run.replace("{base}", base or "HEAD")
    try:
        argv = shlex.split(rendered)
    except ValueError as exc:
        return f"{rule_id}: cannot parse `{rendered}`: {exc}"
    if not argv:
        return f"{rule_id}: command validation has no `run` to execute"
    command = _gate_command_argv(argv)
    if command is None:
        return f"{rule_id}: `{argv[0]}` is not on PATH, so `{rendered}` could not run (an unrunnable check is not a pass)"
    try:
        timeout = float(validation.get("timeout", 600))
    except (TypeError, ValueError):
        timeout = 600.0
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"{rule_id}: `{rendered}` did not finish within {timeout:.0f}s"
    except OSError as exc:
        return f"{rule_id}: `{rendered}` could not start: {exc}"
    if completed.returncode == 0:
        return None
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    tail = [line for line in output.strip().splitlines() if line.strip()][-5:]
    detail = "".join(f"\n    {line}" for line in tail)
    return f"{rule_id}: `{rendered}` exited {completed.returncode}{detail}"


def gate_waivers(base: str | None) -> dict[str, str]:
    if not GATE_WAIVERS_PATH.is_file():
        return {}
    tracked = git_run("ls-files", "--error-unmatch", "--", str(GATE_WAIVERS_PATH)) is not None
    if tracked:
        if not base:
            return {}
        diff = git_run("diff", "--unified=0", "--no-color", base, "--", str(GATE_WAIVERS_PATH)) or ""
        lines = [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
    else:
        lines = GATE_WAIVERS_PATH.read_text(encoding="utf-8").splitlines()
    waivers: dict[str, str] = {}
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        reason = str(entry.get("reason", "")).strip() if isinstance(entry, dict) else ""
        if isinstance(entry, dict) and entry.get("rule") and len(reason) >= 10:
            waivers[str(entry["rule"])] = reason
    return waivers


def gate_rule_memo_signature(rule: dict[str, Any], changed: set[str], base: str | None = None) -> str:
    """What a passed `command` rule's memo is keyed on (REQ-047): the rule's run text (with `{base}`
    rendered, so a moved base re-runs it) and, for every changed path the rule's own globs select
    (`arbiter_rule_scope`), its mtime and size. Any edit to a scoped file changes the signature; an edit
    to a file the rule ignores (the registry, the changelog, a document) does not."""
    validation = rule.get("validation") if isinstance(rule, dict) else None
    validation = validation if isinstance(validation, dict) else {}
    parts = ["run=" + str(validation.get("run", "")).strip().replace("{base}", base or "HEAD")]
    for path in sorted(arbiter_rule_scope(rule, changed)):
        try:
            stat = Path(path).stat()
            parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            parts.append(f"{path}:gone")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def gate_evaluate(
    changed: set[str],
    waivers: dict[str, str],
    base: str | None = None,
    memo: dict[str, str] | None = None,
    outcomes: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[str]]:
    """Every gated rule against the change set. `memo` (rule id -> memo signature, from the gate state
    file) is consulted and updated in place for `command` rules: one whose stored signature matches the
    current scoped change set is reported as passed (memo) and not re-run, a pass records its signature,
    and a failure removes any record, so the memo never applies to a rule that failed (REQ-047). With
    `memo=None` nothing is memoised. `outcomes`, when given, collects one record per rule."""
    failures: list[str] = []
    waived: list[str] = []
    for rule in gate_rules():
        validation = rule["validation"]
        vtype = validation.get("type")
        rule_id = str(rule["id"])
        outcome: dict[str, Any] = {"rule": rule_id, "type": vtype, "status": "pass", "duration_s": 0.0}
        if vtype == "co_changed":
            failure = _gate_check_co_changed(rule, validation, changed)
        elif vtype == "requirement_registry_entry":
            failure = _gate_check_requirement_registry_entry(rule, validation, changed, base)
        elif vtype == "content_forbidden":
            failure = _gate_check_content_forbidden(rule, validation, changed)
        elif vtype == "command":
            if not arbiter_rule_scope(rule, changed):
                outcome["status"] = "skipped"
                if outcomes is not None:
                    outcomes.append(outcome)
                continue
            signature = gate_rule_memo_signature(rule, changed, base)
            if memo is not None and memo.get(rule_id) == signature:
                outcome["status"] = "memo"
                if outcomes is not None:
                    outcomes.append(outcome)
                continue
            started = time.monotonic()
            failure = _gate_check_command(rule, validation, changed, base)
            outcome["duration_s"] = round(time.monotonic() - started, 1)
            if memo is not None:
                if failure is None:
                    memo[rule_id] = signature
                else:
                    memo.pop(rule_id, None)
        else:
            continue
        if failure is None:
            if outcomes is not None:
                outcomes.append(outcome)
            continue
        if rule_id in waivers:
            waived.append(f"{rule_id}: waived ({waivers[rule_id]})")
            outcome["status"] = "waived"
        else:
            failures.append(failure)
            outcome["status"] = "fail"
        if outcomes is not None:
            outcomes.append(outcome)
    return failures, waived


def gate_state_file() -> Path | None:
    git_dir = git_run("rev-parse", "--git-dir")
    return Path(git_dir.strip()) / "omni-gate-last.json" if git_dir else None


def gate_state_load() -> dict[str, Any]:
    """The gate state file's contents (`{"signature": ..., "memo": {rule id: signature}}`), or {}."""
    state_file = gate_state_file()
    if state_file is None:
        return {}
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def gate_state_save(update: dict[str, Any]) -> None:
    """Merge `update` into the gate state file; the hook's failure signature and the per-rule memo share
    the file, so neither write may drop the other's key. Best effort: a read-only .git is not an error."""
    state_file = gate_state_file()
    if state_file is None:
        return
    state = gate_state_load()
    state.update(update)
    try:
        state_file.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def gate_signature(failures: list[str], changed: set[str]) -> str:
    parts = sorted(failures)
    for path in sorted(changed):
        try:
            parts.append(f"{path}:{Path(path).stat().st_mtime_ns}")
        except OSError:
            parts.append(f"{path}:gone")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def run_gate(args: argparse.Namespace) -> int:
    top = git_run("rev-parse", "--show-toplevel")
    if top is None:
        print("omni gate: not inside a git repository; nothing to check.")
        return 0
    os.chdir(top.strip())

    hook_input: dict[str, Any] = {}
    if args.hook and not sys.stdin.isatty():
        try:
            parsed = json.loads(sys.stdin.read() or "{}")
            hook_input = parsed if isinstance(parsed, dict) else {}
        except (ValueError, OSError):
            hook_input = {}
    if args.hook and hook_input.get("stop_hook_active"):
        return 0

    base = gate_base_commit()
    changed = gate_changed_paths(base)
    if changed and not args.hook and omni_graph is not None:
        try:
            graph_file = Path(GRAPH_DEFAULT_OUTPUT)
            if graph_file.is_file():
                print(omni_graph.impact_summary(omni_graph.impact(graph_file, sorted(changed))))
        except Exception:  # noqa: BLE001 -- the impact line is advice beside the gate; it never fails or delays it
            pass
    failures: list[str] = []
    waived: list[str] = []
    outcomes: list[dict[str, Any]] = []
    if changed:
        doctor = build_doctor_report()
        failures.extend(f"doctor: {message}" for message in doctor.errors)
        # REQ-047: a command rule that passed on exactly this scoped change set is not re-run (hook or
        # not) until a scoped file changes; --no-memo forces every rule, and still records the passes.
        stored = gate_state_load().get("memo")
        memo: dict[str, str] = {} if getattr(args, "no_memo", False) or not isinstance(stored, dict) else dict(stored)
        rule_failures, waived = gate_evaluate(changed, gate_waivers(base), base, memo=memo, outcomes=outcomes)
        failures.extend(rule_failures)
        gate_state_save({"memo": memo})

    if not args.hook:
        for outcome in outcomes:
            if outcome["type"] != "command" or outcome["status"] == "skipped":
                continue
            if outcome["status"] == "memo":
                print(f"  memo    {outcome['rule']} passed (memo)")
            else:
                verdict = {"pass": "PASS", "fail": "FAIL", "waived": "FAIL (waived)"}[outcome["status"]]
                print(f"  ran     {outcome['rule']} {outcome['duration_s']:.1f}s {verdict}")

    if not failures:
        if not args.hook:
            print(f"omni gate: PASS ({len(changed)} changed path(s) checked)")
            for note in waived:
                print(f"  waived  {note}")
        return 0

    lines = ["omni gate: completion gates NOT satisfied"]
    lines.extend(f"  - {failure}" for failure in failures)
    lines.append("Do not report this task complete until `./omni gate` passes.")
    message = "\n".join(lines)

    if not args.hook:
        print(message)
        return 1

    signature = gate_signature(failures, changed)
    if gate_state_file() is not None:
        if gate_state_load().get("signature") == signature:
            return 0
        gate_state_save({"signature": signature})
    print(message, file=sys.stderr)
    return 2


def run_waive(args: argparse.Namespace) -> int:
    reason = args.reason.strip()
    if len(reason) < 10:
        print("A waiver needs a real --reason (at least 10 characters).", file=sys.stderr)
        return 1
    known = {str(rule["id"]) for rule in gate_rules()}
    if args.rule not in known:
        print(f"Unknown gate rule: {args.rule}. Gated rules: {', '.join(sorted(known)) or 'none'}", file=sys.stderr)
        return 1
    entry = {
        "rule": args.rule,
        "reason": reason,
        "requirement": args.requirement,
        "date": datetime.now(timezone.utc).date().isoformat(),
    }
    GATE_WAIVERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with GATE_WAIVERS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    print(f"Recorded waiver for {args.rule} in {GATE_WAIVERS_PATH}.")
    return 0


def run_hook_install(args: argparse.Namespace) -> int:
    settings_path = Path(args.settings)
    data: dict[str, Any] = {}
    if settings_path.is_file():
        try:
            data = load_json(settings_path)
        except json.JSONDecodeError as exc:
            print(f"{settings_path} is not valid JSON ({exc}); fix it first.", file=sys.stderr)
            return 1
    stop_entries = data.setdefault("hooks", {}).setdefault("Stop", [])
    already = any(
        hook.get("command") == CLAUDE_STOP_HOOK_COMMAND
        for entry in stop_entries
        if isinstance(entry, dict)
        for hook in entry.get("hooks", [])
        if isinstance(hook, dict)
    )
    if already:
        print(f"Stop hook already installed in {settings_path}.")
        return 0
    stop_entries.append({"hooks": [{"type": "command", "command": CLAUDE_STOP_HOOK_COMMAND}]})
    write_json(settings_path, data)
    print(f"Installed Claude Code Stop hook in {settings_path}: `{CLAUDE_STOP_HOOK_COMMAND}`.")
    print("It blocks the assistant from finishing while `omni gate` fails.")
    if settings_path.name.endswith(".local.json"):
        print("This is a local settings file, so it applies to you only; enforce the gate for everyone by running `omni gate` in CI.")
    else:
        print("Commit this file to share the hook with your team.")
    return 0


def _write_git_hook(root: Path, relpath: Path, marker: str, script: str, force: bool) -> int | None:
    """Write one hook file, refusing to clobber a foreign hook without --force. None means "wrote it"; an int is
    the exit code for a refusal."""
    hook_path = root / relpath
    if hook_path.is_file() and marker not in hook_path.read_text(encoding="utf-8", errors="replace") and not force:
        print(f"{hook_path} already exists and was not installed by omni; rerun with --force to overwrite.", file=sys.stderr)
        return 1
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(script, encoding="utf-8", newline="\n")
    try:
        hook_path.chmod(hook_path.stat().st_mode | 0o111)
    except OSError:
        pass  # Windows ignores the executable bit; Git for Windows runs the hook through its bundled sh regardless.
    print(f"Wrote {hook_path}.")
    return None


def run_hook_install_git(args: argparse.Namespace) -> int:
    top = git_run("rev-parse", "--show-toplevel")
    if top is None:
        print("omni hook install-git: not inside a git repository.", file=sys.stderr)
        return 1
    root = Path(top.strip())

    refusal = _write_git_hook(root, PRE_COMMIT_HOOK_RELPATH, PRE_COMMIT_HOOK_MARKER, PRE_COMMIT_HOOK_SCRIPT, args.force)
    if refusal is not None:
        return refusal
    if args.with_graph_rebuild:
        refusal = _write_git_hook(root, POST_COMMIT_HOOK_RELPATH, POST_COMMIT_HOOK_MARKER, POST_COMMIT_HOOK_SCRIPT, args.force)
        if refusal is not None:
            return refusal

    old_cwd = Path.cwd()
    try:
        os.chdir(root)
        git_run("config", "--local", "core.hooksPath", ".githooks")
    finally:
        os.chdir(old_cwd)
    print("Set core.hooksPath=.githooks for this clone.")
    print("Commit .githooks/ so every clone can `git config core.hooksPath .githooks` (or rerun this command) to "
          "opt in with the same scripts; CI enforces the gate for everyone regardless of whether they do.")
    if args.with_graph_rebuild:
        print("Each commit will also rebuild the graph in the background; watch .ai/.graph-build.log if `omni "
              "graph` output ever looks stale.")
    return 0


def run_mcp_serve(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1
    try:
        import omni_mcp
    except ImportError:
        print(
            "omni_mcp.py is missing next to make_ai.py, so `omni mcp` is unavailable. "
            "Re-run `omni adopt --include-cli` or `omni update` from a current OmniEngineering source to restore it.",
            file=sys.stderr,
        )
        return 1
    print(f"omni mcp: serving {len(omni_mcp.TOOLS)} tool(s) over stdio (JSON-RPC 2.0, one message per line).", file=sys.stderr)
    try:
        omni_mcp.serve_stdio()
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    return 0


def run_mcp_tools(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1
    try:
        import omni_mcp
    except ImportError:
        print("omni_mcp.py is missing next to make_ai.py.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps([tool.spec() for tool in omni_mcp.TOOLS], indent=2))
        return 0
    for tool in omni_mcp.TOOLS:
        print(f"{tool.name}\n  {tool.description}")
    return 0


def run_rule_add(args: argparse.Namespace) -> int:
    path = resolve_rulepack_path(args.rulepack)
    if not path.is_file():
        print(f"Rulepack not found: {path}", file=sys.stderr)
        return 1

    rulepack = load_json(path)
    rulepack_id = str(rulepack.get("rulepack_id", slugify(path.stem)))
    rule_id = args.id or f"{rulepack_id}.{slugify(args.name or args.statement[:48])}"

    existing_ids = {
        rule.get("id")
        for rule in rulepack.get("rules", [])
        if isinstance(rule, dict)
    }
    if rule_id in existing_ids:
        print(f"Rule already exists in {path}: {rule_id}", file=sys.stderr)
        return 1

    new_rule: dict[str, Any] = {
        "id": rule_id,
        "severity": args.severity,
        "statement": args.statement,
    }

    scopes = split_csv(args.scope)
    if scopes:
        new_rule["scope"] = scopes

    if args.validation_type or args.validation_target:
        new_rule["validation"] = {
            key: value
            for key, value in {
                "type": args.validation_type,
                "target": args.validation_target,
            }.items()
            if value
        }

    rulepack.setdefault("rules", []).append(new_rule)
    write_json(path, rulepack)
    print(f"Added rule {rule_id} to {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Maintain an OmniEngineering workspace. The .ai directory, generated "
            "entrypoint sources, assistant shims, and synced ignore files are the "
            "delivery surface; this script syncs and validates them."
        )
    )
    subparsers = parser.add_subparsers(dest="command")

    sync_parser = subparsers.add_parser(
        "sync",
        help="Refresh .ai entrypoints, assistant shims, and ignore files.",
    )
    sync_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing non-Omni assistant files instead of skipping them.",
    )
    doctor_parser = subparsers.add_parser("doctor", help="Check OmniEngineering workspace health.")
    doctor_parser.add_argument("--json", action="store_true", help="Print the report as JSON (schema_version 2).")
    doctor_parser.add_argument(
        "--probe", action="store_true",
        help="Launch every stdio MCP server in .mcp.json for real instead of trusting the day's memo.",
    )
    validate_parser = subparsers.add_parser("validate", help="Alias for doctor.")
    validate_parser.add_argument("--json", action="store_true", help="Print the report as JSON (schema_version 2).")
    validate_parser.add_argument(
        "--probe", action="store_true",
        help="Launch every stdio MCP server in .mcp.json for real instead of trusting the day's memo.",
    )

    map_parser = subparsers.add_parser(
        "map",
        help="Generate .ai/project-map.md for LLM repository navigation.",
    )
    map_parser.add_argument(
        "--root",
        default=".",
        help="Project root to map. Defaults to the current repository.",
    )
    map_parser.add_argument(
        "--output",
        default=PROJECT_MAP_DEFAULT_OUTPUT,
        help=f"Map output path. Defaults to {PROJECT_MAP_DEFAULT_OUTPUT}.",
    )
    map_parser.add_argument(
        "--max-depth",
        type=int,
        default=4,
        help="Maximum directory depth to include.",
    )
    map_parser.add_argument(
        "--max-entries",
        type=int,
        default=600,
        help="Maximum number of files/directories to include.",
    )
    map_parser.add_argument(
        "--include-ai",
        action="store_true",
        help="Include .ai internals in the generated map.",
    )

    graph_parser = subparsers.add_parser(
        "graph",
        help="Build and query a deterministic tree-sitter AST code graph (no embeddings, no vector store).",
    )
    graph_subparsers = graph_parser.add_subparsers(dest="graph_command")

    graph_build = graph_subparsers.add_parser(
        "build",
        help="Parse source with tree-sitter and write a project graph.",
    )
    graph_build.add_argument(
        "--root",
        default=".",
        help="Project root to parse. Defaults to the current repository.",
    )
    graph_build.add_argument(
        "--output",
        default=GRAPH_DEFAULT_OUTPUT,
        help=f"Graph output path. Defaults to {GRAPH_DEFAULT_OUTPUT}.",
    )
    graph_build.add_argument(
        "--languages",
        default="auto",
        help=(
            "Comma-separated languages to parse, or auto (default) to use every language found. "
            "Languages without an installed grammar still appear as file-level nodes; SQL migrations "
            "become a table/foreign-key schema."
        ),
    )
    graph_build.add_argument(
        "--layers",
        default="",
        help=(
            "Comma-separated layers to build: code, governance (requirements, changelog), history (git commits), "
            "assurance (tests, suites, failure ledger), workspace (rulepacks, rules, playbooks, checklists). "
            "Defaults to all five; code is always built."
        ),
    )
    graph_build.add_argument(
        "--max-commits",
        type=int,
        default=None,
        help="Most recent commits scanned when building the history layer (default: max_commits in .ai/graph-config.json, else 400).",
    )
    graph_build.add_argument(
        "--semantic",
        action="store_true",
        help=(
            "Also run a semantic enrichment pass tagged INFERRED via the API configured by "
            f"{GRAPH_SEMANTIC_API_URL_ENV}. Nothing leaves this machine unless this flag "
            "is set and that variable is configured."
        ),
    )
    graph_build.add_argument(
        "--json",
        action="store_true",
        help="Print a machine-readable build summary instead of a human summary.",
    )

    graph_trace = graph_subparsers.add_parser(
        "trace",
        help="Trace the shortest path between two symbols in the graph.",
    )
    graph_trace.add_argument("source", help="Name or qualified name to start from.")
    graph_trace.add_argument("target", help="Name or qualified name to reach.")
    graph_trace.add_argument(
        "--graph",
        default=GRAPH_DEFAULT_OUTPUT,
        help=f"Graph file to read. Defaults to {GRAPH_DEFAULT_OUTPUT}.",
    )
    graph_trace.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    graph_show = graph_subparsers.add_parser(
        "show",
        help="List the direct EXTRACTED/INFERRED edges for one symbol, or every node with --all.",
    )
    graph_show.add_argument("node", nargs="?", help="Name, qualified name, or full id to inspect (omit with --all).")
    graph_show.add_argument(
        "--graph",
        default=GRAPH_DEFAULT_OUTPUT,
        help=f"Graph file to read. Defaults to {GRAPH_DEFAULT_OUTPUT}.",
    )
    graph_show.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    graph_show.add_argument("--all", action="store_true", help="List every node instead of inspecting one (externals hidden unless --include-external).")
    graph_show.add_argument("--kind", choices=["module", "class", "interface", "function", "method", "table", "external", "file", "requirement", "changelog", "commit", "failure", "rule", "suite", "rulepack", "playbook", "checklist"], help="With --all: only this node kind.")
    graph_show.add_argument("--layer", choices=["code", "governance", "history", "assurance", "workspace"], help="With --all: only this layer.")
    graph_show.add_argument("--language", help="With --all: only this language (python, java, sql, go, ...).")
    graph_show.add_argument("--file", help="With --all: only files matching this glob, or containing this text.")
    graph_show.add_argument("--include-external", action="store_true", help="With --all: include external placeholder nodes.")
    graph_show.add_argument("--edges", action="store_true", help="With --all: also print the edges among the listed nodes.")
    graph_show.add_argument("--sort", choices=["file", "degree", "name"], default="file", help="With --all: ordering (default file).")
    graph_show.add_argument("--limit", type=int, default=0, help="With --all: list at most N nodes.")

    graph_why = graph_subparsers.add_parser(
        "why",
        help=(
            "Cross-layer traversal for a file, symbol, requirement or failure: the requirements, changelog entries, "
            "commits, tests, failures and rules that touch it."
        ),
    )
    graph_why.add_argument("node", help="File path, symbol, requirement id (REQ-021) or failure id (FAIL-001).")
    graph_why.add_argument("--graph", default=GRAPH_DEFAULT_OUTPUT, help=f"Graph file to read. Defaults to {GRAPH_DEFAULT_OUTPUT}.")
    graph_why.add_argument("--limit", type=int, default=12, help="Items shown per section (default 12).")
    graph_why.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    graph_lineage = graph_subparsers.add_parser(
        "lineage",
        help=(
            "Trace everything upstream (what led to it) and downstream (what came from it) of any node, with no second "
            "endpoint: pick a requirement, commit, change-log entry, failure, file or symbol."
        ),
    )
    graph_lineage.add_argument("node", help="Requirement id (REQ-021), commit hash, failure id, file path or symbol.")
    graph_lineage.add_argument("--graph", default=GRAPH_DEFAULT_OUTPUT, help=f"Graph file to read. Defaults to {GRAPH_DEFAULT_OUTPUT}.")
    graph_lineage.add_argument("--up", action="store_true", help="Only trace upstream.")
    graph_lineage.add_argument("--down", action="store_true", help="Only trace downstream.")
    graph_lineage.add_argument("--depth", type=int, default=6, help="How many links to follow (default 6).")
    graph_lineage.add_argument("--code", action="store_true", help="Also follow calls, imports, inheritance and table mappings between code symbols.")
    graph_lineage.add_argument("--max-nodes", type=int, default=400, help="Most nodes returned per direction, nearest first (default 400).")
    graph_lineage.add_argument("--limit", type=int, default=12, help="Items shown per kind in text output (default 12).")
    graph_lineage.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    graph_sources = graph_subparsers.add_parser(
        "sources",
        help="Show what each layer would read from this project (requirements, changelog, git, tests, ledger) and what is missing.",
    )
    graph_sources.add_argument("--root", default=".", help="Project root. Defaults to the current repository.")
    graph_sources.add_argument("--write", action="store_true", help="Draft .ai/graph-config.json from what was found (only settings that differ from the defaults).")
    graph_sources.add_argument("--force", action="store_true", help="With --write: overwrite an existing config.")
    graph_sources.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    graph_benchmark = graph_subparsers.add_parser(
        "benchmark",
        help=(
            "Measure a targeted graph query against the naive alternative (grep for the name and read every match "
            "whole; a commit compares against `git show`), for one real node per kind this project's graph already "
            "has. Every size is measured live, against the current graph and repository -- nothing is canned."
        ),
    )
    graph_benchmark.add_argument("--graph", default=GRAPH_DEFAULT_OUTPUT, help=f"Graph file to read. Defaults to {GRAPH_DEFAULT_OUTPUT}.")
    graph_benchmark.add_argument("--root", default=".", help="Project root, for the naive grep/read comparison. Defaults to the current repository.")
    graph_benchmark.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    graph_timeline = graph_subparsers.add_parser(
        "timeline",
        help="Chronology of everything tied to a file, symbol, requirement or failure: commits, changelog entries, failures.",
    )
    graph_timeline.add_argument("node", help="File path, symbol, requirement id (REQ-021) or failure id (FAIL-001).")
    graph_timeline.add_argument("--graph", default=GRAPH_DEFAULT_OUTPUT, help=f"Graph file to read. Defaults to {GRAPH_DEFAULT_OUTPUT}.")
    graph_timeline.add_argument("--depth", type=int, default=1, help="Hops across the layers to follow (default 1: what is directly tied to the node).")
    graph_timeline.add_argument("--limit", type=int, default=40, help="Show the newest N events (0 for all; default 40).")
    graph_timeline.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    graph_impact = graph_subparsers.add_parser(
        "impact",
        help="What the pending change set reaches across the layers: requirements, failures, suites, tests, rules, commits.",
    )
    graph_impact.add_argument("--changed", metavar="BASE", help="Base commit for the change set (default: the merge-base `omni gate` uses).")
    graph_impact.add_argument("--graph", default=GRAPH_DEFAULT_OUTPUT, help=f"Graph file to read. Defaults to {GRAPH_DEFAULT_OUTPUT}.")
    graph_impact.add_argument("--depth", type=int, default=2, help="Cross-layer hops to follow from each changed path (default 2).")
    graph_impact.add_argument("--limit", type=int, default=40, help="Show at most N entries per bucket (default 40).")
    graph_impact.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    graph_view = graph_subparsers.add_parser(
        "view",
        help="Write an interactive, offline 3D viewer (rotate, pan, zoom, click to read, double-click to expand).",
    )
    graph_view.add_argument("--graph", default=GRAPH_DEFAULT_OUTPUT, help=f"Graph file to read. Defaults to {GRAPH_DEFAULT_OUTPUT}.")
    graph_view.add_argument("--output", default=VIEW_DEFAULT_OUTPUT, help=f"HTML output path. Defaults to {VIEW_DEFAULT_OUTPUT}.")
    graph_view.add_argument("--focus", help="Start with only this symbol's neighbourhood.")
    graph_view.add_argument("--depth", type=int, default=RENDER_DEFAULT_DEPTH, help=f"Hops around --focus. Defaults to {RENDER_DEFAULT_DEPTH}.")
    graph_view.add_argument("--max-initial", type=int, default=VIEW_DEFAULT_MAX_INITIAL, help=f"Nodes in the starting view, highest-degree first. Defaults to {VIEW_DEFAULT_MAX_INITIAL}.")
    graph_view.add_argument("--all", action="store_true", help="Start with every node in view (may be slow on large graphs).")
    graph_view.add_argument("--include-external", action="store_true", help="Start with external placeholder nodes visible.")
    graph_view.add_argument("--mode", choices=["auto", "2d", "3d"], default="auto", help="Start in the flat 2D view (no GPU memory needed) or the 3D view. auto remembers your last choice in the browser and otherwise starts in 2D.")
    graph_view.add_argument("--open", action="store_true", help="Open the result in the default browser.")

    graph_schema = graph_subparsers.add_parser(
        "schema",
        help="Print the database schema reconstructed from SQL migrations (tables, columns, keys, entities).",
    )
    graph_schema.add_argument("--graph", default=GRAPH_DEFAULT_OUTPUT, help=f"Graph file to read. Defaults to {GRAPH_DEFAULT_OUTPUT}.")
    graph_schema.add_argument("--table", help="Show only this table.")
    graph_schema.add_argument("--format", choices=["text", "mermaid", "json"], default="text", help="Output format (default text).")

    graph_render = graph_subparsers.add_parser(
        "render",
        help="Render the graph as a force-directed SVG node-link diagram.",
    )
    graph_render.add_argument(
        "--graph",
        default=GRAPH_DEFAULT_OUTPUT,
        help=f"Graph file to read. Defaults to {GRAPH_DEFAULT_OUTPUT}.",
    )
    graph_render.add_argument(
        "--output",
        default=RENDER_DEFAULT_OUTPUT,
        help=f"SVG output path. Defaults to {RENDER_DEFAULT_OUTPUT}.",
    )
    graph_render.add_argument(
        "--include-external",
        action="store_true",
        help="Also render unresolved external references (stdlib calls, third-party imports, etc.).",
    )
    graph_render.add_argument(
        "--max-nodes",
        type=int,
        default=RENDER_DEFAULT_MAX_NODES,
        help=(
            f"Cap on rendered nodes, keeping the highest-degree ones if exceeded. "
            f"Defaults to {RENDER_DEFAULT_MAX_NODES}."
        ),
    )
    graph_render.add_argument(
        "--focus",
        help="Only render the neighborhood around this symbol (name or qualified name) instead of the whole graph.",
    )
    graph_render.add_argument(
        "--depth",
        type=int,
        default=RENDER_DEFAULT_DEPTH,
        help=f"Hops out from --focus to include. Defaults to {RENDER_DEFAULT_DEPTH}. Ignored without --focus.",
    )
    graph_render.add_argument(
        "--all-layers",
        action="store_true",
        help="Also render the governance and assurance layers (requirements, changelog, commits, tests, failures, rules).",
    )
    graph_render.add_argument("--json", action="store_true", help="Print a machine-readable summary instead of a human summary.")

    # REQ-043: Arbiter findings in the graph
    graph_findings = graph_subparsers.add_parser(
        "findings",
        help="Arbiter findings from the newest gate report, grouped by directory with the requirements, failures and suites each one is tied to.",
    )
    graph_findings.add_argument("--graph", default=GRAPH_DEFAULT_OUTPUT, help=f"Graph file to read. Defaults to {GRAPH_DEFAULT_OUTPUT}.")
    graph_findings.add_argument("--under", help="Only findings in this directory (or file).")
    graph_findings.add_argument("--dimension", help="Only this Arbiter dimension (security, quality, drift, supply_chain, ...).")
    graph_findings.add_argument("--severity", help="Only this severity (critical, high, medium, low, info).")
    graph_findings.add_argument("--depth", type=int, default=2, help="Hops over non-code links to collect requirements, failures and suites (default 2).")
    graph_findings.add_argument("--tree", action="store_true", help="Print the text tree (the default).")
    graph_findings.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    graph_findings.add_argument("--view", action="store_true", help="Also write the interactive viewer, opened on the Findings tab.")
    graph_findings.add_argument("--output", default=VIEW_DEFAULT_OUTPUT, help=f"With --view: HTML output path. Defaults to {VIEW_DEFAULT_OUTPUT}.")
    graph_findings.add_argument("--open", action="store_true", help="With --view: open the result in the default browser.")

    context_parser = subparsers.add_parser(
        "context",
        help="Print the low-token file set for a context profile.",
    )
    context_parser.add_argument(
        "profile",
        nargs="?",
        default="minimum",
        help="Context profile: minimum, implementation, review, or deep_policy.",
    )
    context_parser.add_argument(
        "--extra",
        action="append",
        default=[],
        help="Additional file to include in the printed context set. May be repeated.",
    )
    context_parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON.",
    )

    adopt_parser = subparsers.add_parser(
        "adopt",
        help="Copy OmniEngineering into another project without overwriting by default.",
    )
    adopt_parser.add_argument(
        "--target",
        required=True,
        help="Target project root.",
    )
    adopt_parser.add_argument(
        "--tools",
        default="codex,cursor,universal",
        help=(
            "Comma-separated shims to copy. Available: "
            f"{', '.join(sorted(ADOPTION_TOOL_FILES))}, all, none. "
            "Defaults to codex,cursor,universal."
        ),
    )
    adopt_parser.add_argument(
        "--with-arbiter",
        nargs="?",
        const=ARBITER_DEFAULT_SOURCE,
        default=None,
        metavar="SOURCE",
        help=(
            "Also install Arbiter (a local checkout path, a git+https URL, or a pip spec; "
            f"default {ARBITER_DEFAULT_SOURCE}) and wire it in: .mcp.json, the completion.arbiter_gate "
            "command rule, a starter arbiter.yaml."
        ),
    )
    adopt_parser.add_argument(
        "--skip-pip",
        action="store_true",
        help="With --with-arbiter: write the wiring but do not pip install (arbiter is already installed).",
    )
    adopt_parser.add_argument(
        "--include-cli",
        action="store_true",
        help="Copy ./omni and make_ai.py.",
    )
    adopt_parser.add_argument(
        "--include-legal",
        action="store_true",
        help="Copy OmniEngineering license, notice, trademark, contribution, and LICENSES files.",
    )
    adopt_parser.add_argument(
        "--include-presentation",
        action="store_true",
        help="Copy optional public assets and design docs.",
    )
    adopt_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing target files. Use only after manual conflict review.",
    )
    adopt_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be copied without writing files.",
    )

    update_parser = subparsers.add_parser(
        "update",
        help="3-way-merge OmniEngineering template improvements into this workspace.",
    )
    update_parser.add_argument(
        "--source",
        required=True,
        help="Path to a git checkout of OmniEngineering to update from.",
    )
    update_parser.add_argument(
        "--include-legal",
        action="store_true",
        help="Also merge OmniEngineering license, notice, trademark, contribution, and LICENSES files.",
    )
    update_parser.add_argument(
        "--include-presentation",
        action="store_true",
        help="Also merge optional public assets and design docs.",
    )
    update_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing files.",
    )
    update_parser.add_argument(
        "--bootstrap",
        action="store_true",
        help=(
            "Start tracking without merging anything: write .ai/omni-version.json "
            "against --source's current ref. Use this once for a workspace that "
            "adopted OmniEngineering before update-tracking existed."
        ),
    )

    requirement_parser = subparsers.add_parser(
        "requirement",
        help="Manage requirement registry entries.",
    )
    requirement_subparsers = requirement_parser.add_subparsers(dest="requirement_command")
    requirement_add = requirement_subparsers.add_parser(
        "add",
        help="Add a requirement with low-overhead defaults.",
    )
    requirement_add.add_argument("--id", help="Requirement ID. Defaults to next REQ-###.")
    requirement_add.add_argument("--title", required=True, help="Short requirement title.")
    requirement_add.add_argument("--description", required=True, help="Requirement description.")
    requirement_add.add_argument("--category", default="General", help="Requirement category.")
    requirement_add.add_argument(
        "--priority",
        choices=["critical", "high", "medium", "low"],
        default="medium",
        help="Requirement priority.",
    )
    requirement_add.add_argument(
        "--status",
        choices=["completed", "pending", "proposed", "blocked", "needs_review"],
        default="proposed",
        help="Requirement status.",
    )
    requirement_add.add_argument("--scope", default="", help="Comma-separated minimum access scope.")
    requirement_add.add_argument("--acceptance", default="", help="Comma-separated acceptance criteria.")
    requirement_add.add_argument(
        "--validation",
        default="doctor,validate",
        help="Comma-separated validation required.",
    )
    requirement_add.add_argument(
        "--docs",
        default="changelog",
        help="Comma-separated documentation required.",
    )
    requirement_add.add_argument("--risks", default="", help="Comma-separated risk notes.")

    requirement_draft = requirement_subparsers.add_parser(
        "draft",
        help=(
            "Draft a requirement (status proposed) and a CHANGELOG.md stub from the files a commit or the current "
            "change set touched, so the manual step is editing a draft rather than writing one from nothing."
        ),
    )
    requirement_draft.add_argument("--commit", help="Draft from this commit's changed files and subject line, instead of the current (uncommitted) change set.")
    requirement_draft.add_argument("--id", help="Requirement ID. Defaults to next REQ-###.")
    requirement_draft.add_argument("--title", help="Override the guessed title (the commit subject, or a placeholder).")
    requirement_draft.add_argument("--category", help="Override the guessed category.")
    requirement_draft.add_argument("--no-changelog", action="store_true", help="Only draft the requirement; skip the CHANGELOG.md stub.")
    requirement_draft.add_argument("--force", action="store_true", help="Draft even if --commit already names a requirement ID.")

    requirement_show = requirement_subparsers.add_parser("show", help="Print one requirement by ID (active or archived).")
    requirement_show.add_argument("id", help="Requirement ID, e.g. REQ-042 or 42.")
    requirement_show.add_argument("--json", action="store_true", help="Print the raw JSON entry.")

    requirement_list = requirement_subparsers.add_parser("list", help="One line per requirement (active registry by default).")
    requirement_list.add_argument("--status", action="append", choices=REQUIREMENT_STATUSES, help="Filter by status. May be repeated.")
    requirement_list.add_argument("--all", action="store_true", help="Include archived requirements.")
    requirement_list.add_argument("--last", type=int, default=0, help="Show only the last N entries.")
    requirement_list.add_argument("--json", action="store_true", help="Print raw JSON entries.")

    requirement_search = requirement_subparsers.add_parser("search", help="Case-insensitive text search across requirements.")
    requirement_search.add_argument("text", nargs="+", help="Text to look for.")
    requirement_search.add_argument("--active-only", action="store_true", help="Skip archived requirements.")

    requirement_update = requirement_subparsers.add_parser("update", help="Change a requirement's status, title, description, or append a risk note.")
    requirement_update.add_argument("id", help="Requirement ID.")
    requirement_update.add_argument("--status", choices=REQUIREMENT_STATUSES, help="New status.")
    requirement_update.add_argument("--title", help="New title.")
    requirement_update.add_argument("--description", help="New description.")
    requirement_update.add_argument("--note", help="Append a risk note.")

    requirement_complete = requirement_subparsers.add_parser("complete", help="Mark a requirement completed.")
    requirement_complete.add_argument("id", help="Requirement ID.")
    requirement_complete.add_argument("--note", help="Append a risk note.")
    requirement_complete.add_argument(
        "--no-failure-entry",
        help="For defect/bug/fix requirements only: why no failure-ledger entry is warranted (10+ characters, recorded as a risk note).",
    )
    requirement_complete.add_argument(
        "--no-arbiter-check",
        help="Complete without a fresh, passing Arbiter report: why that is safe here (10+ characters, recorded as a risk note).",
    )

    test_parser = subparsers.add_parser(
        "test",
        help="Register and inspect test suites (paths, framework, command); auto-detected suites feed the graph.",
    )
    test_subparsers = test_parser.add_subparsers(dest="test_command")
    test_detect = test_subparsers.add_parser("detect", help="Find test suites from file contents and CI commands; --write registers them.")
    test_detect.add_argument("--write", action="store_true", help=f"Add the detected suites to {TEST_SUITES_PATH}.")
    test_detect.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    test_add = test_subparsers.add_parser("add", help="Register a test suite by hand.")
    test_add.add_argument("--name", required=True, help="Human name, e.g. 'Backend JUnit'.")
    test_add.add_argument("--id", help="Suite id; defaults to a slug of the name.")
    test_add.add_argument("--paths", required=True, help="Comma-separated files, directories or globs that hold the tests.")
    test_add.add_argument("--kind", choices=["unit", "integration", "e2e", "validation", "smoke", "other"], default="unit")
    test_add.add_argument("--framework", default="", help="junit, vitest, jest, pytest, playwright, script, ...")
    test_add.add_argument("--command", dest="run_command", default="", help="How to run it, e.g. 'cd backend && mvn test'.")
    test_add.add_argument("--covers", default="", help="Comma-separated code paths this suite is meant to cover.")
    test_add.add_argument("--notes", default="")
    test_add.add_argument("--allow-missing", action="store_true", help="Register even if a path matches no file yet.")
    test_remove = test_subparsers.add_parser("remove", help="Remove a registered suite.")
    test_remove.add_argument("id")
    test_subparsers.add_parser("list", help="Registered suites plus any detected but unregistered.")
    test_subparsers.add_parser("check", help="Verify every registered path matches files, and report unregistered tests.")
    test_run = test_subparsers.add_parser("run", help="Run registered suites: all of them, the named ones, or only those the pending change set impacts.")
    test_run.add_argument("names", nargs="*", help="Suite ids or names to run (default: every registered or detected suite).")
    test_run.add_argument("--impacted", action="store_true", help="Only suites that `omni graph impact` ties to the change set; every suite when no graph exists.")
    test_run.add_argument("--changed", metavar="BASE", help="Base commit for the change set (default: the merge-base `omni gate` uses).")
    test_run.add_argument("--timeout", type=float, default=TEST_RUN_DEFAULT_TIMEOUT, help="Seconds each suite may run (default 900).")
    test_run.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    failure_parser = subparsers.add_parser(
        "failure",
        help="Record what went wrong, why, and what now prevents it (the failure ledger, a graph layer).",
    )
    failure_subparsers = failure_parser.add_subparsers(dest="failure_command")

    def add_failure_fields(sub: argparse.ArgumentParser, creating: bool) -> None:
        sub.add_argument("--status", choices=FAILURE_STATUSES, default="open" if creating else None, help="open, fixed, mitigated or wontfix.")
        sub.add_argument("--severity", choices=["critical", "high", "medium", "low"], default="medium" if creating else None)
        sub.add_argument("--requirement", help="Requirement being worked when this surfaced (REQ-###).")
        sub.add_argument("--symptom", required=creating, help="What was observed: the error text or wrong behaviour.")
        sub.add_argument("--root-cause", help="WHY it happened. The reasoning is the point of the ledger.")
        sub.add_argument("--detected", help="How it was found (test, review, user report, production).")
        sub.add_argument("--affected", help="Comma-separated files, directories or symbols it affected.")
        sub.add_argument("--fix", help="What changed to fix it.")
        sub.add_argument("--commits", help="Comma-separated commit hashes that fixed it.")
        sub.add_argument("--tests", help="Comma-separated regression tests (path, path::name, or qualified name).")
        sub.add_argument("--no-test-reason", help="If no regression test is practical, say why.")
        sub.add_argument("--prevention-rules", help="Comma-separated rule ids (or playbook paths) that stop it recurring.")
        sub.add_argument("--prevention-notes", help="What now prevents a repeat, in words.")
        sub.add_argument("--recurrence-of", help="Earlier failure id this repeats.")

    failure_add = failure_subparsers.add_parser("add", help="Record a failure.")
    failure_add.add_argument("--id", help="Failure ID. Defaults to the next FAIL-###.")
    failure_add.add_argument("--title", required=True, help="Short title.")
    failure_add.add_argument("--date", help="YYYY-MM-DD; defaults to today.")
    add_failure_fields(failure_add, creating=True)

    failure_update = failure_subparsers.add_parser("update", help="Add reasoning, tests or prevention to a failure. Lists append; scalars replace.")
    failure_update.add_argument("id", help="Failure ID.")
    failure_update.add_argument("--title", help="New title.")
    add_failure_fields(failure_update, creating=False)

    failure_show = failure_subparsers.add_parser("show", help="Print one failure.")
    failure_show.add_argument("id")
    failure_list = failure_subparsers.add_parser("list", help="One line per failure.")
    failure_list.add_argument("--status", choices=FAILURE_STATUSES)
    failure_search = failure_subparsers.add_parser("search", help="Case-insensitive text search across failures.")
    failure_search.add_argument("text")
    failure_subparsers.add_parser("check", help="Verify the ledger is complete and every reference resolves.")

    requirement_archive = requirement_subparsers.add_parser(
        "archive",
        help="Move older completed (and all withdrawn) requirements into requirements-archive.json to keep the live registry small.",
    )
    requirement_archive.add_argument("--id", help="Comma-separated requirement IDs to archive instead of the default sweep; each must be completed or withdrawn.")
    requirement_archive.add_argument("--keep-recent", type=int, default=25, help="Completed requirements to keep active (default 25).")
    requirement_archive.add_argument("--dry-run", action="store_true", help="Report what would move without writing.")

    # REQ-044
    requirement_renumber = requirement_subparsers.add_parser(
        "renumber",
        help=(
            "Change the id prefix of every requirement, keeping the number (REQ-012 -> ARB-012), in the registry, "
            "the archive, the failure ledger, the gate waivers and the governance text that cites them; records "
            "id_aliases so commits and waivers that cite the old ids stay valid. Code, tests, .git, vendored "
            "workspaces and arbiter-out are never touched."
        ),
    )
    requirement_renumber.add_argument("--prefix", required=True, help="New prefix: upper-case letters only, e.g. ARB.")
    requirement_renumber.add_argument("--dry-run", action="store_true", help="Print the per-file change counts without writing.")
    requirement_renumber.add_argument(
        "--paths",
        nargs="+",
        metavar="GLOB",
        help=f"Globs of text files to rewrite instead of the default list ({', '.join(RENUMBER_DEFAULT_PATHS)}).",
    )

    gate_parser = subparsers.add_parser(
        "gate",
        help="Check that changed files carry the changelog/registry updates the completion rulepack requires.",
    )
    gate_parser.add_argument("--hook", action="store_true", help="Claude Code Stop-hook mode: read hook JSON on stdin, exit 2 to block.")
    gate_parser.add_argument("--no-memo", action="store_true",
                             help="Run every command rule, ignoring the per-rule pass memo in the gate state file (REQ-047).")

    waive_parser = subparsers.add_parser("waive", help="Record an explicit, auditable waiver for a gate rule.")
    waive_parser.add_argument("rule", help="Gate rule ID, e.g. completion.changelog_gate.")
    waive_parser.add_argument("--reason", required=True, help="Why the gate does not apply to this change.")
    waive_parser.add_argument("--requirement", help="Requirement ID the waiver belongs to.")

    mcp_parser = subparsers.add_parser(
        "mcp",
        help="MCP (Model Context Protocol) server: the graph and registries as tools, for any MCP-speaking assistant.",
    )
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_command")
    mcp_subparsers.add_parser(
        "serve",
        help="Serve the tools over stdio (JSON-RPC 2.0, newline-delimited) until stdin closes. Point an MCP client's command at `omni mcp serve`.",
    )
    mcp_tools = mcp_subparsers.add_parser("tools", help="List the available tools without starting the server (for a quick check, or piping into a client's config).")
    mcp_tools.add_argument("--json", action="store_true", help="Print the full tool specs (name, description, input schema) as JSON.")

    arbiter_parser = subparsers.add_parser("arbiter", help="Install and wire the Arbiter evaluator beside this workspace.")
    arbiter_subparsers = arbiter_parser.add_subparsers(dest="arbiter_command")
    arbiter_install_parser = arbiter_subparsers.add_parser(
        "install",
        help="pip install Arbiter and register it: .mcp.json, the completion.arbiter_gate command rule, a starter arbiter.yaml.",
    )
    arbiter_install_parser.add_argument("--source", default=ARBITER_DEFAULT_SOURCE,
                                        help=f"Local checkout, git+https URL or pip spec (default {ARBITER_DEFAULT_SOURCE}).")
    arbiter_install_parser.add_argument("--target", default=".", help="Project root (default: current directory).")
    arbiter_install_parser.add_argument("--skip-pip", action="store_true", help="Write the wiring only; arbiter is already installed.")
    arbiter_install_parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing or installing.")
    arbiter_baseline_parser = arbiter_subparsers.add_parser(
        "baseline",
        help="Cut .arbiter/baseline.json from a full offline scan so the gate reports only what is new since it; commit the file.",
    )
    arbiter_baseline_parser.add_argument("--target", default=".", help="Project root (default: current directory).")
    arbiter_baseline_parser.add_argument("--refresh", action="store_true",
                                         help="Re-cut an existing baseline (prunes ids the fresh scan no longer finds); needs the last gate report green.")
    arbiter_baseline_parser.add_argument("--force", action="store_true", help="With --refresh: proceed even when the last gate report failed or is missing.")
    arbiter_update_parser = arbiter_subparsers.add_parser(
        "update",
        help="Upgrade Arbiter: pip --upgrade, the completion.arbiter_gate rule to the current template, a vendored arbiter/ subtree pull, and re-record the version.",
    )
    arbiter_update_parser.add_argument("--target", default=".", help="Project root (default: current directory).")
    arbiter_update_parser.add_argument("--source", default=None,
                                       help=f"Local checkout, git+https URL or pip spec (default: the source recorded at install, else {ARBITER_DEFAULT_SOURCE}).")
    arbiter_update_parser.add_argument("--keep-rule", action="store_true", help="Leave the completion.arbiter_gate rule as it is.")
    arbiter_update_parser.add_argument("--squash", action="store_true", help="Pull the vendored arbiter/ subtree with --squash (only for a history that was squashed).")
    arbiter_update_parser.add_argument("--force", action="store_true", help="Pull the subtree even when --squash disagrees with how arbiter/ was added.")
    arbiter_update_parser.add_argument("--skip-pip", action="store_true", help="Do not pip install --upgrade.")
    arbiter_update_parser.add_argument("--dry-run", action="store_true", help="Print every command without running or writing anything.")
    arbiter_sync_parser = arbiter_subparsers.add_parser(
        "sync",
        help="Level everything in one go: pull the vendored arbiter/ subtree (or pip --upgrade), reinstall, re-record the version, "
             "refresh the baseline and run doctor; stops at the first failing step.",
    )
    arbiter_sync_parser.add_argument("--target", default=".", help="Project root (default: current directory).")
    arbiter_sync_parser.add_argument("--source", default=None,
                                     help=f"Git URL or pip source (default: the source recorded at install, else {ARBITER_DEFAULT_SOURCE}).")
    arbiter_sync_parser.add_argument("--branch", default="main", help="Branch to pull into arbiter/ in subtree mode (default main).")
    arbiter_sync_parser.add_argument("--skip-baseline", action="store_true", help="Do not run `omni arbiter baseline` after the install.")
    arbiter_sync_parser.add_argument("--dry-run", action="store_true", help="Print the steps and their commands without running anything.")

    hook_parser = subparsers.add_parser("hook", help="Install assistant hooks that enforce the gate.")
    hook_subparsers = hook_parser.add_subparsers(dest="hook_command")
    hook_install = hook_subparsers.add_parser("install", help="Install the Claude Code Stop hook that runs `omni gate`.")
    hook_install.add_argument("--settings", default=".claude/settings.json", help="Claude Code project settings file.")
    hook_install_git = hook_subparsers.add_parser(
        "install-git",
        help="Install a portable git pre-commit hook (Linux, macOS, Windows) that runs `omni gate`, no assistant required.",
    )
    hook_install_git.add_argument("--force", action="store_true", help="Overwrite an existing pre-commit hook that omni did not install.")
    hook_install_git.add_argument(
        "--with-graph-rebuild",
        action="store_true",
        help="Also install a post-commit hook that rebuilds the graph in the background after every commit (never blocks the commit).",
    )

    rule_parser = subparsers.add_parser("rule", help="Manage structured rulepacks.")
    rule_subparsers = rule_parser.add_subparsers(dest="rule_command")
    rule_add = rule_subparsers.add_parser(
        "add",
        help="Add a rule to a JSON rulepack.",
    )
    rule_add.add_argument(
        "--rulepack",
        required=True,
        help="Rulepack alias or path, e.g. controlled, completion, data, hci, oop.",
    )
    rule_add.add_argument("--id", help="Rule ID. Defaults to <rulepack_id>.<slug>.")
    rule_add.add_argument("--name", help="Short slug source when --id is omitted.")
    rule_add.add_argument("--statement", required=True, help="Rule statement.")
    rule_add.add_argument(
        "--severity",
        choices=["required", "recommended", "advisory"],
        default="required",
        help="Rule severity.",
    )
    rule_add.add_argument("--scope", default="", help="Comma-separated scope tags.")
    rule_add.add_argument("--validation-type", help="Optional validation hint type.")
    rule_add.add_argument("--validation-target", help="Optional validation target.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    command = args.command or "sync"
    if command == "sync":
        return run_sync(args)
    if command in {"doctor", "validate"}:
        return run_doctor(args)
    if command == "map":
        return run_map(args)
    if command == "graph":
        if args.graph_command == "build":
            return run_graph_build(args)
        if args.graph_command == "trace":
            return run_graph_trace(args)
        if args.graph_command == "show":
            return run_graph_show(args)
        if args.graph_command == "render":
            return run_graph_render(args)
        if args.graph_command == "view":
            return run_graph_view(args)
        if args.graph_command == "schema":
            return run_graph_schema(args)
        if args.graph_command == "why":
            return run_graph_why(args)
        if args.graph_command == "lineage":
            return run_graph_lineage(args)
        if args.graph_command == "timeline":
            return run_graph_timeline(args)
        if args.graph_command == "impact":
            return run_graph_impact(args)
        if args.graph_command == "sources":
            return run_graph_sources(args)
        if args.graph_command == "benchmark":
            return run_graph_benchmark(args)
        if args.graph_command == "findings":  # REQ-043
            return run_graph_findings(args)
        parser.error("graph requires a subcommand (build, trace, show, why, lineage, timeline, impact, sources, benchmark, render, view, schema, findings)")
    if command == "test":
        handlers = {
            "detect": run_test_detect, "add": run_test_add, "remove": run_test_remove,
            "list": run_test_list, "check": run_test_check, "run": run_test_run,
        }
        if args.test_command in handlers:
            return handlers[args.test_command](args)
        parser.error("test requires a subcommand (detect, add, remove, list, check, run)")
    if command == "failure":
        handlers = {
            "add": run_failure_add, "update": run_failure_update, "show": run_failure_show,
            "list": run_failure_list, "search": run_failure_search, "check": run_failure_check,
        }
        if args.failure_command in handlers:
            return handlers[args.failure_command](args)
        parser.error("failure requires a subcommand (add, update, show, list, search, check)")
    if command == "context":
        return run_context(args)
    if command == "adopt":
        return run_adopt(args)
    if command == "update":
        return run_update(args)
    if command == "requirement":
        handlers = {
            "add": run_requirement_add,
            "draft": run_requirement_draft,
            "show": run_requirement_show,
            "list": run_requirement_list,
            "search": run_requirement_search,
            "update": run_requirement_update,
            "complete": run_requirement_complete,
            "archive": run_requirement_archive,
            "renumber": run_requirement_renumber,  # REQ-044
        }
        if args.requirement_command in handlers:
            return handlers[args.requirement_command](args)
        parser.error("requirement requires a subcommand")
    if command == "gate":
        return run_gate(args)
    if command == "waive":
        return run_waive(args)
    if command == "arbiter":
        if args.arbiter_command == "install":
            return run_arbiter_install(args)
        if args.arbiter_command == "baseline":
            return run_arbiter_baseline(args)
        if args.arbiter_command == "update":
            return run_arbiter_update(args)
        if args.arbiter_command == "sync":
            return run_arbiter_sync(args)
        parser.error("arbiter requires a subcommand (install, baseline, update, sync)")
    if command == "hook":
        if args.hook_command == "install":
            return run_hook_install(args)
        if args.hook_command == "install-git":
            return run_hook_install_git(args)
        parser.error("hook requires a subcommand (install, install-git)")
    if command == "mcp":
        if args.mcp_command == "serve":
            return run_mcp_serve(args)
        if args.mcp_command == "tools":
            return run_mcp_tools(args)
        parser.error("mcp requires a subcommand (serve, tools)")
    if command == "rule":
        if args.rule_command == "add":
            return run_rule_add(args)
        parser.error("rule requires a subcommand")

    parser.error(f"Unknown command: {command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
