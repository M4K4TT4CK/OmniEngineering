"""Is the checking switched on?

## The question nobody asks

Every other dimension in this tool asks whether the code is sound. This one
asks whether the *checking* is — and it exists because a clean report has two
possible causes that look identical from the outside:

  * the analyzers ran over everything and found nothing, or
  * large parts of the tree were excluded, hundreds of findings were silenced
    by inline comments, and the tests that would have caught a regression
    assert nothing.

Every scanner on the market will hand you the same green tick for both. They
have to: they honour the suppression comments, they honour the ignore files,
and a test that asserts nothing still passes. The silencing is invisible to
the thing being silenced.

Measured across ten corpus repositories: 445 suppression comments, 169 of them
in a single project. Not one appears in any tool's output.

## Why these are not defects, and must not be scored

A repository with four hundred `# noqa` comments is not insecure. It is
*unmeasured*, which is a different thing and needs saying differently. So the
assurance dimension carries weight zero in the scorecard: its findings are
reported, counted and never folded into a grade. They belong to the same
family as "not assessed" — statements about the evidence rather than about
the system.

This also means the usual discrimination test does not apply. Suppressions are
far more common in mature, well-maintained code than in a deliberately broken
teaching app, so measured against the vulnerable corpus these rules would look
like noise. They are not: nobody writes a repository that is deliberately
badly suppressed, so there is no broken population to compare against, exactly
as with the quality and drift rules. `tools/discriminate.py` already declines
to issue a verdict for dimensions in that position.

## What is checked

1. SILENCED FINDINGS. Every mainstream analyzer's inline ignore syntax, across
   eleven languages. Counted per tool, with the blanket ones — an ignore that
   names no specific rule, and so silences everything on that line or in that
   file — separated out, because a bare `# noqa` is a different act from
   `# noqa: E501`.

2. EXCLUDED CODE. Ignore files and analyzer configuration that remove parts of
   the tree from analysis. A `.semgrepignore` covering `src/` means the
   semgrep result in your CI is about the tests.

3. TESTS THAT CANNOT FAIL. Permanently skipped tests, and test functions whose
   body contains no assertion of any kind. Both pass forever. This is also the
   only thing in the tool that speaks to SSDF PW.8 with any force — the
   existing check merely notes whether test files exist.

Nothing here is inferred. Every finding cites a line you can open.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from .core import Finding, Location
from .probes import ProbeContext, _line_of, _read

# ---------------------------------------------------------------------------
# 1. Inline suppressions
# ---------------------------------------------------------------------------
#
# Each entry: tool label, pattern, and the group holding the specific rule ids
# when the suppression names any. A suppression that names nothing is blanket.
SUPPRESSION_PATTERNS: list[tuple[str, re.Pattern, int]] = [
    ("ruff/flake8", re.compile(r"#\s*noqa(?::\s*([A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*))?"), 1),
    # `#nosec` is bandit's in Python and gosec's in Go, where rules are G-ids
    # and may be space-separated: `// #nosec G115 -- bounded by the check
    # above` names a rule and was reported as a blanket bandit suppression
    # (coredns, ARB-065). The tool label is corrected per file below.
    ("bandit", re.compile(r"#\s*nosec(?:\s+([BG][0-9]+(?:(?:\s*,\s*|\s+)[BG][0-9]+)*))?"), 1),
    ("mypy", re.compile(r"#\s*type:\s*ignore(?:\[([a-z-]+(?:\s*,\s*[a-z-]+)*)\])?"), 1),
    ("pylint", re.compile(r"#\s*pylint:\s*disable=?\s*([\w-]+(?:\s*,\s*[\w-]+)*)?"), 1),
    ("eslint", re.compile(r"//\s*eslint-disable(?:-next-line|-line)?(?:\s+([\w@/-]+(?:\s*,\s*[\w@/-]+)*))?"), 1),
    ("typescript", re.compile(r"//\s*@ts-(?:ignore|nocheck|expect-error)()"), 1),
    ("checkov", re.compile(r"checkov:skip=([\w]+)?"), 1),
    ("tfsec", re.compile(r"tfsec:ignore:([\w-]+)?"), 1),
    ("trivy", re.compile(r"trivy:ignore:([\w-]+)?"), 1),
    ("semgrep", re.compile(r"nosemgrep(?::\s*([\w.-]+))?"), 1),
    ("golangci", re.compile(r"//\s*nolint(?::\s*([\w,-]+))?"), 1),
    ("java", re.compile(r'@SuppressWarnings\(\s*[{"]?([^)]*)'), 1),
    ("rust", re.compile(r"#!?\[allow\(([^)]*)\)\]"), 1),
    ("csharp", re.compile(r"#pragma\s+warning\s+disable\s*([\w,\s]*)"), 1),
    ("sonar", re.compile(r"//\s*NOSONAR(.*)$", re.M), 1),
]

# A justification is a human explanation on the same line. Without one the
# next person has no way to tell a considered exception from a shrug.
_JUSTIFIED = re.compile(r"(?:#|//)\s*\S.{12,}")

# Files where suppressions are expected and uninteresting: generated code, and
# the tool's own test fixtures, which deliberately contain awkward material.
_EXPECTED_SUPPRESSIONS = re.compile(
    r"(^|/)(vendor|third_party|node_modules|\.venv|migrations|generated|proto|pb)(/|$)"
    r"|\.(pb|generated|g)\.[a-z]+$"
)

# checkov/tfsec/trivy suppression comments are only meaningful inside the IaC
# manifests those scanners read (classify()'s "iac" role, plus generic source
# or config files they might annotate). A "docs" or "test" file quoting the
# syntax to describe or test it (e.g. a changelog entry, a requirements
# writeup, or a test asserting the gate rejects an inline skip) is not a
# suppression in effect anywhere a scan would honor it.
_IAC_ONLY_SUPPRESSIONS = {"checkov", "tfsec", "trivy"}

# ---------------------------------------------------------------------------
# 2. Configuration that excludes code from analysis
# ---------------------------------------------------------------------------
EXCLUDE_FILES = {
    ".semgrepignore": "semgrep",
    ".eslintignore": "eslint",
    ".gitleaksignore": "gitleaks",
    ".trivyignore": "trivy",
    ".checkov.yaml": "checkov",
    ".checkov.yml": "checkov",
    ".bandit": "bandit",
    ".golangci.yml": "golangci-lint",
    ".golangci.yaml": "golangci-lint",
}

# Keys inside shared config files that remove code from an analyzer's view.
_CONFIG_EXCLUDE_KEYS = re.compile(
    r"^\s*(exclude|excludes|exclude_paths|exclude-dirs|extend-exclude|"
    r"skip-path|skip_path|ignorePatterns|per-file-ignores|skip-check|skip_check)\b",
    re.I | re.M,
)

_SHARED_CONFIGS = ("pyproject.toml", "setup.cfg", "tox.ini", ".flake8",
                   "eslint.config.js", ".eslintrc", ".eslintrc.json",
                   ".eslintrc.yml", ".eslintrc.yaml")

# ---------------------------------------------------------------------------
# 3. Tests that cannot fail
# ---------------------------------------------------------------------------
_SKIPPED_TEST = re.compile(
    # `xfail(condition=...)` and `xfail(<expr>, reason=...)` are conditional:
    # a first positional argument is the condition (pytest's signature), as
    # in urllib3's `xfail(_MISSING_TUNNEL_CONTROL_CHAR_FIX, reason=...)`.
    r"@pytest\.mark\.skip\b"
    r"|@pytest\.mark\.xfail\b(?!\s*\(\s*(?:condition\b|(?![\w]+\s*=)[^)\s]))"
    r"|@unittest\.skip\b"
    r"|\bit\.skip\s*\(|\bdescribe\.skip\s*\(|\bxit\s*\(|\bxdescribe\s*\("
    r"|\bt\.Skip\s*\(\s*\)"
    r"|@(?:Disabled|Ignore)\b"
)

# A skip call under a condition is a conditional skip, which this rule does
# not report (see the description below). The regex alone cannot see the
# condition: Go's `if testing.Short() { t.Skip() }` and otel's browser/node
# helpers `if (isBrowser) { return describe.skip(title, fn) }` were 61 of the
# gold findings once ARB-080 counted each site. The skip is conditional when
# code precedes it on its own line (`return`, `?`, `&&`), or when the nearest
# less-indented line above it opens a branch.
_CALL_SKIP = re.compile(r"t\.Skip|\.skip\s*\(|\bx(?:it|describe)\s*\(")
_BRANCH_OPENER = re.compile(
    r"^\s*(?:\}\s*)?(?:if\b|else\b|case\b|default\s*:|switch\b|select\b|for\b|while\b)")


def _conditional_call_skip(text: str, pos: int) -> bool:
    line_start = text.rfind("\n", 0, pos) + 1
    before = text[line_start:pos]
    if before.strip():
        return True
    indent = len(before.expandtabs(4))
    for prev in reversed(text[:line_start].split("\n")):
        if not prev.strip():
            continue
        if len(prev.expandtabs(4)) - len(prev.expandtabs(4).lstrip()) < indent:
            return bool(_BRANCH_OPENER.match(prev))
    return False


# A skip on an empty test switches an inherited test off in a subclass it does
# not apply to: netty's `@Disabled("Not applicable for unpooled allocators")
# @Override public void testX() { }` and SSL-engine variants, keycloak's LDAP
# no-import variants (Java does not require `@Override` to override), and
# Django's CookieSession tests (`@unittest.skip("Cookie backend doesn't have
# an external store") def test_x(self): pass`). The parent's test still runs
# for every class it applies to, and an empty body protects nothing enabled
# or not. A skipped test with a body, including an override that still calls
# `super.test...()`, is a real test switched off and stays reported.
_JAVA_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_PY_DEF = re.compile(r"(?m)^([ \t]*)(?:async[ \t]+)?def[ \t]+\w+\s*\([^)]*\)[^:]*:[ \t]*(.*)$")
_PY_EMPTY_LINE = re.compile(r"^\s*(?:pass|\.\.\.|#.*)?\s*$")


def _empty_python_test(text: str, pos: int) -> bool:
    m = _PY_DEF.search(text, pos)
    if m is None or text.count("\n", pos, m.start()) > 8:
        return False
    same_line = m.group(2).strip()
    if same_line and not same_line.startswith("#"):
        # `def test_x(self): pass` -- a one-line body
        return _PY_EMPTY_LINE.match(same_line) is not None
    body = re.sub(r'(?s)^\s*("""|\'\'\')(.*?)\1', "", text[m.end():], count=1)
    indent = len(m.group(1))
    for line in body.split("\n"):
        if not line.strip():
            continue
        if len(line) - len(line.lstrip()) <= indent:
            return True
        if not _PY_EMPTY_LINE.match(line):
            return False
    return True


