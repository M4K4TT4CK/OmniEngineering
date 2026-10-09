"""Quality, supply-chain, assurance and machine-authored-code probes, plus the
AST house rules.
"""
from __future__ import annotations

import pytest
from helpers import ROOT, _scan_text

from arbiter.engine import run_scan
from arbiter.policy import load_config


def test_ast_metrics_measure_real_boundaries(tmp_path):
    from arbiter import ast as ts
    if not ts.available():
        pytest.skip("tree-sitter not installed")
    src = tmp_path / "deep.py"
    body = "\n".join("    " * (i + 1) + f"if x{i}:" for i in range(8))
    src.write_text(f"def deep(x0,x1,x2,x3,x4,x5,x6,x7):\n{body}\n" + "    " * 9 + "return 1\n")
    fns = ts.functions(str(src), "python")
    assert len(fns) == 1
    assert fns[0].name == "deep"
    assert fns[0].max_depth == 8
    assert fns[0].complexity == 9


def test_ast_query_house_rule(tmp_path):
    from arbiter import ast as ts
    if not ts.available():
        pytest.skip("tree-sitter not installed")
    (tmp_path / "a.py").write_text("try:\n    f()\nexcept Exception:\n    pass\n")
    cfg = dict(load_config(None))
    cfg["rules"] = [{
        "id": "no-bare-except", "type": "ast_query", "languages": ["python"],
        "query": "(except_clause) @hit", "severity": "low",
    }]
    rep = run_scan([str(tmp_path)], cfg, only=["house_rules_ast"])
    assert [f for f in rep.active() if f.rule_id == "house/no-bare-except"]


def test_house_rule_required_path_is_asked_per_repository(tmp_path):
    """`file_exists` matched against every path in the scan flattened together,
    so one repository's LICENSE answered for all of them and the finding had no
    repository to name — it reported against the `root` default whatever it had
    matched."""
    rules = {"rules": [{"id": "needs-license", "type": "file_exists",
                        "paths": ["LICENSE"]}]}
    found = _scan_text(tmp_path, "src/a.py", "x = 1\n", ["house_rules"], rules)
    hits = [f for f in found if f.rule_id == "house/needs-license"]
    assert len(hits) == 1
    assert hits[0].location.repo_id == hits[0].repo_id != ""


# ---------------------------------------------------------------------------
# Severity by measured discrimination, not by intuition.
#
# A rule earns its severity by firing more on bad code than on good code. The
# three dependency-pinning rules were all "low". Measured stack-for-stack
# against the corpus, only one of them actually separates the two:
#
#   supply.unpinned-action       4.3x on Node, 219x on CloudFormation  -> low
#   supply.unpinned-npm-dep      1.1x  (0.68/kloc good, 0.73/kloc bad) -> info
#   supply.unpinned-python-dep   0.0x  (fires only on good code)       -> info
#
# "info" scores zero, so a repo is no longer graded down for something that
# carries no evidence. The findings are still reported.
# ---------------------------------------------------------------------------

def test_unpinned_npm_dep_is_informational(tmp_path):
    found = _scan_text(tmp_path, "package.json",
                       '{"dependencies": {"lodash": "^4.17.0"}}\n', ["supply_chain"])
    hits = [f for f in found if f.rule_id == "arbiter/supply.unpinned-npm-dep"]
    assert hits, "the rule must still report"
    assert hits[0].severity == "info"


def test_unpinned_python_dep_is_informational(tmp_path):
    found = _scan_text(tmp_path, "requirements.txt", "requests>=2.0\nflask\n",
                       ["supply_chain"])
    hits = [f for f in found if f.rule_id == "arbiter/supply.unpinned-python-dep"]
    assert len(hits) == 2
    assert all(f.severity == "info" for f in hits)


def test_unpinned_action_keeps_its_severity(tmp_path):
    """This is the one that discriminates, so it keeps scoring."""
    wf = "jobs:\n  b:\n    steps:\n      - uses: actions/checkout@v4\n"
    found = _scan_text(tmp_path, ".github/workflows/ci.yml", wf, ["supply_chain"])
    hits = [f for f in found if f.rule_id == "arbiter/supply.unpinned-action"]
    assert hits and hits[0].severity == "low"


