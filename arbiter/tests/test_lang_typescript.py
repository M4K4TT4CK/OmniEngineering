"""The TypeScript/JavaScript rule pack (ARB-061), beyond the planted corpus.

The corpus test proves every planted defect is caught and every twin is
silent. These tests pin down the things that cannot be read off a fixture:
that every query compiles under all three grammars a rule can meet (`.ts`,
`.tsx`, `.js`), that the shapes which fooled tree-sitter's query language
during development stay fixed, and that the rules narrow the way they claim.
"""
from __future__ import annotations

import pytest
from helpers import ROOT

from arbiter import ast as ts
from arbiter.engine import run_scan
from arbiter.lang_rules import load_packs
from arbiter.policy import load_config

PACK = load_packs()["typescript"]
RULES = {r["id"]: r for r in PACK["rules"]}
QUERY_KEYS = ("query", "unless_inside", "unless_in_scope", "unless_in_file", "requires_file")

pytestmark = pytest.mark.skipif(not ts.available(), reason="tree-sitter not installed")


def _scan(tmp_path, files: dict[str, str], only_rule: str | None = None) -> list:
    # A fresh directory per call: a test that scans twice must not see the
    # first case's files in the second scan.
    root = tmp_path / f"case{len(list(tmp_path.iterdir()))}"
    root.mkdir()
    (root / "package.json").write_text(
        '{"name": "x", "dependencies": {"axios": "1.0.0", "express": "4.0.0", "pg": "8.0.0"}}')
    for name, body in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(body)
    found = run_scan([str(root)], load_config(None), only=["lang_typescript"],
                     use_adapters=False, use_cache=False).active()
    if only_rule:
        found = [f for f in found if f.rule_id == f"arbiter/typescript.{only_rule}"]
    return found


def _lines(findings) -> list[int]:
    return sorted(f.location.start_line for f in findings)


# --------------------------------------------------------------- the pack

def test_pack_covers_both_languages_and_every_rule_has_a_cwe():
    assert set(PACK["languages"]) == {"typescript", "javascript"}
    for rule in PACK["rules"]:
        assert rule.get("cwe"), rule["id"]
        for key in ("severity", "confidence", "title", "description", "remediation", "tags"):
            assert rule.get(key), f"{rule['id']} lacks {key}"


@pytest.mark.parametrize("grammar", ["typescript", "tsx", "javascript"])
def test_every_query_compiles_under_every_grammar_it_can_meet(grammar):
    import tree_sitter
    from tree_sitter_language_pack import get_language
    lang = get_language(grammar)
    for rule in PACK["rules"]:
        if rule.get("files") == "*.[jt]sx" and grammar == "typescript":
            continue  # a .ts file never reaches a JSX rule
        for key in QUERY_KEYS:
            if rule.get(key):
                tree_sitter.Query(lang, rule[key])


def test_no_predicate_sits_outside_its_pattern():
    """A predicate at depth 0 is parsed as its own pattern and silently
    ignored, which made `empty-catch-in-loop` report every catch in a loop
    during development. Also: a predicate written inside `[...]` is dropped
    and the pattern matches everything."""
    for rule in PACK["rules"]:
        for key in QUERY_KEYS:
            q = rule.get(key) or ""
            depth = 0
            alt = 0
            for i, ch in enumerate(q):
                if ch == "(":
                    if q[i:i + 2] == "(#":
                        assert depth > 0, f"{rule['id']}.{key}: predicate outside its pattern"
                        assert alt == 0 or depth > alt, \
                            f"{rule['id']}.{key}: predicate directly inside an alternation"
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif ch == "[":
                    depth += 1
                    alt = depth
                elif ch == "]":
                    depth -= 1
                    alt = 0


# ------------------------------------------------------------ the grammars

def test_the_same_defect_is_caught_in_ts_tsx_and_js(tmp_path):
    body = 'export function run(db, id) {\n  return db.query(`SELECT * FROM t WHERE id = ${id}`);\n}\n'
    found = _scan(tmp_path, {"a.ts": body, "b.tsx": body, "c.js": body, "d.mjs": body},
                  "sql-string-built")
    assert sorted(f.location.path for f in found) == ["a.ts", "b.tsx", "c.js", "d.mjs"]


