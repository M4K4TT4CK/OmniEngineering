<!-- Review draft from the ARB-049 debt sweep (2026-10-09). Every mark below was proposed
by an assistant and NOTHING IS RECORDED in .arbiter/knowledge.json: the sweep's verdicts
were reverted so that a person adjudicates, as .claude/skills/arbiter/SKILL.md requires.
To record them: re-scan this tree (`arbiter scan . --profile offline --out arbiter-out/debt
--no-cache`), read each mark and its reason, then
`arbiter review arbiter-out/debt/report.json --apply .arbiter/reviews/2026-10-09-debt-sweep.md
--reviewer "<your name>"`. Finding ids are content fingerprints, so they match as long as
the flagged lines are unchanged. -->

# Findings to review

Mark each one, save the file, then run:

    arbiter review --apply arbiter-out/debt/review.md

    [y]  a real problem — I would act on this
    [n]  not a real problem — the rule is wrong here
    [ ]  skip; leave it blank and it is not recorded

Marking honestly matters more than marking everything. A skipped finding costs
nothing; a wrong mark is worse than no mark, because calibration reads this
ledger and nothing else.

A rule needs 20 adjudications before it stops being reported as unproven.
The selection below is weighted toward rules that are close to that line.

---


## arbiter/assurance.blanket-suppression
_0 reviewed so far, 20 more to become proven_

[n] f:bdf050c868b3  **Blanket `ruff/flake8` suppression — silences every rule at this site**
    root:src/arbiter/assurance.py:24  ·  low/high
    evidence: `ruff/flake8:A repository with four hundred `# noqa` comments is not insecure. It is`
    This suppression names no rule, so it silences whatever the analyzer would have reported here, including rules added later that nobody has seen yet. A named suppression is a decision; a blanket one is a standing instruct


## arbiter/assurance.permanently-skipped-test
_0 reviewed so far, 20 more to become proven_

[n] f:c374c705c13c  **Test is skipped unconditionally**
    root:tests/test_probes_quality.py:152  ·  low/high
    evidence: `skipped:@pytest.mark.skip`
    An unconditionally skipped test passes forever and protects nothing. A skip with a condition — a platform, a missing dependency — is a different thing and is not reported here.


## arbiter/assurance.suppression-census
_0 reviewed so far, 20 more to become proven_

[ ] f:031b266cbba8  **81 analyzer suppressions in this repository**
    root:repository  ·  info/high
    evidence: `suppressions:81 blanket:18 unexplained:73`
    81 inline suppressions across 8 tools (ruff/flake8 71, mypy 4, checkov 1, tfsec 1, trivy 1, semgrep 1). 18 name no rule at all and 73 carry no explanation. Every one of these is a finding some analyzer would otherwise ha


## arbiter/assurance.test-asserts-nothing
_0 reviewed so far, 20 more to become proven_

[n] f:8a2efd7464dc  **Test `test_text_artifacts_are_written_as_utf8` contains no assertion**
    root:tests/test_report_output.py:190  ·  low/medium
    evidence: `no-assertion:test_text_artifacts_are_written_as_utf8`
    The body contains nothing that can fail — no assert, no expect, no raises. A test like this passes whatever the code does, and counts toward a coverage figure while proving nothing. Some are legitimate smoke tests that o


## arbiter/ast.function-too-long
_0 reviewed so far, 20 more to become proven_

[y] f:a035b869ff8e  **`run_scan` is 332 lines (limit 140)**
    root:src/arbiter/engine.py:251  ·  low/high
    evidence: `run_scan:len=332`
    Measured from the parse tree, not from indentation.


## arbiter/ast.high-complexity
_0 reviewed so far, 20 more to become proven_

[y] f:c623cbe7a428  **`run_arm` has cyclomatic complexity 22 (limit 20)**
    root:src/arbiter/ab.py:191  ·  low/high
    evidence: `run_arm:cx=22`
    Counted from branch nodes in the parse tree.


## arbiter/authored.security-check-disabled
_0 reviewed so far, 20 more to become proven_

[n] f:3398bac391d4  **TLS hostname check disabled (test or local-development path)**
    root:tests/test_api_app.py:350  ·  low/low
    evidence: `disabled:check_hostname = False`
    A protection that is on by default has been explicitly switched off. This is the commonest way a snippet written to make an example work reaches production: it is a single token, it fixes the error in front of you, and n