def test_informational_findings_do_not_move_the_grade(tmp_path):
    """The point of the downgrade: a project full of caret ranges and nothing
    else must not be scored as if it had real problems."""
    from arbiter.core import SEV_WEIGHT
    assert SEV_WEIGHT["info"] == 0.0
    found = _scan_text(tmp_path, "package.json",
                       '{"dependencies": {"a": "^1.0.0", "b": "~2.0.0", "c": "*"}}\n',
                       ["supply_chain"])
    assert found and all(SEV_WEIGHT[f.severity] == 0.0 for f in found)


# ---------------------------------------------------------------------------
# Assurance: is the checking switched on?
#
# A clean report has two possible causes that look identical — the analyzers
# ran and found nothing, or the analyzers were silenced. Every scanner honours
# the suppression comments that hide findings from it, so the silencing is
# invisible to the thing being silenced.
# ---------------------------------------------------------------------------

def test_blanket_suppression_is_separated_from_a_named_one(tmp_path):
    (tmp_path / "a.py").write_text(
        "import os  # noqa\n"           # blanket: silences everything
        "import sys  # noqa: F401\n"    # named: a decision about one rule
    )
    found = _scan_text(tmp_path, "b.txt", "", ["assurance"])
    blanket = [f for f in found if "blanket-suppression" in f.rule_id]
    assert len(blanket) == 1 and blanket[0].location.start_line == 1


def test_suppression_census_counts_every_tool(tmp_path):
    (tmp_path / "a.py").write_text("x = 1  # noqa\ny = 2  # nosec\nz = 3  # type: ignore\n")
    (tmp_path / "b.go").write_text("//nolint\nvar x = 1\n")
    (tmp_path / "c.tf").write_text("# checkov:skip=CKV_AWS_1\nresource \"a\" \"b\" {}\n")
    found = _scan_text(tmp_path, "d.txt", "", ["assurance"])
    census = [f for f in found if "suppression-census" in f.rule_id]
    assert len(census) == 1
    assert "5 inline suppressions" in census[0].description
    assert census[0].severity == "info", "a census is not a defect"


def test_assurance_findings_carry_no_score_weight():
    """A repository with four hundred noqa comments is not insecure — it is
    unmeasured. Scoring the two the same way is the conflation this dimension
    exists to expose."""
    from arbiter.policy import DEFAULT_WEIGHTS
    assert DEFAULT_WEIGHTS["assurance"] == 0.0


def test_analyzer_ignore_file_is_reported(tmp_path):
    (tmp_path / ".semgrepignore").write_text("# comment\nsrc/\nvendor/\n")
    found = _scan_text(tmp_path, "a.py", "x = 1\n", ["assurance"])
    ex = [f for f in found if "analysis-excluded" in f.rule_id]
    assert ex and "semgrep" in ex[0].title and "2 path pattern" in ex[0].title


def test_unconditionally_skipped_test_is_reported(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "import pytest\n\n\n@pytest.mark.skip\ndef test_a():\n    assert 1\n")
    found = _scan_text(tmp_path, "z.txt", "", ["assurance"])
    assert [f for f in found if "permanently-skipped-test" in f.rule_id]


def test_a_test_that_asserts_nothing_is_reported(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "import mod\n\n\ndef test_smoke():\n    mod.thing()\n    mod.other()\n")
    found = _scan_text(tmp_path, "z.txt", "", ["assurance"])
    v = [f for f in found if "asserts-nothing" in f.rule_id]
    assert v and v[0].confidence == "medium", \
        "some are legitimate smoke tests, so this is never high confidence"


@pytest.mark.parametrize("body,label", [
    ("def test_a():\n    assert thing()\n", "plain assert"),
    ("def test_a():\n    with pytest.raises(ValueError):\n        thing()\n", "raises"),
    ("def test_a(self):\n    self.assertEqual(1, 1)\n", "unittest"),
    ("func TestA(t *testing.T) {\n\trequire.NoError(t, err)\n}\n", "go require"),
    ("func TestA(t *testing.T) {\n\tm.EXPECT().Init(nil).Once()\n}\n", "mock expectation"),
    ("func TestA(t *testing.T) {\n\thelperThatChecks(t, input)\n}\n", "delegated to helper"),
    ("func TestMain(m *testing.M) {\n\tos.Exit(m.Run())\n}\n", "harness entry point"),
    ("it('works', () => {\n  expect(x).toBe(1)\n})\n", "jest expect"),
])
def test_real_assertions_are_not_called_vacuous(tmp_path, body, label):
    """Three of this check's first four findings were false positives, all from
    assuming an assertion must sit lexically inside the test body. A false
    claim that somebody's test is worthless is worse than missing one."""
    d = tmp_path / label.replace(" ", "_")
    (d / "tests").mkdir(parents=True)
    ext = "go" if "func Test" in body else ("js" if "it(" in body else "py")
    (d / "tests" / f"test_x.{ext}").write_text(body)
    rep = run_scan([str(d)], load_config(None), only=["assurance"], use_adapters=False)
    v = [f for f in rep.active() if "asserts-nothing" in f.rule_id]
    assert not v, f"{label} is an assertion: {[f.evidence for f in v]}"


