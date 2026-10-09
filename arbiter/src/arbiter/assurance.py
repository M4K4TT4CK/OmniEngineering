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
    ("bandit", re.compile(r"#\s*nosec(?:\s+(B[0-9]+(?:\s*,\s*B[0-9]+)*))?"), 1),
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
    r"@pytest\.mark\.(?:skip|xfail)\b(?!\s*\(\s*condition)"
    r"|@unittest\.skip\b"
    r"|\bit\.skip\s*\(|\bdescribe\.skip\s*\(|\bxit\s*\(|\bxdescribe\s*\("
    r"|\bt\.Skip\s*\(\s*\)"
    r"|@(?:Disabled|Ignore)\b"
)

_TEST_DEF = re.compile(
    r"^\s*(?:async\s+)?def\s+(test_[A-Za-z0-9_]*)\s*\([^)]*\)\s*:"
    r"|^\s*(?:it|test)\s*\(\s*[\"'`]([^\"'`]{1,80})[\"'`]\s*,\s*(?:async\s*)?\(?\s*\)?\s*=>\s*\{"
    r"|^\s*func\s+(Test[A-Za-z0-9_]*)\s*\(",
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

# Go's test harness entry point. It is not a test and has nothing to assert.
_NOT_REALLY_A_TEST = re.compile(r"^(TestMain|TestAccPreCheck|TestHelperProcess)$")


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
            for m in pattern.finditer(text):
                total += 1
                per_tool[tool] += 1
                line_start = text.rfind("\n", 0, m.start()) + 1
                line_end = text.find("\n", m.start())
                line = text[line_start: line_end if line_end != -1 else len(text)]
                named = m.group(gi) if gi <= (m.re.groups or 0) else None
                trailing = line[m.end() - line_start:]
                if not _JUSTIFIED.search(trailing) and not _JUSTIFIED.search(line[:m.start() - line_start]):
                    unjustified += 1
                if _blanket(named):
                    blanket.append((tool, f.path, _line_of(text, m.start()), line.strip()[:110]))

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

        # A test body with no assertion of any kind cannot fail except by
        # raising. Bodies are taken as the span to the next test definition,
        # which is coarse and deliberately biased toward NOT reporting: a
        # helper between two tests gets folded into the first one's body and
        # its assertions count.
        defs = [(m.start(), (m.group(1) or m.group(2) or m.group(3) or "?"))
                for m in _TEST_DEF.finditer(text)]
        for i, (pos, name) in enumerate(defs):
            end = defs[i + 1][0] if i + 1 < len(defs) else len(text)
            body = text[pos:end]
            if len(body.strip().split("\n")) < 2:
                continue
            if _NOT_REALLY_A_TEST.match(name):
                continue
            if _ASSERTION.search(body):
                continue
            if _SKIPPED_TEST.search(body):
                continue  # already reported above
            vacuous.append((f.path, _line_of(text, pos), name))

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
