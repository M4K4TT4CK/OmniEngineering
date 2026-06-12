import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any


REQUIRED_AI_FILES = [
    ".ai/core-context.md",
    ".ai/project-configuration.md",
    ".ai/.ignore",
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

FALLBACK_CONTRACT = """## Fallback Operating Contract

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
5. Inspect only files needed for the active requirement. Do not scan the whole
   repository unless the task cannot be completed safely without it.
6. Do not read or expose `.env`, `.env.*`, private keys, certificates,
   credentials, database files, logs, build artifacts, dependency folders, cache
   directories, or anything listed in `.ai/.ignore`.
7. Do not modify unrelated files, unrelated deployment scripts, unrelated
   infrastructure, generated dependency folders, secrets, credentials, or local
   environment files.
8. Make the smallest safe maintainable change. Preserve existing behavior unless
   it conflicts with the active requirement.
9. Do not introduce dependencies, schema changes, destructive data changes, or
   public interface changes unless the requirement explicitly calls for them.
10. Use clear names, focused functions, explicit data contracts, and existing
    project conventions.
11. Update relevant docs when behavior, setup, commands, architecture, APIs,
    data models, or workflows change.
12. Update `CHANGELOG.md` after each completed task.
13. Update `.ai/requirements/requirements.json` when a requirement is added,
    completed, blocked, or materially changed.
14. Run relevant validation. For OmniContext workspace changes, run
    `omni doctor` or `./omni doctor`; when assistant entrypoint files change,
    run `omni sync` or `./omni sync`.
15. Do not claim completion if validation was skipped. Explain why it was not
    run.
16. Final output must include requirement ID and status, files changed,
    validation performed, documentation and changelog status, risks or
    follow-ups, a commit entry sentence, and pull request information.
"""

ASSISTANT_POINTERS = {
    "CLAUDE.md": f"""# Claude Configuration

Read and prioritize all rules, styles, and workflows located inside the `.ai/`
directory before writing code. Treat `.ai/rules/universal-engineering-ruleset.json`
as the controlling global ruleset. Apply the controlled implementation workflow,
security guardrails, completion workflow, project configuration, and
project-specific rules.

{FALLBACK_CONTRACT}
""",
    ".cursorrules": f"""# Cursor Configuration

Read and prioritize all rules, styles, and workflows located inside the `.ai/`
directory before writing code. Treat `.ai/rules/universal-engineering-ruleset.json`
as the controlling global ruleset. Apply the controlled implementation workflow,
security guardrails, completion workflow, project configuration, and
project-specific rules.

{FALLBACK_CONTRACT}
""",
    ".github/copilot-instructions.md": f"""# Copilot Configuration

Read and prioritize all rules, styles, and workflows located inside the `.ai/`
directory before writing code. Treat `.ai/rules/universal-engineering-ruleset.json`
as the controlling global ruleset. Apply the controlled implementation workflow,
security guardrails, completion workflow, project configuration, and
project-specific rules.

{FALLBACK_CONTRACT}
""",
}

JSON_FILES = [
    ".ai/rules/universal-engineering-ruleset.json",
    *RULEPACK_FILES,
    ".ai/requirements/requirements.json",
    ".ai/schemas/rulepack.schema.json",
    ".ai/schemas/requirements.schema.json",
    ".ai/schemas/universal-engineering-ruleset.schema.json",
]

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
        print("OmniContext doctor")
        print("==================")
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
            f"({preview}); fill these before using OmniContext in a target repo"
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


def validate_markdown_assets(report: DoctorReport) -> None:
    readme = Path("README.md")
    changelog = Path("CHANGELOG.md")
    svg = Path("assets/omni-context.svg")

    if readme.is_file():
        readme_text = readme.read_text(encoding="utf-8")
        if "assets/omni-context.svg" in readme_text and svg.is_file():
            report.pass_check("README references the OmniContext SVG asset")
        elif "assets/omni-context.svg" in readme_text:
            report.error("README references missing SVG asset: assets/omni-context.svg")
        else:
            report.warning("README does not reference the OmniContext SVG asset")
    else:
        report.error("Missing README.md")

    if changelog.is_file():
        report.pass_check("CHANGELOG.md exists")
    else:
        report.warning("CHANGELOG.md is missing")


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
        report.error("Missing pyproject.toml for installable omni command")
        return

    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        report.error(f"Invalid pyproject.toml: line {exc.lineno}, column {exc.colno}")
        return

    script = pyproject.get("project", {}).get("scripts", {}).get("omni")
    if script != "make_ai:main":
        report.error("pyproject.toml must define project.scripts.omni = make_ai:main")
    else:
        report.pass_check("Installable omni console script is configured")


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


def write_assistant_pointers() -> None:
    for file_path, content in ASSISTANT_POINTERS.items():
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"Updated: {path}")


def run_doctor() -> int:
    report = DoctorReport()
    verify_required_ai_files(report)
    parsed = validate_json_files(report)
    validate_ruleset(parsed.get(".ai/rules/universal-engineering-ruleset.json"), report)
    validate_rulepacks(parsed, report)
    validate_requirements(parsed.get(".ai/requirements/requirements.json"), report)
    validate_assistant_pointers(report)
    validate_markdown_assets(report)
    validate_cli_entrypoints(report)
    report.print()
    return 0 if report.ok else 1


def run_sync() -> int:
    report = DoctorReport()
    verify_required_ai_files(report)
    if not report.ok:
        report.print()
        return 1

    write_assistant_pointers()
    print("Done. Assistant entrypoints now point to the global .ai ruleset.")
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
            "Maintain an OmniContext workspace. The .ai directory and assistant "
            "entrypoint files are the product; this script only syncs and "
            "validates them."
        )
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("sync", help="Refresh assistant entrypoint files.")
    subparsers.add_parser("doctor", help="Check OmniContext workspace health.")
    subparsers.add_parser("validate", help="Alias for doctor.")

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
        return run_sync()
    if command in {"doctor", "validate"}:
        return run_doctor()
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