def test_jsx_rule_runs_on_tsx_and_jsx_only(tmp_path):
    jsx = 'export const V = ({ h }) => <div dangerouslySetInnerHTML={{ __html: h }} />;\n'
    found = _scan(tmp_path, {"View.tsx": jsx, "View.jsx": jsx, "plain.ts": "export const x = 1;\n"},
                  "dangerously-set-inner-html")
    assert sorted(f.location.path for f in found) == ["View.jsx", "View.tsx"]


# ------------------------------------------------------------ concurrency

def test_check_then_act_needs_an_await_between_check_and_change(tmp_path):
    racy = (
        "const cache = new Map();\n"
        "export async function get(k) {\n"
        "  if (!cache.has(k)) {\n"
        "    const v = await load(k);\n"
        "    cache.set(k, v);\n"
        "  }\n"
        "}\n"
    )
    atomic = racy.replace("const v = await load(k);\n    cache.set(k, v);", "cache.set(k, load(k));")
    assert _lines(_scan(tmp_path, {"racy.ts": racy}, "async-check-then-act")) == [3]
    assert not _scan(tmp_path, {"ok.ts": atomic}, "async-check-then-act")


def test_check_then_act_on_a_property_needs_the_same_object(tmp_path):
    src = (
        "export async function claim(job, other) {\n"
        "  if (job.state === 'queued') {\n"
        "    await notify();\n"
        "    other.state = 'running';\n"
        "  }\n"
        "}\n"
    )
    assert not _scan(tmp_path, {"a.ts": src}, "async-check-then-act")
    assert _lines(_scan(tmp_path, {"b.ts": src.replace("other.state", "job.state")},
                        "async-check-then-act")) == [2]


# -------------------------------------------------------------- timeouts

def test_fetch_options_passed_by_name_are_not_reported(tmp_path):
    src = (
        "export async function a(init) { return fetch('https://x', init); }\n"
        "export async function b() { return fetch('https://x', { ...base }); }\n"
        "export async function c() { return fetch('https://x', { method: 'POST' }); }\n"
    )
    assert _lines(_scan(tmp_path, {"a.ts": src}, "fetch-no-timeout")) == [3]


def test_a_timer_in_the_same_function_counts_as_a_timeout(tmp_path):
    src = (
        "export async function a() {\n"
        "  const c = new AbortController();\n"
        "  setTimeout(() => c.abort(), 5000);\n"
        "  return fetch('https://x');\n"
        "}\n"
    )
    assert not _scan(tmp_path, {"a.ts": src}, "fetch-no-timeout")


def test_axios_defaults_timeout_silences_the_whole_file(tmp_path):
    src = "import axios from 'axios';\nexport const a = () => axios.get('https://x');\n"
    assert _lines(_scan(tmp_path, {"a.ts": src}, "axios-no-timeout")) == [2]
    assert not _scan(tmp_path, {"b.ts": "axios.defaults.timeout = 5000;\n" + src}, "axios-no-timeout")


def test_http_rule_needs_the_http_module_in_the_file(tmp_path):
    # An axios instance named `http` is not Node's http module.
    src = "const http = axios.create({ timeout: 1 });\nexport const a = () => http.get('/x');\n"
    assert not _scan(tmp_path, {"a.ts": src}, "http-request-no-timeout")
    real = "import http from 'node:http';\nexport const a = () => http.get('http://x', () => {});\n"
    assert _lines(_scan(tmp_path, {"b.ts": real}, "http-request-no-timeout")) == [2]


# ----------------------------------------------------------- retry, worker

def test_retry_loop_with_a_sleep_anywhere_in_the_function_is_fine(tmp_path):
    loop = (
        "export async function f() {\n"
        "  for (;;) {\n"
        "    try { return await call(); } catch (e) { log(e); SLEEP }\n"
        "  }\n"
        "}\n"
    )
    assert _lines(_scan(tmp_path, {"a.ts": loop.replace("SLEEP", "")}, "retry-no-backoff")) == [2]
    assert not _scan(tmp_path, {"b.ts": loop.replace("SLEEP", "await sleep(100);")}, "retry-no-backoff")
    assert not _scan(tmp_path, {"c.ts": loop.replace(
        "SLEEP", "await new Promise(r => setTimeout(r, 100));")}, "retry-no-backoff")


