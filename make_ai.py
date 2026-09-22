import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
REQUIREMENT_STATUSES = ["completed", "pending", "proposed", "blocked", "needs_review"]
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

    @property
    def ok(self) -> bool:
        return not self.errors


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
    payload = {
        "source": str(source_root),
        "ref": git_current_ref(source_root),
        "last_synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    write_json(target_root / OMNI_VERSION_FILE, payload)


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

    if not report.errors:
        report.pass_check("Requirements registry is structurally valid (types and enums checked)")
    elif seen_ids:
        report.warning("Requirements registry was partially readable")


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
    print(f"{node['name']} ({node['kind']})" + (f"  {node['file']}" if node.get("file") else ""))
    if node.get("summary"):
        print(f"  {node['summary']}")
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
    known = {i.get("id") for i in ledger.get("failures", []) if isinstance(i, dict)}
    for item in ledger.get("failures", []):
        if not isinstance(item, dict):
            continue
        fid = item.get("id")
        if item.get("requirement") and ids and item["requirement"] not in ids:
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


def build_doctor_report() -> DoctorReport:
    report = DoctorReport()
    verify_required_ai_files(report)
    parsed = validate_json_files(report)
    validate_context_manifest(parsed.get(".ai/context-manifest.json"), report)
    validate_ruleset(parsed.get(".ai/rules/universal-engineering-ruleset.json"), report)
    validate_rulepacks(parsed, report)
    validate_requirements(parsed.get(".ai/requirements/requirements.json"), report)
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
    validate_cli_entrypoints(report)
    validate_omni_version_present(report)
    return report


def run_doctor() -> int:
    report = build_doctor_report()
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

    results = [
        copy_adoption_path(source_root, target_root, relative_path, args.force, args.dry_run)
        for relative_path in files
    ]
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

    if missing:
        return 1
    return 1 if skipped and not args.dry_run else 0


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
    return value


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


DEFECT_CATEGORY = re.compile(r"defect|bug|fix|regress|incident|failure", re.IGNORECASE)


def run_requirement_complete(args: argparse.Namespace) -> int:
    found = find_requirement(args.id)
    if found is not None and DEFECT_CATEGORY.search(str(found[2].get("category", ""))):
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
    args.status = "completed"
    args.title = None
    args.description = None
    return run_requirement_update(args)


def run_requirement_archive(args: argparse.Namespace) -> int:
    active = load_json(REQUIREMENTS_PATH)
    items = active.get("requirements", [])
    completed_positions = [
        index for index, item in enumerate(items) if isinstance(item, dict) and item.get("status") == "completed"
    ]
    keep_positions = set(completed_positions[-args.keep_recent:]) if args.keep_recent > 0 else set()
    archive_positions = [index for index in completed_positions if index not in keep_positions]
    if not archive_positions:
        print("Nothing to archive.")
        return 0

    to_archive = [items[index] for index in archive_positions]
    remaining = [item for index, item in enumerate(items) if index not in set(archive_positions)]
    print(
        f"Archiving {len(to_archive)} completed requirement(s); "
        f"{len(remaining)} stay active (all non-completed + the {args.keep_recent} most recent completed)."
    )
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
EXECUTABLE_VALIDATION_TYPES = {"co_changed", "requirement_registry_entry", "content_forbidden"}


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
        prefix = str(load_json(target).get("requirement_id_prefix", "REQ")) if target.is_file() else "REQ"
    except (OSError, json.JSONDecodeError):
        return None  # a malformed registry is already reported by doctor; do not double up here
    pattern = re.compile(rf"\b{re.escape(prefix)}-\d+\b")
    cited: set[str] = set()
    if base:
        cited.update(pattern.findall(git_run("log", f"{base}..HEAD", "--format=%B") or ""))
    if "CHANGELOG.md" in changed and Path("CHANGELOG.md").is_file():
        cited.update(pattern.findall(Path("CHANGELOG.md").read_text(encoding="utf-8", errors="replace")))
    if not cited:
        return None
    unknown = sorted(cited - all_requirement_ids())
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


def gate_evaluate(changed: set[str], waivers: dict[str, str], base: str | None = None) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    waived: list[str] = []
    for rule in gate_rules():
        validation = rule["validation"]
        vtype = validation.get("type")
        if vtype == "co_changed":
            failure = _gate_check_co_changed(rule, validation, changed)
        elif vtype == "requirement_registry_entry":
            failure = _gate_check_requirement_registry_entry(rule, validation, changed, base)
        elif vtype == "content_forbidden":
            failure = _gate_check_content_forbidden(rule, validation, changed)
        else:
            continue
        if failure is None:
            continue
        rule_id = str(rule["id"])
        if rule_id in waivers:
            waived.append(f"{rule_id}: waived ({waivers[rule_id]})")
            continue
        failures.append(failure)
    return failures, waived


def gate_state_file() -> Path | None:
    git_dir = git_run("rev-parse", "--git-dir")
    return Path(git_dir.strip()) / "omni-gate-last.json" if git_dir else None


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
    failures: list[str] = []
    waived: list[str] = []
    if changed:
        doctor = build_doctor_report()
        failures.extend(f"doctor: {message}" for message in doctor.errors)
        rule_failures, waived = gate_evaluate(changed, gate_waivers(base), base)
        failures.extend(rule_failures)

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

    state_file = gate_state_file()
    signature = gate_signature(failures, changed)
    if state_file is not None:
        try:
            if json.loads(state_file.read_text(encoding="utf-8")).get("signature") == signature:
                return 0
        except (OSError, ValueError):
            pass
        try:
            state_file.write_text(json.dumps({"signature": signature}), encoding="utf-8")
        except OSError:
            pass
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
    subparsers.add_parser("doctor", help="Check OmniEngineering workspace health.")
    subparsers.add_parser("validate", help="Alias for doctor.")

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
        help="Move older completed requirements into requirements-archive.json to keep the live registry small.",
    )
    requirement_archive.add_argument("--keep-recent", type=int, default=25, help="Completed requirements to keep active (default 25).")
    requirement_archive.add_argument("--dry-run", action="store_true", help="Report what would move without writing.")

    gate_parser = subparsers.add_parser(
        "gate",
        help="Check that changed files carry the changelog/registry updates the completion rulepack requires.",
    )
    gate_parser.add_argument("--hook", action="store_true", help="Claude Code Stop-hook mode: read hook JSON on stdin, exit 2 to block.")

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
        return run_doctor()
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
        if args.graph_command == "sources":
            return run_graph_sources(args)
        if args.graph_command == "benchmark":
            return run_graph_benchmark(args)
        parser.error("graph requires a subcommand (build, trace, show, why, lineage, timeline, sources, benchmark, render, view, schema)")
    if command == "test":
        handlers = {
            "detect": run_test_detect, "add": run_test_add, "remove": run_test_remove,
            "list": run_test_list, "check": run_test_check,
        }
        if args.test_command in handlers:
            return handlers[args.test_command](args)
        parser.error("test requires a subcommand (detect, add, remove, list, check)")
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
        }
        if args.requirement_command in handlers:
            return handlers[args.requirement_command](args)
        parser.error("requirement requires a subcommand")
    if command == "gate":
        return run_gate(args)
    if command == "waive":
        return run_waive(args)
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