## arbiter/authored.undeclared-import
_0 reviewed so far, 20 more to become proven_

[n] f:6d0f30bbc168  **`tree_sitter` is imported but declared in no manifest**
    root:omni_graph.py:442  ·  info/low
    evidence: `undeclared:python:tree_sitter`
    The code imports `tree_sitter`, no dependency manifest in this repository declares it, and it is not in the standard library. Most often the manifest is stale, or the package arrives transitively through something else —


## arbiter/drift.doc-references-missing-file
_0 reviewed so far, 20 more to become proven_

[n] f:a6e8912139ff  **Documentation describes `.ai/requirements/archive.json`, which was removed from the repository**
    root:.ai/project-context.md:101  ·  info/medium
    evidence: `path=.ai/requirements/archive.json removed=true`
    The file was tracked once and is gone now. A changelog or decision record is right to name it; a guide that still points a reader at it is stale.


## arbiter/quality.file-too-long
_0 reviewed so far, 20 more to become proven_

[y] f:eed77ca858ae  **File is 928 lines (limit 900)**
    root:src/arbiter/api.py:1  ·  low/high
    evidence: `lines=928`
    Long files concentrate change risk and slow review.


## arbiter/quality.todo-density
_0 reviewed so far, 20 more to become proven_

[n] f:6c89776554d8  **7 unresolved TODO/FIXME markers in one file**
    root:src/arbiter/probes.py:952  ·  low/medium
    evidence: `markers=7`
    A cluster of deferred work markers usually means an unfinished refactor.


## arbiter/secrets.assigned-credential
_0 reviewed so far, 20 more to become proven_

[n] f:144a1ea371f8  **Hardcoded credential assigned to `SEMANTIC_API_KEY_ENV`**
    root:omni_graph.py:57  ·  medium/low
    evidence: `SEMANTIC_API_KEY_ENV=OMNI*******************_KEY`
    High-entropy literal (H=3.81, 2 character classes) assigned to a credential-named symbol.


## arbiter/secrets.pg-url
_0 reviewed so far, 20 more to become proven_

[n] f:7ab934fe3706  **Database URL with inline credentials**
    root:src/arbiter/probes.py:436  ·  low/low
    evidence: `pg-url:post*****************************432.`
    A credential-shaped literal is present in version-controlled source. The password is a well-known local-development default, so this is a compose or example connection string rather than a leaked credential.


## arbiter/supply.unpinned-action
_0 reviewed so far, 20 more to become proven_

[y] f:f351df511ecf  **Action `actions/checkout` pinned to a mutable ref (`v4`)**
    root:.github/workflows/pr-check.yml:66  ·  low/high
    evidence: `actions/checkout@v4`
    Tags and branches can be repointed, so the workflow can execute different code tomorrow.


## bandit/B101
_0 reviewed so far, 20 more to become proven_

[n] f:db702689041e  **Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.**
    root:tests/test_adapter_cache.py:110  ·  info/high
    evidence: `B101@tests/test_adapter_cache.py`


## bandit/B103
_0 reviewed so far, 20 more to become proven_

[n] f:1db125a3dd69  **Chmod setting a permissive mask 0o755 on file (NOT PARSED).**
    root:src/arbiter/bundle.py:245  ·  medium/high
    evidence: `B103@src/arbiter/bundle.py`


## bandit/B104
_0 reviewed so far, 20 more to become proven_

[n] f:aaeb6c8f0193  **Possible binding to all interfaces.**
    root:tools/disagree.py:73  ·  medium/high
    evidence: `B104@tools/disagree.py`


## bandit/B105
_0 reviewed so far, 20 more to become proven_

[n] f:4aaf8b5bb16f  **Possible hardcoded password: 'True'**
    root:omni_mcp.py:159  ·  medium/high
    evidence: `B105@omni_mcp.py`


## bandit/B108
_0 reviewed so far, 20 more to become proven_

[n] f:14606e53e074  **Probable insecure usage of temp file/directory.**
    root:tests/test_mcp_api.py:245  ·  medium/high
    evidence: `B108@tests/test_mcp_api.py`


## bandit/B110
_0 reviewed so far, 20 more to become proven_

[n] f:e2f6042caff6  **Try, Except, Pass detected.**
    root:src/arbiter/engine.py:561  ·  low/high
    evidence: `B110@src/arbiter/engine.py`


## bandit/B112
_0 reviewed so far, 20 more to become proven_