def test_worker_loop_that_checks_a_signal_or_breaks_is_fine(tmp_path):
    loop = "export async function w(signal) {\n  while (true) {\n    const j = await next();\n    BODY\n  }\n}\n"
    assert _lines(_scan(tmp_path, {"a.ts": loop.replace("BODY", "await run(j);")}, "worker-loop-no-stop")) == [2]
    assert not _scan(tmp_path, {"b.ts": loop.replace("BODY", "if (signal.aborted) break;")}, "worker-loop-no-stop")
    assert not _scan(tmp_path, {"c.ts": loop.replace("BODY", "if (!running) return;")}, "worker-loop-no-stop")
    # A sync loop with no await is not a worker loop.
    assert not _scan(tmp_path, {"d.ts": loop.replace("await next()", "next()").replace("BODY", "run(j);")},
                     "worker-loop-no-stop")


# -------------------------------------------------------------- swallowed

def test_empty_catch_is_low_outside_a_loop_and_medium_inside(tmp_path):
    src = (
        "export async function a() {\n"
        "  try { await x(); } catch {}\n"
        "  for (const i of items) {\n"
        "    try { await y(i); } catch (e) { /* skip */ }\n"
        "  }\n"
        "  try { await z(); } catch (e) { log(e); }\n"
        "}\n"
    )
    found = _scan(tmp_path, {"a.ts": src})
    by_rule = {f.rule_id.rsplit(".", 1)[1]: (f.location.start_line, f.severity) for f in found
               if "empty-catch" in f.rule_id}
    assert by_rule == {"empty-catch": (2, "low"), "empty-catch-in-loop": (4, "medium")}


def test_promise_catch_with_a_real_handler_is_fine(tmp_path):
    src = (
        "p.catch(() => {});\n"
        "p.catch(() => undefined);\n"
        "p.catch(function () {});\n"
        "p.catch((e) => log(e));\n"
        "p.catch(() => { log('failed'); });\n"
    )
    assert _lines(_scan(tmp_path, {"a.js": src}, "promise-catch-noop")) == [1, 2, 3]


# ------------------------------------------------------------------ stubs

def test_stub_is_the_first_statement_not_a_guard(tmp_path):
    src = (
        "export function a() { throw new Error('not implemented'); }\n"
        "export function b(x) { if (!x) throw new Error('not implemented for empty'); return x; }\n"
        "export function c() { throw new Error('connection refused'); }\n"
        "export const d = () => { throw new Error('TODO'); };\n"
    )
    assert _lines(_scan(tmp_path, {"a.ts": src}, "stub-throws-not-implemented")) == [1, 4]


# ------------------------------------------------------------------ tests

def test_test_rules_run_only_on_test_files(tmp_path):
    src = "it('works', function () { run(); });\ntest.skip('later', () => { expect(1).toBe(1); });\n"
    assert not _scan(tmp_path, {"src/a.ts": src})
    found = _scan(tmp_path, {"src/a.test.ts": src})
    assert sorted(f.rule_id.rsplit(".", 1)[1] for f in found) == ["skipped-test", "test-without-assertion"]


def test_a_function_callback_test_that_asserts_is_fine(tmp_path):
    src = (
        "it('a', function () { expect(run()).toBe(1); });\n"
        "it('b', (done) => { run().then((v) => { assert.equal(v, 1); done(); }); });\n"
        "it('c', async function () { await request(app).get('/').expect(200); });\n"
    )
    assert not _scan(tmp_path, {"a.test.js": src}, "test-without-assertion")


# ------------------------------------------- precision on real code (ARB-061)
# Each case below is a shape that fired on a real repository (zod, express,
# juice-shop) and was judged a false positive; the fix is pinned here.

