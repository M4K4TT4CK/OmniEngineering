"""One-hop data flow (Milestone 3): `dataflow.py` and the packs' `taint:` blocks.

Each case is a few lines of one language run through one rule; the planted
corpus (fixtures/planted/<lang>/{bad,good}/**/dataflow.*) holds the larger
shapes and `tools/variants.py` the hoist/helper/rename rewrites.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from arbiter import ast as ts
from arbiter import dataflow as df
from arbiter import lang_rules as lr

pytestmark = pytest.mark.skipif(not ts.available(), reason="tree-sitter not installed")

EXT = {"typescript": "ts", "python": "py", "go": "go", "java": "java", "ruby": "rb"}


def lines(tmp_path, lang: str, rule_id: str, code: str) -> list[int]:
    """Lines the pack rule `rule_id` reports on `code`."""
    path = tmp_path / f"case.{EXT[lang]}"
    path.write_text(code, encoding="utf-8")
    parsed = ts.parse_file(str(path), lang)
    if parsed is None:
        pytest.skip(f"no {lang} grammar")
    rule = next(r for r in lr.load_packs()[lang]["rules"] if r["id"] == rule_id)
    f = SimpleNamespace(language=lang, path=path.name, repo_id="r", abspath=str(path), role="source")
    return sorted(x.location.start_line for x in lr._run_rule(lang, rule, f, parsed))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _pack(taint: dict) -> dict:
    return {"pack": "t", "languages": ["typescript"], "rules": [
        {"id": "r", "title": "t", "query": "((identifier) @hit)", "remediation": "x", "taint": taint}]}


@pytest.mark.parametrize("taint,msg", [
    ({"sinks": "((identifier) @hit)"}, "@sink"),
    ({"sinks": "((identifier) @sink) @hit"}, "sources"),
    ({"mode": "nonconst", "sinks": "((identifier) @sink) @hit", "hops": 3}, "hops"),
    ({"mode": "both", "sinks": "((identifier) @sink) @hit"}, "mode"),
    ({"mode": "nonconst", "sinks": "((identifier) @sink) @hit", "extra": 1}, "unknown taint keys"),
    ({"mode": "nonconst", "sinks": "((identifier) @sink) @hit", "where": {"a": "("}}, "regex"),
    ({"mode": "nonconst", "sinks": "((identifier) @sink) @hit", "sanitizers": "((identifier) @x)"},
     "@sanitizer"),
])
def test_taint_schema_rejects(taint, msg):
    with pytest.raises(lr.PackError, match=msg):
        lr.validate_pack(_pack(taint), "t.yaml")


def test_taint_schema_accepts_minimal():
    lr.validate_pack(_pack({"mode": "nonconst", "sinks": "((identifier) @sink) @hit"}), "t.yaml")


def test_shipped_taint_queries_compile():
    for name, data in lr.load_packs().items():
        lr.compile_pack(data, name)


# ---------------------------------------------------------------------------
# Engine: constant folding and branches
# ---------------------------------------------------------------------------

def _value_at(lang: str, code: str, tmp_path, marker: str = "SINK"):
    path = tmp_path / f"v.{EXT[lang]}"
    path.write_text(code, encoding="utf-8")
    tree, _src = ts.parse_file(str(path), lang)
    target = None
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type in ("call_expression", "call", "method_invocation") and df.text(n).startswith(marker):
            args = n.child_by_field_name("arguments")
            target = df.named(args)[0]
        stack.extend(n.children)
    seen = df.analyse(lang, tree.root_node, df.Spec(mode="nonconst"), [target])
    return seen[df.nkey(target)]


@pytest.mark.parametrize("body,kind", [
    ("let x = p; x = 'k'; SINK(x);", df.CLEAN),
    ("let x = 'k'; if (false) { x = p; } SINK(x);", df.CLEAN),
    ("let x = 'k'; if (c) { x = p; } SINK(x);", df.UNKNOWN),
    ("const n = 106; const x = (7 * 18) + n > 200 ? 'a' : p; SINK(x);", df.CLEAN),
    ("const n = 106; const x = (7 * 18) + n > 300 ? 'a' : p; SINK(x);", df.UNKNOWN),
    ("let x = 'a'; switch ('B') { case 'A': x = p; break; case 'B': x = 'b'; break; default: x = p; } SINK(x);",
     df.CLEAN),
    ("const m = {a: 'v', b: p}; SINK(m.a);", df.CLEAN),
    ("const m = {a: 'v', b: p}; SINK(m.b);", df.UNKNOWN),
    ("let x = ''; for (const s of ['a', 'b']) { x += s; } SINK(x);", df.CLEAN),
    ("function id(v) { return v; } SINK(id('k'));", df.CLEAN),
    ("function id(v) { return v; } SINK(id(p));", df.UNKNOWN),
])
def test_typescript_constant_propagation(tmp_path, body, kind):
    code = "function f(p, c) {\n" + body + "\n}\n"
    assert _value_at("typescript", code, tmp_path).kind == kind


def test_budget_exhaustion_changes_nothing(tmp_path):
    path = tmp_path / "b.ts"
    path.write_text("function f(p) { let x = p; " + "x = x + 'a'; " * 50 + "SINK(x); }\n", encoding="utf-8")
    tree, _src = ts.parse_file(str(path), "typescript")
    assert df.analyse("typescript", tree.root_node, df.Spec(mode="nonconst"),
                      [tree.root_node.named_children[0]], budget=10) is None


# ---------------------------------------------------------------------------
# TypeScript rules
# ---------------------------------------------------------------------------

TS_HTML = """\
function wrap(n: string) { return "<b>" + n + "</b>"; }
app.get("/a", (req, res) => {
  const name = req.query.name;
  res.send(`<h1>${name}</h1>`);
});
app.get("/b", (req, res) => { res.send(wrap(req.params.id)); });
app.get("/c", (req, res) => {
  let name = req.query.name;
  name = "guest";
  res.send(`<h1>${name}</h1>`);
});
app.get("/d", (req, res) => { res.send(`<h1>${escapeHtml(req.query.name)}</h1>`); });
app.get("/e", (req, res) => { res.send({ name: req.query.name }); });
"""


def test_typescript_html_from_request(tmp_path):
    assert lines(tmp_path, "typescript", "html-from-request", TS_HTML) == [4, 6]


def test_typescript_html_from_request_typed_params(tmp_path):
    code = ("export function h(v1: Request, v2: Response) {\n"
            "  const t = v1.query.name;\n  v2.send('<p>' + t + '</p>');\n}\n")
    assert lines(tmp_path, "typescript", "html-from-request", code) == [3]


def test_typescript_sql_hoisted_and_helper(tmp_path):
    code = """\