[n] f:6ff0f3d481ca  **Try, Except, Continue detected.**
    root:src/arbiter/adapters.py:618  ·  low/high
    evidence: `B112@src/arbiter/adapters.py`


## bandit/B310
_0 reviewed so far, 20 more to become proven_

[n] f:c3b49978c960  **Audit url open for permitted schemes. Allowing use of file:/ or custom schemes is often unexpected.**
    root:src/arbiter/authored.py:516  ·  info/high
    evidence: `B310@src/arbiter/authored.py`


## bandit/B311
_0 reviewed so far, 20 more to become proven_

[n] f:7e6fab3dc2c4  **Standard pseudo-random generators are not suitable for security/cryptographic purposes.**
    root:tools/inject.py:40  ·  medium/high
    evidence: `B311@tools/inject.py`


## bandit/B404
_0 reviewed so far, 20 more to become proven_

[n] f:c3ca8e97d8c3  **Consider possible security implications associated with the subprocess module.**
    root:src/arbiter/ab.py:21  ·  info/high
    evidence: `B404@src/arbiter/ab.py`


## bandit/B603
_0 reviewed so far, 20 more to become proven_

[n] f:0dd1489b4ae0  **subprocess call - check for execution of untrusted input.**
    root:src/arbiter/ab.py:201  ·  info/high
    evidence: `B603@src/arbiter/ab.py`


## bandit/B607
_0 reviewed so far, 20 more to become proven_

[n] f:70e62fa25fd2  **Starting a process with a partial executable path**
    root:src/arbiter/cli.py:379  ·  info/high
    evidence: `B607@src/arbiter/cli.py`


## house/no-bare-except
_0 reviewed so far, 20 more to become proven_

[n] f:a197d46559d1  **Broad except clause**
    root:src/arbiter/adapters.py:350  ·  low/high
    evidence: `except_clause:except BaseException: # KeyboardInterrupt included: the tool's process group die`
    Structural check for `except Exception` and `except BaseException`; bare `except:` is ruff E722.


## ruff/B008
_0 reviewed so far, 20 more to become proven_

[n] f:ec13f8001343  **Do not perform function call `File` in argument defaults; instead, perform the call within the function, or read the default from a module-level singleton variable**
    root:src/arbiter/api.py:858  ·  low/high
    evidence: `B008@src/arbiter/api.py`


## ruff/B017
_0 reviewed so far, 20 more to become proven_

[y] f:1186bedac20c  **Do not assert blind exception: `Exception`**
    root:tests/test_probes_iac.py:201  ·  low/high
    evidence: `B017@tests/test_probes_iac.py`


## ruff/B023
_0 reviewed so far, 20 more to become proven_

[n] f:c24a036e2913  **Function definition does not bind loop variable `out_dirs`**
    root:src/arbiter/probes.py:1441  ·  low/high
    evidence: `B023@src/arbiter/probes.py`


## ruff/BLE001
_0 reviewed so far, 20 more to become proven_

[y] f:3e821526ea21  **Do not catch blind exception: `Exception`**
    root:src/arbiter/cli.py:503  ·  low/high
    evidence: `BLE001@src/arbiter/cli.py`


## ruff/C401
_0 reviewed so far, 20 more to become proven_

[n] f:9db47ed3e374  **Unnecessary generator (rewrite as a set comprehension)**
    root:src/arbiter/probes.py:744  ·  low/high
    evidence: `C401@src/arbiter/probes.py`


## ruff/DTZ005
_0 reviewed so far, 20 more to become proven_

[n] f:28e1133918cd  **`datetime.datetime.now()` called without a `tz` argument**
    root:make_ai.py:2803  ·  low/high
    evidence: `DTZ005@make_ai.py`


## ruff/DTZ011
_0 reviewed so far, 20 more to become proven_

[n] f:258d226aa810  **`datetime.date.today()` used**
    root:src/arbiter/ledger.py:121  ·  low/high
    evidence: `DTZ011@src/arbiter/ledger.py`


## ruff/EXE001
_0 reviewed so far, 20 more to become proven_

[n] f:d6a4f4e43d42  **Shebang is present but file is not executable**
    root:tools/calibrate_external.py:1  ·  low/high
    evidence: `EXE001@tools/calibrate_external.py`


## ruff/F841
_0 reviewed so far, 20 more to become proven_

