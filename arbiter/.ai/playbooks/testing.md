# Testing Playbook

Use this to decide what validation is required and how to report it.

## Test Selection

- Use `.ai/knowledge/swebok/software-testing.md` for testing strategy.
- Use `.ai/knowledge/swebok/testing-quality-checklist.md` before accepting
  validation as sufficient.
- Use `.ai/knowledge/swebok/software-quality.md` when validation supports a
  quality claim.
- Use computing, mathematical, or engineering foundations checklists when tests
  rely on algorithms, quantitative claims, runtime behavior, or failure modes.
- Run the narrowest test that proves the change.
- Add broader tests for shared behavior, public interfaces, data handling, or
  cross-module contracts.
- For construction changes, use the construction quality checklist to confirm
  developer-level verification is sufficient.
- Run lint, typecheck, build, or doctor when configuration, docs, generated
  files, or workspace behavior changes.

## Test Suites

A suite is a named group of tests with a framework and a run command. The
registry (`.ai/test-suites.json`, or the path set in `.ai/graph-config.json`)
is the authoritative list; `omni graph build` also auto-detects suites from test
frameworks in file contents and from the commands in CI files, and a registered
suite always wins over a detected one.

```bash
omni test detect                 # propose suites: what was found, where, how CI runs it
omni test detect --write         # register what it found, then review the commands and paths
omni test add --name "Payments e2e" --kind e2e --framework playwright \
    --paths e2e --command "npx playwright test" --covers src/payments
omni test check                  # every registered path matches files; unregistered tests reported
```

- Register the project's own tests: unit, integration, end-to-end, and the
  validation or smoke scripts a build depends on, even when their names look
  nothing like tests (`--paths` may be files, directories or globs).
- OmniEngineering's own files (`make_ai.py`, `omni_graph.py`, `.ai/`) are
  tooling, not the project's tests. They belong to the workspace layer and are
  skipped by detection (`tooling_paths` in `.ai/graph-config.json`).
- Add a suite whenever a new framework, test directory or check script appears.

## Tests Are Part Of The Graph

`omni graph build` puts test files in the assurance layer and links each to the
code it exercises (`verifies` edges) and to any failure it guards (`guards`
edges). Use that instead of guessing:

- `omni graph why <file or symbol>` lists the tests that reference it, and flags
  code that no test reaches.
- New tests should follow the project's test naming so they are recognised
  (`test_*.py`, `*.test.ts`, `*Test.java`, `tests/`, `__tests__/`, ...). A test
  outside those conventions still joins the graph once a ledger entry names it
  in `regression_tests`.
- Add a test for every new behaviour and, for every fixed failure, a regression
  test that fails without the fix. Record it with
  `omni failure update FAIL-### --tests <path or path::name>`.

## Failing Tests Are Evidence

When a test, build or check fails, do not just make it green. Record what it
showed:

1. Keep the exact failure output.
2. Find why it failed (the root cause, not the assertion text).
3. Decide whether the code or the test was wrong. A wrong test is a failure too:
   record it, and never weaken or delete a test just to pass.
4. Add the entry with `omni failure add`/`update` (see the debugging playbook),
   including the rule or playbook change that would have prevented it.
5. Before touching code that an earlier failure affected, run that failure's
   regression tests and report the result.

## Reporting

Report each validation command with one of:

- `passed`
- `failed`
- `not run`

When a check is not run, explain why and identify the residual risk.

## Default OmniContext Checks

For this workspace, prefer:

```bash
./omni doctor
./omni validate
./omni failure check
python3 -m unittest discover -s tests
```
