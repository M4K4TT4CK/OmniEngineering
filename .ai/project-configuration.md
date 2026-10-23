# Project Configuration

Fill these values before using this workspace on a specific project. These
settings correspond to `.ai/rules/universal-engineering-ruleset.json`.

## Required Project Values

- `project_name`: `<PROJECT_NAME>`
- `repository_type`: `<web_app | api | mobile_app | desktop_app | data_pipeline | infrastructure | mixed>`
- `primary_language_or_stack`: `<STACK_OR_LANGUAGE>`
- `package_manager`: `<npm | yarn | pnpm | pip | poetry | maven | gradle | cargo | go | other>`
- `build_command`: `<BUILD_COMMAND>`
- `test_command`: `<TEST_COMMAND>`
- `lint_command`: `<LINT_COMMAND>`
- `typecheck_command`: `<TYPECHECK_COMMAND>`
- `changelog_location`: `<CHANGELOG_PATH>`
- `documentation_locations`: `<README_PATH>, <ARCHITECTURE_DOC_PATH>, <API_DOC_PATH>, <ERD_OR_DATA_MODEL_PATH>, <OTHER_DOC_PATHS>`
- `branching_or_pr_standard`: `<BRANCH_AND_PR_STANDARD>`
- `comment_style`: `<COMMENT_STYLE_REQUIREMENT>`
- `requirement_id_prefix`: `REQ`

## Default Safety Flags

- `allow_broad_refactor`: `false`
- `allow_dependency_changes`: `false`
- `allow_schema_changes`: `false`
- `allow_destructive_data_changes`: `false`
- `require_user_confirmation_for_destructive_actions`: `true`

## Per-Task Requirement Template

- `id`: `REQ-###`
- `category`: `<CATEGORY>`
- `title`: `<SHORT_TITLE>`
- `description`: `<CLEAR_DESCRIPTION_OF_THE_CHANGE>`
- `priority`: `<critical | high | medium | low>`
- `minimum_access_scope`: `<specific file/module/component/table/config/test/doc>`
- `do_not_access`: `<specific excluded area>`
- `acceptance_criteria`: `<observable completion criteria>`
- `validation_required`: `<build | test | lint | typecheck | manual_verification | data_validation | security_review>`
- `documentation_required`: `<changelog | readme | architecture | api | data_model | deployment | none>`
- `risk_notes`: `<known risk, dependency, assumption, or follow-up>`

## Findings In The Graph

When `.ai/graph-config.json` points `findings_report` at an Arbiter report
(`"auto"` reads the newest full report under `arbiter-out/` and the newest gate
report together), every unsuppressed finding becomes a `finding` node with its
`rule_id`, `severity`, `cwe` and `controls`. Edges: `flags` the file and its
smallest enclosing symbol, `violates` an `arbiter_rule` node, `classified_as`
each `cwe` node, `cites` the requirement in its `req:<ID>` tag, and
`introduced_by` the commit `git blame` holds for its line. REQ-054: it also
carries the rule's `description` and `remediation` (never the evidence), and
its rule is `specified_by` the ARB-###/REQ-### id a report `requirements` field,
change-log bullet, rule pack or registry entry names beside it, so the finding
lists `traced_to` ids on a full scan too.

- `./omni graph build --if-stale` rebuilds only when HEAD or a report changed.
- `./omni graph findings --cwe CWE-89 --rule 'arbiter/python.*' --under src/`
- `./omni graph why f:<id>`: rule, CWE, flagged code, requirement, commit.
- `./omni graph impact <file>` and `./omni graph impact CWE-89` list findings.
- MCP: `graph_findings`, `graph_cwe`, `graph_impact`.