[y] f:545b563dfa21  **Local variable `output` is assigned to but never used**
    root:make_ai.py:1939  ·  medium/high
    evidence: `F841@make_ai.py`


## ruff/FURB167
_0 reviewed so far, 20 more to become proven_

[n] f:5e2d78d14888  **Use of regular expression alias `re.S`**
    root:omni_graph.py:1297  ·  low/high
    evidence: `FURB167@omni_graph.py`


## ruff/FURB188
_0 reviewed so far, 20 more to become proven_

[n] f:3920d9c82ea7  **Prefer `str.removeprefix()` over conditionally replacing with slice.**
    root:make_ai.py:3125  ·  low/high
    evidence: `FURB188@make_ai.py`


## ruff/ISC004
_0 reviewed so far, 20 more to become proven_

[n] f:c49d802d60fd  **Unparenthesized implicit string concatenation in collection**
    root:omni_graph.py:4874  ·  low/high
    evidence: `ISC004@omni_graph.py`


## ruff/PIE810
_0 reviewed so far, 20 more to become proven_

[n] f:a59b2e9a326c  **Call `startswith` once with a `tuple`**
    root:make_ai.py:1274  ·  low/high
    evidence: `PIE810@make_ai.py`


## ruff/PLR1730
_0 reviewed so far, 20 more to become proven_

[n] f:2e91e8cea7fb  **Replace `if` statement with `best = max(best, d)`**
    root:src/arbiter/ast.py:200  ·  low/high
    evidence: `PLR1730@src/arbiter/ast.py`


## ruff/PLW1510
_0 reviewed so far, 20 more to become proven_

[n] f:15743fba27c9  **`subprocess.run` without explicit `check` argument**
    root:make_ai.py:786  ·  low/high
    evidence: `PLW1510@make_ai.py`


## ruff/RUF059
_0 reviewed so far, 20 more to become proven_

[n] f:cf1f7ca8a967  **Unpacked variable `name` is never used**
    root:src/arbiter/ab.py:228  ·  low/high
    evidence: `RUF059@src/arbiter/ab.py`


## ruff/RUF100
_0 reviewed so far, 20 more to become proven_

[n] f:595107d3477e  **Unused `noqa` directive (non-enabled: `S310`)**
    root:omni_graph.py:928  ·  low/high
    evidence: `RUF100@omni_graph.py`


## ruff/S110
_0 reviewed so far, 20 more to become proven_

[n] f:d9dff6246ef1  **`try`-`except`-`pass` detected, consider logging the exception**
    root:make_ai.py:5666  ·  low/high
    evidence: `S110@make_ai.py`


## ruff/S112
_0 reviewed so far, 20 more to become proven_

[n] f:45b7bad1ff66  **`try`-`except`-`continue` detected, consider logging the exception**
    root:src/arbiter/adapters.py:618  ·  low/high
    evidence: `S112@src/arbiter/adapters.py`


## ruff/SIM103
_0 reviewed so far, 20 more to become proven_

[n] f:cf9866039f1b  **Return the condition directly**
    root:omni_graph.py:1723  ·  low/high
    evidence: `SIM103@omni_graph.py`


## ruff/SIM117
_0 reviewed so far, 20 more to become proven_

[n] f:e75730cf055b  **Use a single `with` statement with multiple contexts instead of nested `with` statements**
    root:tests/test_file_limiter.py:72  ·  low/high
    evidence: `SIM117@tests/test_file_limiter.py`


## ruff/TRY004
_0 reviewed so far, 20 more to become proven_

[n] f:5e36de29dd4b  **Prefer `TypeError` exception for invalid type**
    root:make_ai.py:3874  ·  low/high
    evidence: `TRY004@make_ai.py`


## ruff/UP012
_0 reviewed so far, 20 more to become proven_

[n] f:318de937d5bf  **Unnecessary UTF-8 `encoding` argument to `encode`**
    root:src/arbiter/cache.py:420  ·  low/high
    evidence: `UP012@src/arbiter/cache.py`


## ruff/UP035
_0 reviewed so far, 20 more to become proven_

[n] f:0469d8d719b6  **Import from `collections.abc` instead: `Callable`**
    root:omni_mcp.py:27  ·  low/high
    evidence: `UP035@omni_mcp.py`


## ruff/UP037
_0 reviewed so far, 20 more to become proven_

