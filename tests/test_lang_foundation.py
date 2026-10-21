"""The shared foundation every language pack stands on (ARB-060).

CWE classification, SARIF taxa, grammar availability, test-file naming and
stack detection for each IDE language.
"""
from __future__ import annotations

import json

import pytest

from arbiter import ast as ts
from arbiter import cwe
from arbiter.core import Finding, Location, Report
from arbiter.engine import run_scan
from arbiter.inventory import classify
from arbiter.policy import load_config


def test_cwe_is_normalised_from_every_shape_analyzers_use():
    assert cwe.normalise("CWE-89: Improper Neutralization of ...") == ["CWE-89"]
    assert cwe.normalise(["CWE-79: XSS", "cwe-79", 352]) == ["CWE-79", "CWE-352"]
    assert cwe.normalise({"id": 502}) == ["CWE-502"]
    assert cwe.normalise("78") == ["CWE-78"]
    assert cwe.normalise(None) == [] and cwe.normalise("no weakness here") == []


def test_native_rules_get_the_most_specific_cwe():
    assert cwe.for_rule("arbiter/secrets.stripe-key") == ["CWE-798"]
    assert cwe.for_rule("arbiter/concurrency.unsynchronised-counter") == ["CWE-362", "CWE-366"]
    assert cwe.for_rule("arbiter/concurrency.check-then-act-unlocked") == ["CWE-362"]
    assert cwe.for_rule("arbiter/quality.file-too-long") == []


def test_a_rule_that_declares_its_cwe_keeps_it():
    f = Finding(rule_id="arbiter/secrets.x", title="t", cwe=["CWE-321"])
    cwe.apply([f])
    assert f.cwe == ["CWE-321"]


def test_sarif_carries_cwe_as_code_scanning_reads_it(tmp_path):
    from arbiter.report import write_sarif
    f = Finding(rule_id="arbiter/go.http-default-client", title="t", cwe=["CWE-400"],
                location=Location(path="a.go", start_line=3))
    write_sarif(Report(findings=[f]), str(tmp_path / "r.sarif"))
    rule = json.loads((tmp_path / "r.sarif").read_text())["runs"][0]["tool"]["driver"]["rules"][0]
    assert "external/cwe/cwe-400" in rule["properties"]["tags"]
    assert rule["properties"]["cwe"] == ["CWE-400"]


def test_a_finding_round_trips_its_cwe():
    f = Finding(rule_id="r", title="t", cwe=["CWE-89"])
    assert Finding.from_dict(f.to_dict()).cwe == ["CWE-89"]


def test_a_language_with_no_cached_grammar_is_not_assessed(tmp_path, monkeypatch):
    if not ts.available():
        pytest.skip("tree-sitter not installed")
    (tmp_path / "a.go").write_text('package main\nimport "net/http"\nfunc f(){ http.Get("x") }\n')
    (tmp_path / "go.mod").write_text("module m\n\ngo 1.22\n")
    monkeypatch.setattr(ts, "cached_grammars", lambda: {"python"})
    rep = run_scan([str(tmp_path)], load_config(None), only=["lang_go"], use_adapters=False)
    outcome = next(p for p in rep.probes if p.name == "lang_go")
    assert outcome.status == "skipped" and "missing tree-sitter grammar: go" in outcome.reason
    assert not rep.active()


def test_tsx_files_parse_with_the_tsx_grammar():
    assert ts.grammar_for("src/App.tsx", "typescript") == "tsx"
    assert ts.grammar_for("src/app.ts", "typescript") == "typescript"


@pytest.mark.parametrize("path", [
    "src/foo.test.ts", "web/a.spec.js", "lib/user_spec.rb", "spec/a_spec.rb",
    "src/test/java/a/FooTest.java", "pkg/x_test.go", "tests/test_a.py",
])
def test_every_ide_languages_test_files_are_tests(path):
    assert classify(path, "x") == "test"


@pytest.mark.parametrize("path", ["src/main/java/a/Foo.java", "lib/app.rb", "src/attest.py",
                                  "cmd/main.go", "src/latest.ts"])
def test_source_files_are_not_mistaken_for_tests(path):
    assert classify(path, "x") == "source"


@pytest.mark.parametrize("manifest,stack", [("pom.xml", "java"), ("build.gradle", "java"),
                                            ("Gemfile", "ruby"), ("go.mod", "go")])
def test_java_ruby_and_go_stacks_are_detected(tmp_path, manifest, stack):
    from arbiter.inventory import build_inventory
    (tmp_path / manifest).write_text("x\n")
    inv = build_inventory([type("R", (), {"id": "root", "path": str(tmp_path)})()])
    assert stack in inv.stacks


@pytest.mark.parametrize("header,reported", [
    ("//go:build ignore\n\n", False),
    ("//go:build ignore\n// +build ignore\n\n", False),
    ("// +build ignore\n\n", False),
    ("// Code generator.\n//go:build ignore\n\n", False),
    ("//go:build linux\n\n", True),     # an ordinary constraint still builds
    ("", True),
])
def test_go_build_ignore_files_may_import_undeclared_modules(tmp_path, header, reported):
    """client_golang's collector generators are `//go:build ignore` scripts that
    import github.com/hashicorp/go-version, which its go.mod does not declare."""
    (tmp_path / "go.mod").write_text("module example.com/m\n\ngo 1.22\n")
    (tmp_path / "gen.go").write_text(
        header + 'package main\n\nimport "github.com/hashicorp/go-version"\n\n'
        'func main() { _ = version.Must }\n')
    rep = run_scan([str(tmp_path)], load_config(None), only=["authored"], use_adapters=False,
                   use_cache=False)
    hits = [f for f in rep.active() if "go-version" in f.title]
    assert bool(hits) == reported, [f.title for f in hits]
