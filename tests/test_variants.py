"""Mutation variants (tools/variants.py): every transform keeps the program
parseable, keeps the marker on the line it was on, and leaves code it was
not asked to touch alone; the sweep scores markers and detects a rule that
loses its finding. Runs on snippets and one planted language."""
from __future__ import annotations

import importlib.util
import sys

import pytest
from helpers import ROOT

from arbiter import ast as _ts  # noqa: E402

# Every variant is a tree-sitter rewrite; the fast CI tier installs no grammars.
pytestmark = pytest.mark.skipif(not _ts.available(), reason="tree-sitter not installed")

_spec = importlib.util.spec_from_file_location("variants", ROOT / "tools" / "variants.py")
vt = importlib.util.module_from_spec(_spec)
sys.modules["variants"] = vt
_spec.loader.exec_module(vt)

SNIPPETS = {
    "python": ('def get_job(conn, job_id):\n    cur = conn.cursor()\n'
               '    cur.execute(f"SELECT * FROM jobs WHERE id = \'{job_id}\'")  # expect: x\n'
               '    return cur.fetchone()\n\n\ndef other():\n    return 1\n', 3),
    "typescript": ('import { exec } from "child_process";\nexport function run(name: string) {\n'
                   '  exec(`zip ${name}`); // expect: x\n}\nfunction other() { return 1; }\n', 3),
    "go": ('package x\n\nimport "fmt"\n\nfunc get(id string) {\n'
           '\trows, err := db.Query(fmt.Sprintf("SELECT %s", id)) // expect: x\n\t_, _ = rows, err\n}\n\n'
           'func other() {}\n', 6),
    "java": ('package a;\nclass A {\n    void f(String id) throws Exception {\n'
             '        stmt.executeQuery("SELECT " + id); // expect: x\n    }\n    void g() {}\n}\n', 4),
    "ruby": ('class A\n  def f(name)\n    system("zip #{name}") # expect: x\n  end\n\n  def g\n  end\nend\n', 3),
}


@pytest.mark.parametrize("lang", sorted(SNIPPETS))
@pytest.mark.parametrize("kind", vt.KINDS)
def test_transform_parses_and_tracks_the_marker(lang, kind):
    src, line = SNIPPETS[lang]
    rw = vt.rewrite(kind, src, lang, {line})
    if rw is None:
        pytest.skip(f"{kind} does not apply to this {lang} snippet")
    assert vt.parses_clean(rw.text, lang)
    assert line in rw.affected
    new = rw.text.split("\n")[rw.line_map[line] - 1]
    assert "expect: x" in new, f"marker did not travel with its line: {new!r}"
    assert rw.text != src


@pytest.mark.parametrize("lang", sorted(SNIPPETS))
def test_every_language_supports_hoist_helper_and_strings(lang):
    src, line = SNIPPETS[lang]
    for kind in ("hoist", "helper", "strings", "move"):
        assert vt.rewrite(kind, src, lang, {line}) is not None, f"{kind} on {lang}"


def test_strings_rewrites_each_python_style():
    src = ('def f(conn, a):\n    q = "SELECT %s" % a\n    r = "x {} y".format(a)\n'
           '    conn.execute(f"SELECT {a}")\n')
    rw = vt.rewrite("strings", src, "python", {2, 3, 4})
    lines = rw.text.split("\n")
    assert lines[1].strip() == 'q = f"SELECT {a}"'
    assert lines[2].strip() == 'r = f"x {a} y"'
    assert lines[3].strip() == 'conn.execute(("SELECT " + str(a)))'


def test_rename_leaves_attributes_keywords_and_shorthand_alone():
    py = 'def f(name, conn):\n    conn.name = name\n    g(name=name)\n'
    out = vt.rewrite("rename", py, "python", {2, 3}).text
    assert "g(name=" in out and ".name =" in out
    ts = 'function f(name: string) {\n  const id = name;\n  return { name, other: id };\n}\n'
    out = vt.rewrite("rename", ts, "typescript", {2, 3}).text
    assert "{ name, other: v1 }" in out, out  # a shorthand's variable keeps its name; `id` is free to go


def test_hoist_never_moves_a_heredoc_opener():
    rb = 'def f(db, o)\n  db.execute(<<~SQL)\n    SELECT #{o}\n  SQL\nend\n'
    assert vt.rewrite("hoist", rb, "ruby", {2}) is None


def test_a_variant_that_breaks_the_parse_is_discarded(monkeypatch):
    src, line = SNIPPETS["python"]
    monkeypatch.setattr(vt, "transform_strings",
                        lambda text, lang, lines: vt.Rewrite(text.replace("):", ")"), vt.identity_map(text), {line}))
    assert vt.rewrite("strings", src, "python", {line}) is None


def test_sweep_scores_markers_and_reports_a_rule_that_loses_its_finding(monkeypatch):
    rows = vt.run_language(ROOT / "fixtures" / "planted", "python", ["strings"], None)
    assert rows and all(r["kind"] == "strings" for r in rows)
    sql = [r for r in rows if r["rule"] == "arbiter/python.sql-injection"]
    assert sql and all(r["kept"] for r in sql), "today's sql rule follows every string style"
    # Seed a regression: the sql rule stops recognising concatenation.
    from arbiter import lang_rules
    real = lang_rules._run_rule

    def blind(pack, rule, f, parsed):
        out = real(pack, rule, f, parsed)
        if rule["id"] != "sql-injection":
            return out
        text = parsed[1].decode()
        lines = text.split("\n")
        return [x for x in out if '" + str(' not in lines[x.location.start_line - 1]]

    monkeypatch.setattr(lang_rules, "_run_rule", blind)
    rows = vt.run_language(ROOT / "fixtures" / "planted", "python", ["strings"], None)
    lost = [r for r in rows if r["rule"] == "arbiter/python.sql-injection" and not r["kept"]]
    assert lost, "the sweep must notice a rule that no longer fires on a rewritten defect"
    table = {t["rule"]: t for t in vt.robustness(rows)}
    assert table["arbiter/python.sql-injection"]["robustness"] < 1.0