function _same(v: any): any { return v; }
async function f(req: Request) {
  const q = `SELECT * FROM t WHERE id = ${req.params.id}`;
  await pool.query(q);
  await pool.query(_same(`SELECT * FROM t WHERE id = ${req.params.id}`));
  await pool.query(`SELECT * FROM ${TABLE}`);
}
"""
    assert lines(tmp_path, "typescript", "sql-string-built", code) == [4, 5]


def test_typescript_argument_injection_hoisted_tool_and_double_dash(tmp_path):
    code = """\
import { execFile } from "node:child_process";
const GIT = "git";
export function a(req: Request) {
  execFile(GIT, ["clone", req.body.repo, "/srv"]);
}
export function b(req: Request) {
  const args = ["clone", "--", req.body.repo, "/srv"];
  execFile(GIT, args);
}
"""
    assert lines(tmp_path, "typescript", "argument-injection", code) == [4]


def test_typescript_ssrf_fixed_origin_is_not_reported(tmp_path):
    code = """\
export async function a(req: Request) {
  const u = req.query.url as string;
  await fetch(u);
  await fetch(`https://api.example.com/v1/${u}`);
}
"""
    assert lines(tmp_path, "typescript", "ssrf-request-url", code) == [3]


def test_typescript_shell_command_constant_is_dropped(tmp_path):
    code = """\
import { execSync } from "child_process";
const p = "./src/a.ts";
execSync(`git add ${p}`);
export function f(dir: string) { execSync(`du -sh ${dir}`); }
"""
    assert lines(tmp_path, "typescript", "shell-command-built", code) == [4]
