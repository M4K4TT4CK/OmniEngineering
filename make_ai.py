import argparse
import fnmatch
import json
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
    ".ai/schemas/rulepack.schema.json",
    ".ai/schemas/requirements.schema.json",
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
   `.ai/project-configuration.md`, and `.ai/requirements/requirements.json`
   before editing when they are available.
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
14. Update `.ai/requirements/requirements.json` when a requirement is added,
    completed, blocked, or materially changed.
15. Run relevant validation. For OmniContext workspace changes, run
    `omni doctor` or `./omni doctor`; when assistant entrypoint files change,
    run `omni sync` or `./omni sync`.
16. Do not claim completion if validation was skipped. Explain why it was not
    run.
17. Final output must include requirement ID and status, files changed,
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

{FALLBACK_CONTRACT}
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

<!-- Generated by OmniEngineering. Run `./omni sync` to refresh. -->

This file is intentionally small so repository-root assistant files do not
clog the main workspace.

Read `{source_path}` for the full tool-specific instructions, then follow
`.ai/context-brief.md` and `.ai/context-manifest.json` for the complete loading
order.

If `{source_path}` is unavailable, read `.ai/entrypoints/fallback-contract.md`
and apply it as the controlling instruction set.
"""


ASSISTANT_POINTERS = {
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
    ".ai/schemas/rulepack.schema.json",
    ".ai/schemas/requirements.schema.json",
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

ADOPTION_CLI_FILES = ["omni", "make_ai.py", "omni_graph.py"]
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
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


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
    for requirement in requirements.get("requirements", []):
        requirement_id = str(requirement.get("id", ""))
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


def validate_requirements(requirements: Any, report: DoctorReport) -> None:
    if not isinstance(requirements, dict):
        report.error("Requirements registry must be a JSON object")
        return

    items = requirements.get("requirements")
    if not isinstance(items, list):
        report.error("Requirements registry must contain a requirements array")
        return

    seen_ids: set[str] = set()
    for index, requirement in enumerate(items, start=1):
        if not isinstance(requirement, dict):
            report.error(f"Requirement entry {index} must be an object")
            continue

        missing = REQUIRED_REQUIREMENT_KEYS - set(requirement)
        if missing:
            report.error(
                f"Requirement entry {requirement.get('id', index)} missing keys: "
                f"{sorted(missing)}"
            )

        requirement_id = requirement.get("id")
        if not isinstance(requirement_id, str) or not re.fullmatch(r"[A-Z]+-\d{3}", requirement_id):
            report.error(f"Requirement entry {index} has invalid id: {requirement_id}")
            continue

        if requirement_id in seen_ids:
            report.error(f"Duplicate requirement id: {requirement_id}")
        seen_ids.add(requirement_id)

    if not report.errors:
        report.pass_check("Requirements registry is structurally valid")
    elif seen_ids:
        report.warning("Requirements registry was partially readable")


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
            or value in ASSISTANT_POINTERS
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
    for file_path, expected in ASSISTANT_POINTERS.items():
        path = Path(file_path)
        if not path.is_file():
            missing.append(file_path)
            continue
        if path.read_text(encoding="utf-8") != expected:
            drifted.append(file_path)

    for file_path in missing:
        report.error(f"Missing assistant pointer file: {file_path}")
    for file_path in drifted:
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

    for item in Path(".").iterdir():
        name = item.name
        if item.is_dir():
            if name in ROOT_CLUTTER_DIRS:
                root_clutter.append(name)
            elif name not in ALLOWED_ROOT_DIRS:
                misplaced.append(name + "/")
        elif item.is_file() and name not in ALLOWED_ROOT_FILES:
            misplaced.append(name)

    for file_path in LOCAL_ONLY_PUBLIC_PATHS:
        if Path(file_path).exists():
            misplaced.append(file_path)

    for file_path in ASSISTANT_POINTERS:
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
    for file_path, content in ASSISTANT_POINTERS.items():
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


def run_graph_build(args: argparse.Namespace) -> int:
    if not require_omni_graph():
        return 1

    root = Path(args.root)
    if not root.is_dir():
        print(f"Project root not found: {root}", file=sys.stderr)
        return 1

    languages = split_csv(args.languages)
    unknown = [language for language in languages if language not in omni_graph.LANGUAGE_EXTENSIONS]
    if unknown:
        print(
            f"Unsupported language(s): {', '.join(unknown)}. "
            f"Supported: {', '.join(sorted(omni_graph.LANGUAGE_EXTENSIONS))}",
            file=sys.stderr,
        )
        return 1

    try:
        graph, semantic_info = omni_graph.build_graph(root.resolve(), languages, semantic=args.semantic)
    except omni_graph.GraphDependencyError as exc:
        print(str(exc), file=sys.stderr)
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
        "semantic_pass": semantic_info,
    }
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"Wrote {summary['nodes']} nodes and {summary['edges']} edges to {output}")
    print(f"  EXTRACTED edges: {extracted}   INFERRED edges: {inferred}")
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
    print("Then inspect only the task-relevant project files selected from .ai/project-map.md.")
    return 0


def run_doctor() -> int:
    report = DoctorReport()
    verify_required_ai_files(report)
    parsed = validate_json_files(report)
    validate_context_manifest(parsed.get(".ai/context-manifest.json"), report)
    validate_ruleset(parsed.get(".ai/rules/universal-engineering-ruleset.json"), report)
    validate_rulepacks(parsed, report)
    validate_requirements(parsed.get(".ai/requirements/requirements.json"), report)
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

    existing_ids = {
        requirement.get("id")
        for requirement in requirements.get("requirements", [])
        if isinstance(requirement, dict)
    }
    if requirement_id in existing_ids:
        print(f"Requirement already exists: {requirement_id}", file=sys.stderr)
        return 1

    requirements.setdefault("requirements", []).append(new_requirement)
    write_json(path, requirements)
    print(f"Added requirement {requirement_id}: {args.title}")
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
        default="python,javascript,typescript",
        help="Comma-separated languages to parse. Requires the [graph] extra.",
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
        help="List the direct EXTRACTED/INFERRED edges for one symbol.",
    )
    graph_show.add_argument("node", help="Name or qualified name to inspect.")
    graph_show.add_argument(
        "--graph",
        default=GRAPH_DEFAULT_OUTPUT,
        help=f"Graph file to read. Defaults to {GRAPH_DEFAULT_OUTPUT}.",
    )
    graph_show.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

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
        parser.error("graph requires a subcommand (build, trace, show, render)")
    if command == "context":
        return run_context(args)
    if command == "adopt":
        return run_adopt(args)
    if command == "update":
        return run_update(args)
    if command == "requirement":
        if args.requirement_command == "add":
            return run_requirement_add(args)
        parser.error("requirement requires a subcommand")
    if command == "rule":
        if args.rule_command == "add":
            return run_rule_add(args)
        parser.error("rule requires a subcommand")

    parser.error(f"Unknown command: {command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