def test_eval_of_a_literal_is_not_reported(tmp_path):
    # zod: `eval("...")` on a constant string is a feature probe, not injection.
    src = (
        "export const a = () => eval('typeof globalThis');\n"
        "export const b = (s) => eval(s);\n"
        "export const c = (r) => eval(r.body.x);\n"
        "export const d = (n) => eval(`${n} + 1`);\n"
    )
    assert _lines(_scan(tmp_path, {"a.js": src}, "eval-call")) == [2, 3, 4]


def test_response_with_a_fallback_value_is_not_html_from_request(tmp_path):
    src = (
        "app.get('/a', (req, res) => { res.send(cache.get(req.params.id) || { error: 'x' }); });\n"
        "app.get('/b', (req, res) => { res.send('hi ' + req.params.name); });\n"
    )
    assert _lines(_scan(tmp_path, {"a.js": src}, "html-from-request")) == [2]


def test_deliberate_empty_catch_is_not_reported(tmp_path):
    # zod: `catch (_err) {}` in coercion and benchmarks; scripts that say why.
    src = (
        "export function a() {\n"
        "  try { x(); } catch (_err) {}\n"
        "  try { x(); } catch (_e: any) {}\n"
        "  try { x(); } catch {\n"
        "    // unreadable directories are skipped\n"
        "  }\n"
        "  try { x(); } catch (e) {}\n"
        "  try { x(); } catch {} // trailing comment is not an explanation\n"
        "}\n"
    )
    found = _scan(tmp_path, {"a.ts": src}, "empty-catch")
    assert _lines(found) == [7, 8]
    assert {f.confidence for f in found} == {"medium"}


def test_random_names_need_a_secret_word_not_an_id_suffix(tmp_path):
    # three.js `generateUUID: function () {...}` and `(function () {...})()`
    # are helper bodies, not values drawn for a secret; `valid` and `decode`
    # only end in "id" / "code".
    src = (
        "export const M = {\n"
        "  generateUUID: function () { return Math.random().toString(16); },\n"
        "  objectId: (function () { return () => Math.random(); })(),\n"
        "  secret: '' + Math.random(),\n"
        "};\n"
        "const valid = items[Math.floor(Math.random() * 3)];\n"
        "const decode = Math.random() > 0.5;\n"
        "const userId = Math.random().toString(36);\n"
        "const api_key = Math.random().toString(36);\n"
        "const requestId = Math.random().toString(36).substring(2, 15);\n"
        "const resetCode = Math.random().toString(36);\n"
    )
    # bullmq child-processor: an IPC correlation id (`requestId`, `userId`)
    # is an identifier, not a secret; only secret words and reset/verify/
    # invite codes, keys and ids count.
    assert _lines(_scan(tmp_path, {"a.ts": src}, "insecure-random-secret")) == [4, 9, 11]


def test_done_handed_on_or_a_does_not_throw_title_counts_as_an_assertion(tmp_path):
    # express test/Router.js: `{ end: done }` fails by timeout if the route is
    # wrong; "should not throw ..." says that not throwing is the check.
    src = (
        "it('routes', function (done) { router.handle(req, { end: done }, function () {}); });\n"
        "it('should not throw if all callbacks are functions', function () { build(); });\n"
        "it(\"doesn't throw on empty input\", function () { build(); });\n"
        "it('builds', function () { build(); });\n"
        "it('waits', function (done) { setTimeout(function () { done(); }, 1); });\n"
    )
    assert _lines(_scan(tmp_path, {"a.test.js": src}, "test-without-assertion")) == [4, 5]


# ------------------------- precision on clean libraries: got, bullmq (ARB-061)

def test_sql_with_a_constant_or_quoted_identifier_is_not_reported(tmp_path):
    # bullmq migrator: DDL identifiers cannot be bound, so they are quoted first.
    src = (
        "export async function m(c, schema, name) {\n"
        "  const quotedSchema = c.escapeIdentifier(schema);\n"
        "  await c.query(`CREATE SCHEMA IF NOT EXISTS ${quotedSchema}`);\n"
        "  await c.query(`SET search_path TO ${c.escapeIdentifier(schema)}`);\n"
        "  await c.query(`DROP SCHEMA IF EXISTS \"${DEFAULT_SCHEMA}\" CASCADE`);\n"
        "  await c.query(`SELECT * FROM t WHERE name = '${name}'`);\n"
        "  await c.query(`DROP SCHEMA ${schema}`);\n"
        "}\n"
    )
    assert _lines(_scan(tmp_path, {"a.ts": src}, "sql-string-built")) == [6, 7]