[n] f:81c4236a9429  **Remove quotes from type annotation**
    root:src/arbiter/adapters.py:358  ·  low/high
    evidence: `UP037@src/arbiter/adapters.py`


## semgrep/semgrep-rules.bash.lang.correctness.unquoted-variable-expansion-in-command
_0 reviewed so far, 20 more to become proven_

[n] f:ba35381861f0  **Variable expansions must be double-quoted so as to prevent being split into multiple pieces according to whitespace or whichever separator is specified by the IFS variable. If you really wish to split**
    root:tools/bootstrap_repo.sh:121  ·  info/high
    evidence: `semgrep-rules.bash.lang.correctness.unquoted-variable-expansion-in-command@tools/bootstrap_repo.sh`


## semgrep/semgrep-rules.generic.secrets.gitleaks.generic-api-key
_0 reviewed so far, 20 more to become proven_

[n] f:3eafb5595d3d  **A gitleaks generic-api-key was detected which attempts to identify hard-coded credentials.  It is not recommended to store credentials in source-code, as this risks secrets being leaked  and used by e**
    root:docs/hosted-api.md:264  ·  low/high
    evidence: `semgrep-rules.generic.secrets.gitleaks.generic-api-key@docs/hosted-api.md`


## semgrep/semgrep-rules.python.lang.correctness.common-mistakes.string-concat-in-list
_0 reviewed so far, 20 more to become proven_

[n] f:ecdade89d4bf  **Detected strings that are implicitly concatenated inside a list. Python will implicitly concatenate strings when not explicitly delimited. Was this supposed to be individual elements of the list?**
    root:src/arbiter/ab.py:502  ·  medium/high
    evidence: `semgrep-rules.python.lang.correctness.common-mistakes.string-concat-in-list@src/arbiter/ab.py`


## semgrep/semgrep-rules.python.lang.maintainability.return-not-in-function
_0 reviewed so far, 20 more to become proven_

[n] f:370896038867  **`return` only makes sense inside a function**
    root:src/arbiter/adapters.py:209  ·  medium/high
    evidence: `semgrep-rules.python.lang.maintainability.return-not-in-function@src/arbiter/adapters.py`


## semgrep/semgrep-rules.python.lang.security.audit.dangerous-subprocess-use-audit
_0 reviewed so far, 20 more to become proven_

[n] f:39f923a119cc  **Detected subprocess function 'run' without a static string. If this data can be controlled by a malicious actor, it may be an instance of command injection. Audit the use of this call to ensure it is **
    root:src/arbiter/ab.py:201  ·  info/high
    evidence: `semgrep-rules.python.lang.security.audit.dangerous-subprocess-use-audit@src/arbiter/ab.py`


## semgrep/semgrep-rules.python.lang.security.audit.dynamic-urllib-use-detected
_0 reviewed so far, 20 more to become proven_

[n] f:86cfd2d389b6  **Detected a dynamic value being used with urllib. urllib supports 'file://' schemes, so a dynamic value controlled by a malicious actor may allow them to read arbitrary files. Audit uses of urllib call**
    root:src/arbiter/authored.py:516  ·  info/high
    evidence: `semgrep-rules.python.lang.security.audit.dynamic-urllib-use-detected@src/arbiter/authored.py`


## semgrep/semgrep-rules.yaml.github-actions.security.github-actions-mutable-action-tag
_0 reviewed so far, 20 more to become proven_

[y] f:6450363da7ac  **GitHub Actions step uses a mutable tag or branch reference. Tags and branch names can be silently repointed by the action owner, enabling supply-chain attacks — as seen in the trivy-action and kics-gi**
    root:.github/workflows/pr-check.yml:66  ·  medium/high
    evidence: `semgrep-rules.yaml.github-actions.security.github-actions-mutable-action-tag@.github/workflows/pr-check.yml`


## semgrep/semgrep-rules.yaml.github-actions.security.run-shell-injection
_0 reviewed so far, 20 more to become proven_

[n] f:e9d4278d5be5  **Using variable interpolation `${{...}}` with `github` context data in a `run:` step could allow an attacker to inject their own code into the runner. This would allow them to steal secrets and code. `**
    root:ci/github-action/action.yml:35  ·  medium/high
    evidence: `semgrep-rules.yaml.github-actions.security.run-shell-injection@ci/github-action/action.yml`


---

_Generated 2026-10-09T19:44:05+00:00 · 60 findings across 60 rules._
