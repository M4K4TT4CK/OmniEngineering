"""Every planted defect is reported, and no correct twin is.

The corpus is fixtures/planted/<lang>/{bad,good}/; the format is documented in
src/arbiter/planted.py. Native probes only: external analyzers are scored by
tools/planted_score.py, not here, so this runs in the fast tier everywhere.
"""
from __future__ import annotations

import pytest
from helpers import ROOT

from arbiter import ast as ts
from arbiter import planted

CORPUS = ROOT / "fixtures" / "planted"
LANGS = planted.languages(CORPUS)


def test_every_ide_language_has_a_corpus():
    assert set(LANGS) >= {"python", "typescript", "go", "java", "ruby"}


@pytest.mark.parametrize("language", LANGS)
def test_planted_defects_are_reported_and_twins_are_silent(language):
    if not ts.available():
        pytest.skip("tree-sitter not installed")
    s = planted.score(CORPUS, language)
    assert s.expected, f"{language}: no expect: markers in bad/"
    assert not s.missed, "missed: " + "; ".join(f"{e.rule} at {e.path}:{e.line}" for e in s.missed)
    assert not s.false_positives_good, f"fired on a correct twin: {s.false_positives_good}"
    assert not s.unexpected_bad, f"fired on an unmarked line: {s.unexpected_bad}"


def test_markers_parse_every_comment_style(tmp_path):
    (tmp_path / "a.go").write_text("x() // expect: arbiter/go.a, arbiter/go.b\n", encoding="utf-8")
    (tmp_path / "b.rb").write_text("y # expect-ext: semgrep/x\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("z = 1\n", encoding="utf-8")
    got = sorted((e.rule, e.path, e.line, e.external) for e in planted.markers(tmp_path))
    assert got == [("arbiter/go.a", "a.go", 1, False), ("arbiter/go.b", "a.go", 1, False),
                   ("semgrep/x", "b.rb", 1, True)]


def test_a_broken_pack_fails_loudly():
    from arbiter.lang_rules import PackError, validate_pack
    with pytest.raises(PackError, match="@hit"):
        validate_pack({"languages": ["go"], "rules": [
            {"id": "x", "title": "t", "remediation": "r", "query": "(identifier) @nope"}]}, "t.yaml")
    with pytest.raises(PackError, match="unknown keys"):
        validate_pack({"languages": ["go"], "rules": [
            {"id": "x", "title": "t", "remediation": "r", "query": "(_) @hit", "sevrity": "high"}]},
            "t.yaml")