def test_await_inside_a_nested_async_function_does_not_split_check_and_act(tmp_path):
    # bullmq postgres-connection: the memoised promise is stored synchronously.
    src = (
        "export class C {\n"
        "  get() {\n"
        "    if (!this.p) {\n"
        "      const establishing = (async () => { await open(); })();\n"
        "      this.p = establishing;\n"
        "    }\n"
        "    return this.p;\n"
        "  }\n"
        "  async listen(c) {\n"
        "    if (!this.listening) {\n"
        "      await c.query('LISTEN x');\n"
        "      this.listening = true;\n"
        "    }\n"
        "  }\n"
        "}\n"
    )
    assert _lines(_scan(tmp_path, {"a.ts": src}, "async-check-then-act")) == [10]


def test_deliberate_promise_suppression_is_not_reported(tmp_path):
    src = (
        "export function a(p, client) {\n"
        "  client.quit().catch(() => {});\n"
        "  void p.catch(() => {});\n"
        "  // a late rejection from an abandoned wait must not surface\n"
        "  p.catch((): null => null);\n"
        "  p.catch(() => {});\n"
        "}\n"
    )
    found = _scan(tmp_path, {"a.ts": src}, "promise-catch-noop")
    assert _lines(found) == [6]
    assert {f.confidence for f in found} == {"medium"}


def test_eslint_no_empty_or_a_comment_before_the_try_marks_a_deliberate_catch(tmp_path):
    src = (
        "export function a(paths) {\n"
        "  for (const p of paths) {\n"
        "    try { return load(p);\n"
        "      // eslint-disable-next-line no-empty\n"
        "    } catch (e) {}\n"
        "  }\n"
        "  for (const p of paths) {\n"
        "    try { load(p); } catch (e) {}\n"
        "  }\n"
        "  // keep the original error; this is only a fallback\n"
        "  try { x(); } catch {}\n"
        "  try { x(); } catch {}\n"
        "}\n"
    )
    found = _scan(tmp_path, {"a.ts": src})
    got = sorted((f.rule_id.rsplit(".", 1)[1], f.location.start_line) for f in found
                 if "empty-catch" in f.rule_id)
    assert got == [("empty-catch", 12), ("empty-catch-in-loop", 8)]


def test_assertion_helpers_and_conditional_skips_are_not_reported(tmp_path):
    # got test/progress.ts: `checkEvents(t, events)` asserts in a helper;
    # test/zstd.ts: `test.skip` in the else branch of a feature check.
    src = (
        "test('progress', withServer, async (t, server) => { await run(); checkEvents(t, []); });\n"
        "test('progress 2', withServer, async (t, server) => { await run(); checkout(t); });\n"
        "if (supported) { test('z', () => { expect(1).toBe(1); }); } else { test.skip('needs zstd', () => {}); }\n"
        "test.skip('flaky upstream', () => {});\n"
    )
    found = _scan(tmp_path, {"a.test.ts": src})
    got = sorted((f.rule_id.rsplit(".", 1)[1], f.location.start_line) for f in found
                 if f.rule_id.startswith("arbiter/typescript."))
    assert got == [("skipped-test", 4), ("test-without-assertion", 2)]


# ---------------------------------------------------------------- the corpus

def test_the_corpus_exercises_every_rule_in_the_pack():
    from arbiter import planted
    bad = ROOT / "fixtures" / "planted" / "typescript" / "bad"
    marked = {e.rule.split(".", 1)[1] for e in planted.markers(bad) if e.rule.startswith("arbiter/typescript.")}
    assert marked == set(RULES), {"unexercised": set(RULES) - marked, "unknown": marked - set(RULES)}