def test_generated_and_vendored_suppressions_are_ignored(tmp_path):
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "lib.py").write_text("x = 1  # noqa\n" * 20)
    found = _scan_text(tmp_path, "a.py", "y = 2\n", ["assurance"])
    assert not [f for f in found if "suppression" in f.rule_id]


def test_assurance_dimension_is_registered():
    from arbiter.core import DIMENSIONS
    assert "assurance" in DIMENSIONS


# ---------------------------------------------------------------------------
# Machine-authored code defects.
#
# The awkward fact behind this probe: a model is the worst available reviewer
# for its own hallucinated imports. Asked to check, it reads the import, finds
# it plausible — it generated it precisely because it was plausible — and
# passes. These failures are decidable, so they belong in a deterministic
# probe rather than in the judgement pass.
# ---------------------------------------------------------------------------

def test_offline_cannot_tell_a_stale_manifest_from_an_invented_package(tmp_path):
    """Offline, all this knows is that a manifest does not declare something.
    Grading that as a security finding claims a distinction that was not
    checked, so it reports at zero weight."""
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")
    (tmp_path / "app.py").write_text("import requests\nimport some_other_thing\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["authored"],
                   use_adapters=False)
    u = [f for f in rep.active() if "undeclared-import" in f.rule_id]
    assert u and u[0].severity == "info"
    assert "connected profile" in u[0].description


def test_prose_is_not_read_as_an_import(tmp_path):
    """A line-anchored regex reads "import the module" in a docstring as an
    import. The first run of this check reported a package called `the`,
    lifted out of a docstring in psf/requests."""
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")
    (tmp_path / "a.py").write_text(
        '"""Usage:\n\nimport the library first, then\nimport nonexistentthing\n"""\n'
        "import requests\n"
        "# import alsonotreal\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["authored"],
                   use_adapters=False)
    names = {f.evidence.split(":")[-1] for f in rep.active()
             if "undeclared" in f.rule_id}
    assert not names & {"the", "nonexistentthing", "alsonotreal"}, names


def test_an_optional_import_is_not_a_missing_dependency(tmp_path):
    """try/except around an import is how an optional dependency is declared.
    Its absence from the manifest is the point."""
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")
    (tmp_path / "a.py").write_text(
        "import requests\ntry:\n    import simplejson as json\nexcept ImportError:\n"
        "    import json\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["authored"],
                   use_adapters=False)
    assert not [f for f in rep.active() if "simplejson" in f.evidence]


def test_a_module_named_differently_from_its_package_is_not_reported(tmp_path):
    (tmp_path / "requirements.txt").write_text("PyYAML==6.0\npyOpenSSL==24.0\n")
    (tmp_path / "a.py").write_text("import yaml\nimport OpenSSL\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["authored"],
                   use_adapters=False)
    assert not [f for f in rep.active() if "undeclared" in f.rule_id]


def test_a_monorepo_sibling_package_is_not_a_missing_dependency(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"name": "juice-shop", "workspaces": ["frontend"], "dependencies": {}}')
    (tmp_path / "a.ts").write_text("import { X } from '@juice-shop/models'\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["authored"],
                   use_adapters=False)
    assert not [f for f in rep.active() if "undeclared" in f.rule_id]


def test_a_registry_failure_never_reads_as_absence(monkeypatch):
    """None is load-bearing. A network problem must not become a critical
    finding that a package does not exist."""
    from arbiter import authored
    authored._EXISTENCE_CACHE.clear()
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no network")))
    assert authored.package_exists("python", "anything") is None
    f = authored._import_finding(
        type("F", (), {"path": "a.py", "repo_id": "root"})(), "import anything\n",
        "anything", "python", None)
    assert f.severity == "info" and "nonexistent" not in f.rule_id