def _switched_off_inherited_test(text: str, pos: int) -> bool:
    if text.startswith(("@pytest", "@unittest"), pos):
        return _empty_python_test(text, pos)
    if not text.startswith(("@Disabled", "@Ignore"), pos):
        return False
    head_end = text.find("{", pos)
    if head_end < 0:
        return False
    head = _JAVA_COMMENTS.sub(" ", text[pos:head_end])
    if re.search(r"\b(class|interface|enum|record)\b", head) or "(" not in head:
        return False
    depth = 0
    for i in range(head_end, min(len(text), head_end + 4000)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return not _JAVA_COMMENTS.sub("", text[head_end + 1:i]).strip()
    return False


def _in_comment(text: str, pos: int) -> bool:
    """True when `pos` sits in a comment line or a Javadoc `{@code ...}` span.

    gson's PerformanceTest wrote ``{@code @Ignore}`` in a Javadoc comment and was
    reported as a skipped test.
    """
    start = text.rfind("\n", 0, pos) + 1
    before = text[start:pos].lstrip()
    return before.startswith(("*", "/*", "//", "#")) or "{@code" in before or "{@link" in before


# `^[ \t]*`, not `^\s*`: under re.M `\s` also takes the blank lines above a
# test, which reported it on the first of them (round 2, harness agent).
_TEST_DEF = re.compile(
    r"^[ \t]*(?:async\s+)?def\s+(test_[A-Za-z0-9_]*)\s*\([^)]*\)\s*:"
    r"|^[ \t]*(?:it|test)\s*\(\s*[\"'`]([^\"'`]{1,80})[\"'`]\s*,\s*(?:async\s*)?\(?\s*\)?\s*=>\s*\{"
    r"|^[ \t]*func\s+(Test[A-Za-z0-9_]*)\s*\(",
    re.M,
)

# What counts as "this test can fail". Deliberately generous: a false claim
# that somebody's test is worthless is worse than missing one, so every
# plausible way of asserting is listed and anything unrecognised is left alone.
#
# The first version of this check reported three false positives out of its
# first four findings, all from assuming an assertion must be lexically inside
# the test body:
#   * TestMain is the Go test harness entry point, not a test at all;
#   * testify mock `EXPECT()` calls fail at cleanup, so they ARE assertions;
#   * a test that hands `t` to a shared helper has delegated its assertions,
#     which is a normal and good pattern, not an absence.
_ASSERTION = re.compile(
    r"\bassert\b|\bexpect\s*\(|\.should\b|\bassert[A-Z]\w*\s*\("
    r"|\bt\.(?:Error|Fatal|Fail)\w*\s*\(|\brequire\.\w+\s*\(|\bassert\.\w+\s*\("
    r"|\bpytest\.raises\b|\bwith\s+self\.assertRaises\b|\bthrows?\b"
    # Round 2 (ARB-065), the forms `java.test-without-assertion` and
    # `typescript.test-without-assertion` already accept: pytest's other
    # context managers that fail when nothing happens (`pytest.warns`,
    # `deprecated_call`), any `with *raises*(...)` helper (cryptography's
    # `raises_unsupported_algorithm`), an explicit `fail`, node:assert
    # functions imported by name and called bare (`strictEqual(...)`: 98 of
    # undici's 100 findings), `ensure*` helpers, and a Promise `reject` that
    # fails the test.
    r"|\bpytest\.(?:warns|deprecated_call|fail)\b|\bwith\s+[\w.]*raises\w*\s*\("
    r"|\bself\.fail\w*\s*\(|\breject\b"
    r"|(?<![.\w])(?:strictEqual|deepStrictEqual|deepEqual|notStrictEqual|notDeepStrictEqual"
    r"|notDeepEqual|notEqual|equal|ok|match|doesNotMatch|ifError|rejects|doesNotReject"
    r"|doesNotThrow|fail)\s*\("
    r"|\bensure\w*\s*\("
    # mock expectations: unmet ones fail the test at teardown
    r"|\.EXPECT\s*\(|\.AssertExpectations\s*\(|\.assert_called\w*\s*\("
    r"|\.Verify\s*\(|verify\s*\(|\.toHaveBeenCalled"
    # assertions delegated to a helper that receives the test handle
    r"|\w+\(\s*t\s*[,)]|\w+\(\s*self\s*[,)]|\w+\(\s*tt?\s*,"
    # a helper whose name says it checks something
    r"|\b\w*(?:assert|check|verify|validate|expect)\w*\s*\("
    # snapshot and golden-file comparisons
    r"|toMatchSnapshot|golden|\bcmp\.Diff\s*\("
    # AWS CDK's Template/Match assertion API (aws_cdk.assertions in Python,
    # aws-cdk-lib/assertions in TS): these raise internally on a mismatch,
    # same as a bare assert, but their names don't contain assert/check/
    # verify/validate/expect so the generic helper pattern above misses them.
    r"|\.(?:resource_count_is|resourceCountIs|has_resource_properties|"
    r"hasResourceProperties|has_resource\b|hasResource\b|find_resources|"
    r"findResources|has_output|hasOutput|has_condition|hasCondition|"
    r"has_mapping|hasMapping|has_parameter|hasParameter|template_matches|"
    r"templateMatches)\s*\("
)

# Go only: a helper handed the test handle `t` in ANY argument position.
# cobra's args_test.go writes `noArgsWithArgs(err, t, "one")`, and the
# first-position pattern above reported 32 of its tests as asserting nothing.
# Kept to Go, where `t` is the conventional *testing.T name; in Python or JS
# a variable called `t` passed to a call says nothing about assertions.
# A struct literal that keeps the handle (`writer{t: t}`, coredns) hands it on
# the same way.
_GO_T_HELPER = re.compile(r"\b\w+\((?:[^()\n]*,)?\s*t\s*[,)]|\bt:\s*t\b")

# Go's test harness entry point. It is not a test and has nothing to assert.
_NOT_REALLY_A_TEST = re.compile(r"^(TestMain|TestAccPreCheck|TestHelperProcess)$")

# The test says that not raising is the check, which is what the remediation
# asks for: in its name ("should not crash", "no exceptions on record",
# "...without error", otel-js; `test_x_does_not_raise`), or in a comment in
# its body ("# Should not raise exception", celery; "// Should not panic",
# coredns). The same exclusion `java.test-without-assertion` and
# `typescript.test-without-assertion` make.
_NO_RAISE_WORDS = re.compile(
    r"(?i)\b(?:not|never|no|without|doesn't|don't|won't|doesnt|dont|n't)\s+"
    r"(?:throw|crash|hang|error|exception|fail|panic|raise|block)(?:s|es|ing)?\b")
_NO_RAISE_COMMENT = re.compile(
    r"(?im)(?:#|//)[^\n]*?(?:\b(?:should|must|does|do|will|would|can|is|are)\s*(?:n't|\s+not|\s+never)\s+"
    r"(?:raise|throw|panic|crash|fail|block|hang|error|blow up)"
    r"|\b(?:without|no)\s+(?:raising|throwing|panicking|crashing|panics?|exceptions?)\b"
    r"|\bpasses if no\b)")


def _words(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ")


# Which Python functions a runner collects: pytest's default `test_*.py` /
# `*_test.py`, and unittest's and Django's `test*.py`. A `test_*` function in
# any other module is not a test (Django's `django/test/runner.py` defines
# `test_match_tags(test, tags, exclude_tags)`), and neither is one nested in a
# function (Django's `@skipUnlessDBFeature(...) def test_func(): raise
# ValueError` fixtures inside test_utils, celery's local `test_function`).
_PY_TEST_MODULE = re.compile(r"(?:^test[^/]*|_tests?)\.py$")
# A pytest-benchmark test (`def test_aesgcm_encrypt(benchmark)`, cryptography's
# tests/bench) measures and has nothing to assert, as the Java rule's
# performance-harness classes.
_HARNESS_PARAMS = {"benchmark"}
# Test methods (`def test_x(self)`) have never been judged: the delegated-
# helper pattern `\w+\(\s*self\s*[,)]` matched the method's own `def` line,
# so every one of them read as asserting. Round 2 found this while moving
# Python to `ast`, and keeps the scope as it was, explicitly, until the user
# decides; judging them adds 357 findings on gold (Django 226, celery 105,
# urllib3 16, cryptography 10); of 15 read, 14 only call the code and one is
# an empty override switching an inherited test off (CHANGELOG 2026-10-22,
# round 2).
JUDGE_PYTHON_METHODS = False
_PY_SKIP_CALL = re.compile(
    r"^\s*(?:pytest\.skip|self\.skipTest|raise\s+(?:unittest\.)?SkipTest)\b")
# Unconditional skip calls inside a JS or Go test body.
_BODY_SKIP_CALL = re.compile(
    r"\b(?:this|t|ctx|context)\.skip\s*\(|\bt\.Skip(?:Now|f)?\s*\(")
_JS_SKIPPED_BLOCK = re.compile(r"\bdescribe\.skip\s*\(|\bxdescribe\s*\(")
_JS_HELPER_DEF = re.compile(
    r"^[ \t]*(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\("
    r"|^[ \t]*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?"
    r"(?:function\b[^{]*|\([^)]*\)\s*=>\s*|[A-Za-z_$][\w$]*\s*=>\s*)\{",
    re.M)
_GO_HELPER_DEF = re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(", re.M)


def _brace_end(text: str, open_pos: int, limit: int = 200_000) -> int:
    """Index just past the brace that closes the one at `open_pos` (JS, Go).

    Strings, template literals, runes and comments are skipped; a body that
    does not close (malformed, or a brace inside a regex literal) ends at -1.
    """
    depth = 0
    i, n = open_pos, min(len(text), open_pos + limit)
    while i < n:
        c = text[i]
        if c in "\"'`":
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\\":
                    j += 1
                elif text[j] == "\n" and c != "`":
                    break
                j += 1
            i = j + 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] in "/*":
            j = text.find("\n" if text[i + 1] == "/" else "*/", i + 2)
            i = n if j < 0 else j + (1 if text[i + 1] == "/" else 2)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _calls_any(body: str, names: set[str], own: str = "") -> bool:
    return any(n != own and re.search(rf"(?<![\w$]){re.escape(n)}\s*\(|@{re.escape(n)}\b", body)
               for n in names)


def _asserting_helpers(defs: list[tuple[str, str]]) -> set[str]:
    """Names of this file's functions that assert, directly or through another.

    `testHeaderValue(...)` (axios), `testFileByMimeType` and `equalTest`
    (nest), `@test_mutation()` (a Django decorator that wraps its test in
    `assertRaisesMessage`): a test calling a helper defined in the same file
    that asserts has delegated its assertion, as Go tests handing `t` on do.
    """
    found = {name for name, body in defs if _ASSERTION.search(body)}
    for _ in range(2):
        more = {name for name, body in defs if name not in found and _calls_any(body, found, name)}
        if not more:
            break
        found |= more
    return found


def _judged(name: str, body: str, helpers: set[str]) -> bool:
    """True when a test body has nothing that could fail it."""
    if _NOT_REALLY_A_TEST.match(name) or _NO_RAISE_WORDS.search(_words(name)):
        return False
    if _ASSERTION.search(body) or _NO_RAISE_COMMENT.search(body):
        return False
    return not _calls_any(body, helpers, name)


def _unconditional_body_skip(body: str) -> bool:
    for m in _BODY_SKIP_CALL.finditer(body):
        if not _in_comment(body, m.start()) and not _conditional_call_skip(body, m.start()):
            return True
    return False


# A fixture or helper that happens to be named `test_*`: a `@pytest.fixture`
# (aiohttp's `test_client`), or a function that returns or yields a value,
# which pytest does not run as a test (Django's `test_processor(request)`
# context processor in test_response.py).
_PY_FIXTURE = re.compile(r"@(?:pytest\.)?fixture\b")


def _returns_value(func) -> bool:
    import ast
    stack = list(func.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Return) and node.value is not None \
                and not (isinstance(node.value, ast.Constant) and node.value.value is None):
            return True
        if isinstance(node, (ast.Yield, ast.YieldFrom)):
            return True
        stack.extend(ast.iter_child_nodes(node))
    return False


def _python_tests(f, text: str):
    """(line, name) of each collected Python test that asserts nothing.

    Read with `ast`, so a test's body is its own: the old span ran to the next
    `def test_`, which folded in the NEXT test's decorators, and an
    unconditional `@pytest.mark.skip` there hid this test (the conditional-skip
    blind spot, round 2). A test is not judged when it never runs: an
    unconditional skip decorator on it or its class (reported by
    `permanently-skipped-test`, or an inherited test switched off), or an
    unconditional `pytest.skip()` / `self.skipTest()` at the top of its body.
    `skipif`, `xfail(<condition>)` and a skip under an `if` leave it running
    somewhere, so its body is judged.
    """
    import ast
    tree = ast.parse(text)
    lines = text.split("\n")

    def src(node) -> str:
        return "\n".join(lines[node.lineno - 1: node.end_lineno])

    def unconditional(decorators) -> bool:
        return any(_SKIPPED_TEST.match("@" + (ast.get_source_segment(text, d) or ""))
                   for d in decorators)

    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    helpers = _asserting_helpers([(n.name, src(n)) for n in funcs])
    found: list[tuple[int, str]] = []

    def visit(body, skipped: bool) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit(node.body, skipped or unconditional(node.decorator_list))
            elif isinstance(node, (ast.If, ast.Try, ast.With)):
                for part in (node.body, getattr(node, "orelse", []),
                             getattr(node, "finalbody", []),
                             *[h.body for h in getattr(node, "handlers", [])]):
                    visit(part, skipped)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name.startswith("test_"):
                if skipped or unconditional(node.decorator_list):
                    continue
                if node.end_lineno == node.lineno:
                    continue    # `def test_x(self): pass`, a one-line body
                args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
                if _HARNESS_PARAMS & {a.arg for a in args}:
                    continue
                if not JUDGE_PYTHON_METHODS and args and args[0].arg == "self":
                    continue

                if any(_PY_SKIP_CALL.match(ast.get_source_segment(text, s) or "")
                       for s in node.body):
                    continue
                decorated = " ".join("@" + (ast.get_source_segment(text, d) or "")
                                     for d in node.decorator_list)
                if _PY_FIXTURE.search(decorated) or _returns_value(node):
                    continue
                # From the line after `def`, so a comment above the first
                # statement ("# should not raise exception") is read.
                body = "\n".join(lines[node.lineno: node.end_lineno])
                if _judged(node.name, decorated + "\n" + body, helpers):
                    found.append((node.lineno, node.name))

    visit(tree.body, False)
    return found


def _brace_tests(f, text: str):
    """(line, name) of each JS/TS or Go test that asserts nothing.

    The body is the test's own braces. The old span to the next test folded
    in the next test's `it.skip(` or `t.Skip()` and hid this one. A test
    with an unconditional skip call in its body, or inside a
    `describe.skip`/`xdescribe` block, never runs and is not judged; a skip
    under a branch (`if testing.Short() { t.Skip() }`) is, because the test
    runs everywhere else.
    """
    go = f.language == "go"
    defs = []
    for m in (_GO_HELPER_DEF if go else _JS_HELPER_DEF).finditer(text):
        name = next(g for g in m.groups() if g)
        brace = text.find("{", m.end() - 1)
        end = _brace_end(text, brace) if brace >= 0 else -1
        if end > 0:
            defs.append((name, text[brace:end]))
    helpers = _asserting_helpers(defs)
    skipped_blocks = []
    if not go:
        for m in _JS_SKIPPED_BLOCK.finditer(text):
            if _in_comment(text, m.start()) or _conditional_call_skip(text, m.start()):
                continue
            brace = text.find("{", m.end())
            end = _brace_end(text, brace) if brace >= 0 else -1
            if end > 0:
                skipped_blocks.append((brace, end))
    found: list[tuple[int, str]] = []
    for m in _TEST_DEF.finditer(text):
        name = m.group(2) or m.group(3)
        if not name or bool(m.group(3)) != go:
            continue
        brace = text.find("{", m.end() - 1)
        end = _brace_end(text, brace) if brace >= 0 else -1
        if end < 0:
            continue
        body = text[brace:end]
        if "\n" not in body.strip():
            continue
        if any(a <= m.start() < b for a, b in skipped_blocks):
            continue
        if _unconditional_body_skip(body):
            continue
        if go and _GO_T_HELPER.search(body):
            continue
        if _judged(name, body, helpers):
            found.append((_line_of(text, m.start()), name))
    return found


def _vacuous_tests(f, text: str) -> list[tuple[int, str]]:
    """Tests whose body contains nothing that could fail them."""
    if f.language == "python":
        if not _PY_TEST_MODULE.search(f.path.rsplit("/", 1)[-1]):
            return []
        try:
            return _python_tests(f, text)
        except (SyntaxError, ValueError, RecursionError):
            return []
    if f.language in ("go", "javascript", "typescript"):
        return _brace_tests(f, text)
    return []


def _is_test_file(f) -> bool:
    return f.role == "test"


def _blanket(match_groups: str | None) -> bool:
    """True when the suppression names no specific rule, so it silences all."""
    return not (match_groups or "").strip()


def probe_assurance(ctx: ProbeContext) -> list[Finding]:
    out: list[Finding] = []
    for repo in ctx.repos:
        out.extend(_suppressions(ctx, repo.id))
        out.extend(_exclusions(ctx, repo.id))
        out.extend(_untestable_tests(ctx, repo.id))
    return out


# ---------------------------------------------------------------------------

def _python_comments_only(text: str) -> str:
    """The text with everything that is not a comment blanked, positions kept.

    A `# noqa` inside a string literal or a docstring is prose or a fixture,
    not a suppression: this module's own docstring explains what `# noqa`
    means, and the probe's tests write `"x = 1  # noqa\n"` into temporary
    files. The regex census counted both (FAIL-048). The tokenizer knows which
    hashes are comments; a file it cannot tokenize keeps the regex view.
    """
    import io
    import tokenize
    spans: list[tuple[int, int, int, int]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                spans.append((*tok.start, *tok.end))
    except (tokenize.TokenError, SyntaxError, ValueError):
        return text
    lines = text.split("\n")
    out = [" " * len(line) for line in lines]
    for row, col, _erow, ecol in spans:
        line = lines[row - 1]
        out[row - 1] = out[row - 1][:col] + line[col:ecol] + out[row - 1][ecol:]
    return "\n".join(out)


def _suppressions(ctx: ProbeContext, repo_id: str) -> list[Finding]:
    """Count silenced findings per tool, and report the blanket ones by site."""
    per_tool: dict[str, int] = defaultdict(int)
    blanket: list[tuple[str, str, int, str]] = []   # tool, path, line, text
    unjustified = 0
    total = 0

    for f in ctx.inventory.text_files():
        # Documentation discusses suppressions; it does not apply them. README
        # and the decision log here both explain what `# noqa` means and were
        # reported as blanket suppressions for saying so.
        if f.repo_id != repo_id or f.role in ("generated", "docs"):
            continue
        if _EXPECTED_SUPPRESSIONS.search(f.path):
            continue
        text = _read(f)
        if not text:
            continue
        if getattr(f, "language", "") == "python":
            text = _python_comments_only(text)
        for tool, pattern, gi in SUPPRESSION_PATTERNS:
            if tool in _IAC_ONLY_SUPPRESSIONS and f.role in ("docs", "test"):
                continue
            label = "gosec" if tool == "bandit" and getattr(f, "language", "") == "go" else tool
            for m in pattern.finditer(text):
                total += 1
                per_tool[label] += 1
                line_start = text.rfind("\n", 0, m.start()) + 1
                line_end = text.find("\n", m.start())
                line = text[line_start: line_end if line_end != -1 else len(text)]
                named = m.group(gi) if gi <= (m.re.groups or 0) else None
                trailing = line[m.end() - line_start:]
                if not _JUSTIFIED.search(trailing) and not _JUSTIFIED.search(line[:m.start() - line_start]):
                    unjustified += 1
                if _blanket(named):
                    blanket.append((label, f.path, _line_of(text, m.start()), line.strip()[:110]))

    out: list[Finding] = []
    if not total:
        return out

    # One finding per site for blanket suppressions -- those are the ones worth
    # opening, because a bare ignore silences rules nobody has read.
    for tool, path, line, snippet in blanket[:200]:
        out.append(Finding(
            rule_id="arbiter/assurance.blanket-suppression",
            title=f"Blanket `{tool}` suppression — silences every rule at this site",
            dimension="assurance", severity="low", confidence="high",
            repo_id=repo_id, probe="assurance",
            location=Location(path=path, start_line=line, repo_id=repo_id),
            description=(
                "This suppression names no rule, so it silences whatever the analyzer "
                "would have reported here, including rules added later that nobody has "
                "seen yet. A named suppression is a decision; a blanket one is a "
                "standing instruction to stop looking."
            ),
            remediation="Name the specific rule being suppressed, and say why on the same line.",
            evidence=f"{tool}:{snippet}",
            controls=["NIST-800-218:PW.7"],
            tags=["assurance", "suppression", tool],
        ))

    # And one summary finding per repository, because the total is the number
    # that changes how much a clean report from anything else is worth.
    breakdown = ", ".join(f"{t} {n}" for t, n in sorted(per_tool.items(), key=lambda kv: -kv[1])[:6])
    out.append(Finding(
        rule_id="arbiter/assurance.suppression-census",
        title=f"{total} analyzer suppressions in this repository",
        dimension="assurance",
        # Informational by construction. This is not a defect; it is the size
        # of the asterisk on every other tool's clean report.
        severity="info", confidence="high",
        repo_id=repo_id, probe="assurance",
        location=Location(repo_id=repo_id, logical="repository"),
        description=(
            f"{total} inline suppressions across {len(per_tool)} tools ({breakdown}). "
            f"{len(blanket)} name no rule at all and {unjustified} carry no explanation. "
            "Every one of these is a finding some analyzer would otherwise have "
            "reported, and none of them appear in that analyzer's output. This is "
            "not a defect — it is how much smaller 'clean' is than it looks."
        ),
        remediation=(
            "Review the blanket ones first. A suppression worth keeping is worth "
            "naming a rule and a reason."
        ),
        evidence=f"suppressions:{total} blanket:{len(blanket)} unexplained:{unjustified}",
        controls=["NIST-800-218:PW.7", "NIST-800-53r5:CA-7"],
        tags=["assurance", "suppression", "census"],
    ))
    return out


def _exclusions(ctx: ProbeContext, repo_id: str) -> list[Finding]:
    """Configuration that removes parts of the tree from analysis."""
    out: list[Finding] = []
    for f in ctx.inventory.text_files():
        if f.repo_id != repo_id:
            continue
        base = Path(f.path).name
        tool = EXCLUDE_FILES.get(base)
        text = None
        if tool:
            text = _read(f)
            patterns = [ln.strip() for ln in (text or "").split("\n")
                        if ln.strip() and not ln.strip().startswith("#")]
            if not patterns:
                continue
            out.append(Finding(
                rule_id="arbiter/assurance.analysis-excluded",
                title=f"`{tool}` is configured to skip {len(patterns)} path pattern(s)",
                dimension="assurance", severity="low", confidence="high",
                repo_id=repo_id, probe="assurance",
                location=Location(path=f.path, start_line=1, repo_id=repo_id),
                description=(
                    "Anything matching these patterns is not analyzed, so a clean "
                    f"{tool} result says nothing about it. Excluding vendored code is "
                    "reasonable; excluding source is how a scanner ends up reporting "
                    "on the tests."
                ),
                remediation="Confirm each pattern excludes code you meant to exclude.",
                evidence=f"{tool}:" + "; ".join(patterns[:6]),
                controls=["NIST-800-218:PO.3"],
                tags=["assurance", "exclusion", tool],
            ))
            continue

        if base in _SHARED_CONFIGS:
            text = _read(f)
            if not text:
                continue
            keys = _CONFIG_EXCLUDE_KEYS.findall(text)
            if not keys:
                continue
            m = _CONFIG_EXCLUDE_KEYS.search(text)
            out.append(Finding(
                rule_id="arbiter/assurance.analysis-excluded",
                title=f"`{base}` narrows what the analyzers look at",
                dimension="assurance", severity="info", confidence="medium",
                repo_id=repo_id, probe="assurance",
                location=Location(path=f.path, start_line=_line_of(text, m.start()),
                                  repo_id=repo_id),
                description=(
                    f"This configuration contains {len(keys)} exclusion or per-file "
                    "ignore setting(s). They are usually deliberate and worth knowing "
                    "about, because they bound what every analyzer result means."
                ),
                remediation="Confirm the exclusions still match what you intended.",
                evidence=f"{base}:" + ",".join(sorted({k.strip() for k in keys})[:6]),
                controls=["NIST-800-218:PO.3"],
                tags=["assurance", "exclusion"],
            ))
    return out


def _untestable_tests(ctx: ProbeContext, repo_id: str) -> list[Finding]:
    """Tests that pass no matter what the code does."""
    out: list[Finding] = []
    skipped_total = 0
    vacuous: list[tuple[str, int, str]] = []

    for f in ctx.inventory.text_files():
        if f.repo_id != repo_id or not _is_test_file(f):
            continue
        text = _read(f)
        if not text:
            continue

        for m in _SKIPPED_TEST.finditer(text):
            if _in_comment(text, m.start()):
                continue
            if _CALL_SKIP.search(m.group(0)) and _conditional_call_skip(text, m.start()):
                continue
            if _switched_off_inherited_test(text, m.start()):
                continue
            skipped_total += 1
            out.append(Finding(
                rule_id="arbiter/assurance.permanently-skipped-test",
                title="Test is skipped unconditionally",
                dimension="assurance", severity="low", confidence="high",
                repo_id=repo_id, probe="assurance",
                location=Location(path=f.path, start_line=_line_of(text, m.start()),
                                  repo_id=repo_id),
                description=(
                    "An unconditionally skipped test passes forever and protects "
                    "nothing. A skip with a condition — a platform, a missing "
                    "dependency — is a different thing and is not reported here."
                ),
                remediation="Fix it, delete it, or give the skip a condition and a reason.",
                evidence=f"skipped:{m.group(0)[:60]}",
                controls=["NIST-800-218:PW.8", "NIST-800-53r5:SA-11"],
                tags=["assurance", "test"],
            ))

        vacuous.extend((f.path, line, name) for line, name in _vacuous_tests(f, text))

    for path, line, name in vacuous[:100]:
        out.append(Finding(
            rule_id="arbiter/assurance.test-asserts-nothing",
            title=f"Test `{name}` contains no assertion",
            dimension="assurance", severity="low", confidence="medium",
            repo_id=repo_id, probe="assurance",
            location=Location(path=path, start_line=line, repo_id=repo_id),
            description=(
                "The body contains nothing that can fail — no assert, no expect, no "
                "raises. A test like this passes whatever the code does, and counts "
                "toward a coverage figure while proving nothing. Some are legitimate "
                "smoke tests that only check the code does not raise, which is why "
                "this is reported at medium confidence rather than high."
            ),
            remediation="Assert the behaviour, or say in a comment that not raising is the assertion.",
            evidence=f"no-assertion:{name}",
            controls=["NIST-800-218:PW.8", "NIST-800-53r5:SA-11"],
            tags=["assurance", "test"],
        ))
    return out


def register_assurance() -> None:
    from .probes import Probe, register
    register(Probe(
        name="assurance",
        dimensions=["assurance"],
        checks=5,
        run=probe_assurance,
    ))
