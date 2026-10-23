"""The AI-written-code corpus scorer (tools/ai_corpus_score.py): matching,
recall/precision arithmetic, label validation, and the committed corpus's
labels pointing at real lines. The pure functions run on labels built here;
one test scans a tiny app end to end."""
from __future__ import annotations

import importlib.util
import json
import sys

import pytest
from helpers import ROOT

_spec = importlib.util.spec_from_file_location("ai_corpus_score", ROOT / "tools" / "ai_corpus_score.py")
acs = importlib.util.module_from_spec(_spec)
sys.modules["ai_corpus_score"] = acs
_spec.loader.exec_module(acs)

CFG = {"metric_rule_prefixes": ["arbiter/ast.", "arbiter/quality.", "arbiter/assurance.census"]}


def _defect(**kw):
    d = {"id": "D1", "file": "app.py", "line": 10, "cwe": ["CWE-89"], "category": "sql-injection",
         "severity": "high", "justification": "f-string into execute", "source": "independent"}
    d.update(kw)
    return d


def _finding(**kw):
    f = {"rule": "arbiter/python.sql-injection", "path": "app.py", "line": 10, "cwe": ["CWE-89"],
         "severity": "high", "title": "t"}
    f.update(kw)
    return f


def test_a_finding_detects_a_defect_by_cwe_within_tolerance():
    assert acs.detects(_finding(line=12), _defect(), tolerance=2)
    assert not acs.detects(_finding(line=13), _defect(), tolerance=2)
    assert acs.detects(_finding(line=13), _defect(end_line=11), tolerance=2)


def test_same_line_but_unrelated_weakness_is_not_a_detection():
    assert not acs.detects(_finding(cwe=["CWE-798"], rule="arbiter/secrets.x"), _defect(), 2)


def test_match_rules_accepts_a_neighbouring_cwe():
    d = _defect(cwe=["CWE-22"], match_rules=["arbiter/python.path-*"])
    assert acs.detects(_finding(cwe=["CWE-23"], rule="arbiter/python.path-traversal"), d, 2)


def test_other_file_never_matches():
    assert not acs.detects(_finding(path="other.py"), _defect(), 2)


def test_score_splits_findings_into_tp_listed_fp_and_unreviewed():
    labels = {"defects": [_defect(), _defect(id="D2", line=50, cwe=["CWE-918"], source="reconcile")],
              "false_alarms": [{"rule": "arbiter/python.x", "file": "app.py", "line": 30, "reason": "safe"},
                               {"rule": "arbiter/python.gone", "file": "app.py", "line": 1, "reason": "old"}]}
    findings = [_finding(), _finding(rule="arbiter/python.x", line=30, cwe=[]),
                _finding(rule="arbiter/python.y", line=70, cwe=[]),
                _finding(rule="arbiter/ast.high-complexity", line=5, cwe=[]),
                _finding(rule="arbiter/assurance.census", line=1, cwe=[], severity="info")]
    s = acs.score_app("python/x", "python", labels, findings, CFG)
    assert [d["id"] for d in s.detected] == ["D1"]
    assert [d["id"] for d in s.missed] == ["D2"]
    assert len(s.true_positives) == 1 and len(s.false_alarms) == 1 and len(s.unreviewed) == 1
    assert s.out_of_scope == 2
    assert [fa["rule"] for fa in s.stale_false_alarms] == ["arbiter/python.gone"]
    summ = acs.summarize([s])["all"]
    assert summ["recall"] == 0.5 and summ["recall_independent"] == 1.0
    assert summ["precision"] == pytest.approx(1 / 3, abs=1e-3)
    assert summ["precision_reviewed"] == 0.5


def test_validate_labels_reports_missing_files_lines_and_fields(tmp_path):
    app = tmp_path / "python" / "demo"
    (app / "app").mkdir(parents=True)
    (app / "app" / "app.py").write_text("a = 1\nb = 2\n")
    (app / "labels.json").write_text(json.dumps({"defects": [
        _defect(line=2), _defect(id="D2", line=9), _defect(id="D3", file="nope.py"),
        {"id": "D4", "file": "app.py", "line": 1}]}))
    problems = acs.validate_labels(app)
    assert not any(":D1" in p for p in problems)
    assert any(":D2" in p and "outside" in p for p in problems)
    assert any(":D3" in p and "no file" in p for p in problems)
    assert any(":D4" in p and "missing justification" in p for p in problems)


def test_the_committed_corpus_labels_point_at_real_lines():
    corpus = ROOT / "fixtures" / "ai-corpus"
    found = acs.apps(corpus)
    assert len(found) >= 25, "five tasks in five languages"
    problems = [p for a in found for p in acs.validate_labels(a)]
    assert problems == []
    manifest = json.loads((corpus / "manifest.json").read_text())
    have = {a.relative_to(corpus).as_posix() for a in found}
    assert {f"{m['language']}/{m['task']}" for m in manifest["apps"]} == have
    for m in manifest["apps"]:
        assert m["prompt"] and m["model"] and m["date"]


def test_end_to_end_scan_of_a_tiny_app(tmp_path):
    app = tmp_path / "python" / "tiny"
    (app / "app").mkdir(parents=True)
    (app / "app" / "db.py").write_text(
        "import sqlite3\n\n\ndef find(conn, name):\n"
        "    return conn.execute(f\"SELECT * FROM users WHERE name = '{name}'\").fetchall()\n")
    (app / "labels.json").write_text(json.dumps({"defects": [_defect(file="db.py", line=5)]}))
    out = tmp_path / "score.json"
    assert acs.main(["--corpus", str(tmp_path), "--json", str(out), "--markdown", str(tmp_path / "s.md")]) == 0
    data = json.loads(out.read_text())
    assert data["summary"]["all"]["defects"] == 1
    assert data["summary"]["all"]["recall"] == 1.0