def test_a_package_that_does_not_exist_is_critical():
    from arbiter.authored import _import_finding
    f = _import_finding(type("F", (), {"path": "a.py", "repo_id": "root"})(),
                        "import made_up_thing\n", "made_up_thing", "python", exists=False)
    assert f.severity == "critical"
    assert "import-of-nonexistent-package" in f.rule_id
    assert "unclaimed" in f.description


def test_a_stub_on_a_security_path_outranks_a_stub_anywhere_else(tmp_path):
    (tmp_path / "a.py").write_text(
        "def verify_signature(sig, body):\n    return True  # TODO: implement\n\n\n"
        "def render_footer():\n    pass\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["authored"],
                   use_adapters=False)
    by_name = {f.evidence.split(":")[1]: f for f in rep.active()
               if "stub" in f.rule_id}
    assert by_name["verify_signature"].severity == "high"
    assert by_name["verify_signature"].dimension == "security"
    assert by_name["render_footer"].severity == "low"


def test_a_suppression_inside_a_python_string_is_not_a_suppression(tmp_path):
    """A `# noqa` in a string literal or a docstring is prose or a fixture: this
    probe's own module docstring explains the directive, and its tests write
    `"x = 1  # noqa\\n"` into temporary files. The regex census read both as
    blanket suppressions on the self-scan (FAIL-048). Only a comment counts."""
    (tmp_path / "a.py").write_text(
        '"""A bare `# noqa` silences every rule; `# type: ignore` does too."""\n'
        'FIXTURE = "x = 1  # noqa\\ny = 2  # nosec\\n"\n'
        'import os  # noqa\n'
    )
    found = _scan_text(tmp_path, "b.txt", "", ["assurance"])
    blanket = [f for f in found if "blanket-suppression" in f.rule_id]
    assert [f.location.start_line for f in blanket] == [3]
    census = [f for f in found if "suppression-census" in f.rule_id]
    assert census and "1 inline suppression" in census[0].description


def test_a_requirement_quoted_inside_a_one_line_list_is_declared(tmp_path):
    """`ast = ["tree-sitter>=0.23", "tree-sitter-language-pack>=0.9"]` is how a
    PEP 621 extra is usually written. The manifest reader anchored every name
    to the start of a line, so a package declared past the first position of
    an inline list was reported as undeclared (FAIL-049)."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = \"x\"\ndependencies = [\"PyYAML>=6.0\"]\n"
        "[project.optional-dependencies]\n"
        "ast = [\"tree-sitter>=0.23\", \"tree-sitter-language-pack>=0.9\"]\n"
    )
    (tmp_path / "app.py").write_text("import yaml\nimport tree_sitter\nimport tree_sitter_language_pack\nimport nothing_declares_me\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["authored"], use_adapters=False)
    names = {f.evidence.rsplit(":", 1)[-1] for f in rep.active() if "undeclared-import" in f.rule_id}
    assert names == {"nothing_declares_me"}


def test_prose_discussing_a_suppression_is_not_a_suppression(tmp_path):
    """README and the decision log explain what `# noqa` means. The
    suppression probe reported them as blanket suppressions."""
    found = _scan_text(tmp_path, "README.md",
                       "A bare `# noqa` silences every rule, and `checkov:skip` "
                       "without an id does the same.\n", ["assurance"])
    assert not [f for f in found if "blanket-suppression" in f.rule_id]


def test_the_broad_except_house_rule_matches_only_broad_clauses(tmp_path):
    """`(except_clause) @hit` matched every handler in the tree: 89 findings
    on a self-scan, `except OSError:` among them. The predicate is the rule."""
    from arbiter import ast as ts
    if not ts.available():
        pytest.skip("tree-sitter not installed")
    import yaml
    rule = next(r for r in yaml.safe_load((ROOT / "arbiter.yaml").read_text())["rules"]
                if r["id"] == "no-bare-except")
    rule = dict(rule, files="**/*.py")
    code = ("try:\n    pass\n"
            "except OSError:\n    pass\n"
            "except Exception:\n    pass\n"
            "except BaseException:\n    raise\n")
    found = _scan_text(tmp_path, "pkg/a.py", code, ["house_rules_ast"], {"rules": [rule]})
    hits = sorted(f.location.start_line for f in found if f.rule_id == "house/no-bare-except")
    assert hits == [5, 7]
