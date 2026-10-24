"""Each pack query is compiled once per process, however many files (ARB-076).

`run_query` used to build `Query(lang, src)` on every call, so a scan paid
files x rules compilations. The compiled query is now cached by grammar and
query text; these tests count real tree-sitter compilations during a scan.
"""
from __future__ import annotations

from collections import Counter

import pytest

from arbiter import ast as ts
from arbiter import lang_rules
from arbiter.engine import run_scan
from arbiter.policy import load_config

pytestmark = pytest.mark.skipif(not ts.available(), reason="tree-sitter not installed")

_RUBY = '''\
class Account{n}
  def lookup(id)
    User.where("id = #{{id}}")
  end

  def risky
    yield
  rescue Exception
    nil
  end
end
'''


def _count_compiles(monkeypatch) -> Counter:
    import tree_sitter as _ts
    compiled: Counter = Counter()
    real = _ts.Query

    def counting(lang, src):
        compiled[src] += 1
        return real(lang, src)

    monkeypatch.setattr(_ts, "Query", counting)
    ts._compiled.cache_clear()
    return compiled


@pytest.mark.parametrize("files", [1, 8])
def test_a_pack_query_is_compiled_once_per_process_for_n_files(tmp_path, monkeypatch, files):
    if "ruby" not in ts.cached_grammars():
        pytest.skip("ruby grammar not cached")
    import tree_sitter as _ts
    if not (hasattr(_ts, "Query") and not hasattr(ts._language("ruby"), "query")):
        pytest.skip("older tree-sitter API: Language.query, not Query(lang, src)")
    for n in range(files):
        (tmp_path / f"account{n}.rb").write_text(_RUBY.format(n=n))
    compiled = _count_compiles(monkeypatch)
    report = run_scan([str(tmp_path)], load_config(None), only=["lang_ruby"],
                      use_adapters=False, use_cache=False)
    rules = {f.rule_id for f in report.active()}
    assert "arbiter/ruby.rescue-exception" in rules, "the pack really ran on these files"
    hits = [f for f in report.active() if f.rule_id == "arbiter/ruby.rescue-exception"]
    assert len({f.location.path for f in hits}) == files, "every file was scanned"

    assert compiled, "the scan compiled its queries"
    assert max(compiled.values()) == 1, {q[:60]: c for q, c in compiled.items() if c > 1}
    rules_ = lang_rules.load_packs()["ruby"]["rules"]
    pack_queries = {r[k] for r in rules_ for k in lang_rules.QUERY_KEYS if r.get(k)}
    pack_queries |= {r["taint"][k] for r in rules_ if r.get("taint")
                     for k in lang_rules.TAINT_QUERY_KEYS if r["taint"].get(k)}
    assert set(compiled) <= pack_queries, "only pack queries were compiled"


def test_a_query_is_cached_per_grammar_not_per_language_name(monkeypatch):
    needed = {"typescript", "tsx"}
    if not needed <= ts.cached_grammars():
        pytest.skip("typescript and tsx grammars not cached")
    compiled = _count_compiles(monkeypatch)
    q = "(identifier) @hit"
    for _ in range(3):
        ts.compile_query("typescript", q)
        ts.compile_query("tsx", q)
    assert compiled[q] == 2, "one compile for each grammar, reused after that"


def test_a_query_that_does_not_compile_is_not_cached_as_good(monkeypatch):
    if "go" not in ts.cached_grammars():
        pytest.skip("go grammar not cached")
    compiled = _count_compiles(monkeypatch)
    for _ in range(2):
        with pytest.raises(ValueError):
            ts.compile_query("go", "(no_such_node_type) @hit")
    assert compiled["(no_such_node_type) @hit"] == 2, "a failure is retried, never cached"
