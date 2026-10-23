"""The gold-standard benchmark harness (ARB-065): pins, line counts, per-rule
rates, budgets, report shape and recall scoring. No network and no gold
repository: everything runs on small fixtures built here."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys

import pytest
from helpers import ROOT

_spec = importlib.util.spec_from_file_location("gold_benchmark", ROOT / "tools" / "gold_benchmark.py")
gb = importlib.util.module_from_spec(_spec)
sys.modules["gold_benchmark"] = gb  # dataclasses and worker pickling look it up
_spec.loader.exec_module(gb)

GOLD = ROOT / "tools" / "gold"
LANGS = ("python", "typescript", "go", "java", "ruby")


def _pins():
    return gb.parse_pins((ROOT / "tools" / "fetch_corpus.sh").read_text(encoding="utf-8"))


# --- the data files agree with the fetch script --------------------------------

def test_every_language_has_three_to_five_pinned_gold_repositories():
    benches = set(json.loads((GOLD / "benchmarks.json").read_text()))
    gold = [p for p in _pins() if p.name not in benches]
    for lang in LANGS:
        n = sum(p.language == lang for p in gold)
        assert 3 <= n <= 5, f"{lang}: {n} gold repositories"
    assert len({p.name for p in gold}) == len(gold), "duplicate names"
    assert all(len(p.sha) == 40 for p in gold), "every pin is a full commit SHA"


def test_evidence_and_benchmarks_name_exactly_the_pinned_repositories():
    pins = {p.name: p for p in _pins()}
    benches = json.loads((GOLD / "benchmarks.json").read_text())
    evidence = json.loads((GOLD / "repos.json").read_text())["repos"]
    assert set(evidence) == set(pins) - set(benches)
    for name, ev in evidence.items():
        assert ev["pinned_sha"] == pins[name].sha, f"{name}: evidence and pin disagree"
        assert "scorecard" in ev and "exercises" in ev
    for name, cfg in benches.items():
        assert name in pins, f"benchmark {name} is not pinned in fetch_corpus.sh"
        assert cfg["license"] and cfg["expected_results"] and cfg["test_dir"]


def test_budget_file_is_well_formed():
    b = json.loads((GOLD / "budgets.json").read_text())
    assert float(b["default_per_kloc"]) >= 0
    for section in ("rules", "prefixes"):
        for rule, entry in b.get(section, {}).items():
            assert float(entry["per_kloc"]) >= 0, rule
            assert entry.get("reason"), f"{rule}: a budget needs a reason"


def test_parse_pins_ignores_everything_but_full_pin_lines():
    text = ("get foo https://x/foo.git\n"
            "get_pinned bar https://x/bar.git " + "a" * 40 + " go\n"
            "# get_pinned commented https://x " + "b" * 40 + " go\n"
            "get_pinned short https://x/s.git abc123 go\n")
    assert gb.parse_pins(text) == [gb.Pin("bar", "https://x/bar.git", "a" * 40, "go")]


# --- counting lines -------------------------------------------------------------

def test_count_lines_uses_the_scanners_walk_and_test_split(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n\n\ny = 2\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_x():\n    assert 1\n")
    (tmp_path / "web.js").write_text("let a\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("a\nb\nc\n")
    (tmp_path / "README.md").write_text("words\n")
    lines = gb.count_lines(tmp_path)
    assert lines == {"python": {"prod": 2, "test": 2}, "typescript": {"prod": 1, "test": 0}}


# --- aggregation and budgets ----------------------------------------------------

def _repo(name, lines, findings):
    return {"name": name, "head": "", "lines": lines, "findings": findings, "seconds": 0}


def _f(rule, path, line=1, cwe=()):
    return {"rule": rule, "path": path, "line": line, "cwe": list(cwe)}


def test_pack_rules_divide_by_their_own_language_and_shared_rules_by_all():
    repos = [
        _repo("go-a", {"go": {"prod": 1500, "test": 500}},
              [_f("arbiter/go.http-client-no-timeout", "x.go"),
               _f("arbiter/go.http-client-no-timeout", "x_test.go"),
               _f("arbiter/authored.undeclared-import", "y.go")]),
        _repo("java-b", {"java": {"prod": 8000, "test": 0}},
              [_f("arbiter/authored.undeclared-import", "Y.java")]),
    ]
    rules = {r["rule"]: r for r in gb.aggregate(repos)["rules"]}
    go = rules["arbiter/go.http-client-no-timeout"]
    assert go["pack"] == "go" and go["kloc_denominator"] == 2.0 and go["per_kloc"] == 1.0
    assert go["in_tests"] == 1 and go["by_language"] == {"go": 2}
    shared = rules["arbiter/authored.undeclared-import"]
    assert shared["pack"] == "shared" and shared["kloc_denominator"] == 10.0
    assert shared["per_kloc"] == 0.2 and shared["repos_hit"] == 2
    # sorted loudest first
    assert gb.aggregate(repos)["rules"][0]["rule"] == "arbiter/go.http-client-no-timeout"


def test_samples_are_spread_across_repositories():
    many = [_f("arbiter/python.x", f"a{i}.py", i) for i in range(50)]
    repos = [_repo("noisy", {"python": {"prod": 1000, "test": 0}}, many),
             _repo("quiet", {"python": {"prod": 1000, "test": 0}}, [_f("arbiter/python.x", "q.py")])]
    samples = gb.aggregate(repos, samples_per_repo=3)["rules"][0]["samples"]
    assert sum(s.startswith("noisy:") for s in samples) == 3
    assert "quiet:q.py:1" in samples


def test_budget_lookup_prefers_exact_then_longest_prefix_then_default():
    b = {"default_per_kloc": 0.1,
         "prefixes": {"arbiter/ast.": {"per_kloc": 2.0, "reason": "metric"},
                      "arbiter/ast.high-": {"per_kloc": 3.0, "reason": "metric"}},
         "rules": {"arbiter/ast.high-complexity": {"per_kloc": 5.0, "reason": "metric"}}}
    assert gb.budget_for("arbiter/ast.high-complexity", b) == 5.0
    assert gb.budget_for("arbiter/ast.high-fanout", b) == 3.0
    assert gb.budget_for("arbiter/ast.deep-nesting", b) == 2.0
    assert gb.budget_for("arbiter/go.sql-string-built", b) == 0.1


def test_evaluate_flags_only_rules_over_budget_with_enough_findings():
    b = {"default_per_kloc": 0.1, "min_count": 3}
    rules = [
        {"rule": "over", "per_kloc": 0.5, "count": 10},
        {"rule": "at", "per_kloc": 0.1, "count": 10},          # at budget is within it
        {"rule": "anecdote", "per_kloc": 0.9, "count": 2},     # too few to be a rate
        {"rule": "none", "per_kloc": None, "count": 1},
    ]
    breaches = gb.evaluate(rules, b)
    assert [x["rule"] for x in breaches] == ["over"]
    assert breaches[0] == {"rule": "over", "per_kloc": 0.5, "budget": 0.1, "count": 10}
    assert [r["over_budget"] for r in rules] == [True, False, False, False]


def test_an_accepted_breach_stays_reported_and_fails_again_past_its_ceiling():
    b = {"default_per_kloc": 0.1, "min_count": 1, "accepted_breaches": {
        "held": {"ceiling_per_kloc": 0.2, "reason": "decided"},
        "risen": {"ceiling_per_kloc": 0.2, "reason": "decided"}}}
    rules = [{"rule": "held", "per_kloc": 0.15, "count": 5},
             {"rule": "risen", "per_kloc": 0.25, "count": 5},
             {"rule": "plain", "per_kloc": 0.5, "count": 5}]
    breaches = gb.evaluate(rules, b)
    assert [x["rule"] for x in breaches] == ["held", "risen", "plain"], "an accepted breach is still a breach"
    assert [x.get("accepted") for x in breaches] == [True, False, None]
    assert [x["rule"] for x in gb.failing_breaches(breaches)] == ["risen", "plain"]


def test_every_accepted_breach_names_a_reason_a_date_and_a_ceiling_above_its_budget():
    b = json.loads((GOLD / "budgets.json").read_text())
    for rule, entry in b.get("accepted_breaches", {}).items():
        if rule.startswith("_"):
            continue
        assert entry.get("reason") and entry.get("decided"), rule
        assert float(entry["ceiling_per_kloc"]) > gb.budget_for(rule, b), rule


def test_confidence_is_reported_weighted_but_the_budget_reads_the_raw_rate():
    """Lowering a rule's confidence (java.test-without-assertion) shows in the
    weighted column; it does not by itself bring the rule under budget."""
    repos = [_repo("java-a", {"java": {"prod": 1000, "test": 0}},
                   [{**_f("arbiter/java.low", f"T{i}.java"), "confidence": "low"} for i in range(10)]
                   + [{**_f("arbiter/java.med", f"M{i}.java"), "confidence": "medium"} for i in range(10)]
                   + [_f("arbiter/java.unrecorded", f"U{i}.java") for i in range(10)])]
    rules = {r["rule"]: r for r in gb.aggregate(repos)["rules"]}
    low = rules["arbiter/java.low"]
    assert low["per_kloc"] == 10.0 and low["weighted_per_kloc"] == 5.0
    assert low["confidence"] == {"low": 10}
    assert rules["arbiter/java.med"]["weighted_per_kloc"] == 10.0
    assert rules["arbiter/java.unrecorded"]["weighted_per_kloc"] == 10.0
    breaches = gb.evaluate(list(rules.values()), {"default_per_kloc": 6.0, "min_count": 1})
    assert sorted(b["rule"] for b in breaches) == [
        "arbiter/java.low", "arbiter/java.med", "arbiter/java.unrecorded"]
    assert all("weighted_per_kloc" not in b for b in breaches)


def test_confidence_weight_follows_the_scorecard_factor():
    from arbiter.core import CONFIDENCE_FACTOR
    assert gb.confidence_weight({"confidence": "low"}) == pytest.approx(
        CONFIDENCE_FACTOR["low"] / CONFIDENCE_FACTOR["medium"])
    assert gb.confidence_weight({"confidence": "medium"}) == 1.0
    assert gb.confidence_weight({"confidence": "high"}) == 1.0
    assert gb.confidence_weight({}) == 1.0


def test_report_shape_and_markdown():
    pins = {"go-a": gb.Pin("go-a", "https://x/a.git", "a" * 40, "go")}
    repos = [{**_repo("go-a", {"go": {"prod": 1000, "test": 0}},
                      [_f("arbiter/go.retry-without-backoff", "r.go")] * 4), "head": "a" * 40}]
    rep = gb.build_report(repos, pins, {"default_per_kloc": 1.0, "min_count": 1},
                          ["go-b"], {"arbiter_commit": "abc"})
    assert rep["schema_version"] == gb.SCHEMA_VERSION and rep["kind"] == "gold-precision"
    assert set(rep) >= {"totals", "lines", "missing", "repos", "rules", "breaches", "arbiter_commit"}
    assert rep["totals"] == {"repos": 1, "kloc": 1.0, "findings": 4, "per_kloc": 4.0,
                             "rules_fired": 1, "rules_over_budget": 1,
                             "rules_over_budget_accepted": 0}
    row = rep["repos"][0]
    assert row["at_pin"] is True and row["pinned_sha"] == "a" * 40 and row["per_kloc"] == 4.0
    assert rep["rules"][0]["budget_per_kloc"] == 1.0 and rep["rules"][0]["over_budget"]
    md = gb.render_markdown(rep)
    assert "`arbiter/go.retry-without-backoff`" in md and "go-b" in md and "1 over budget" in md
    json.dumps(rep)  # serialisable


# --- recall ---------------------------------------------------------------------

def test_recall_scores_per_cwe_with_aliases(tmp_path):
    csv = tmp_path / "expected.csv"
    csv.write_text("# test name, category, real vulnerability, cwe, Benchmark version: 1.2\n"
                   "BenchmarkTest00001,sqli,true,89\n"
                   "BenchmarkTest00002,sqli,true,89\n"
                   "BenchmarkTest00003,sqli,false,89\n"
                   "BenchmarkTest00004,weakrand,true,330\n"
                   "BenchmarkTest00005,weakrand,false,330\n")
    expected = gb.read_expected(csv)
    assert expected["BenchmarkTest00001"] == {"category": "sqli", "real": True, "cwe": "CWE-89"}
    td = "src/testcode"
    findings = [
        _f("arbiter/java.sql-string-built", f"{td}/BenchmarkTest00001.java", cwe=["CWE-89"]),
        _f("arbiter/java.sql-string-built", f"{td}/BenchmarkTest00003.java", cwe=["CWE-89"]),
        # caught through the alias: the rule says 338, the benchmark says 330
        _f("arbiter/java.weak-random-for-secret", f"{td}/BenchmarkTest00004.java", cwe=["CWE-338"]),
        # the right CWE in the wrong file does not count
        _f("arbiter/java.sql-string-built", "src/helpers/BenchmarkTest00002.java", cwe=["CWE-89"]),
        # a different weakness in a test file is reported, not credited
        _f("arbiter/java.swallowed-exception", f"{td}/BenchmarkTest00002.java", cwe=["CWE-703"]),
    ]
    rep = gb.score_recall(expected, findings, {"CWE-330": ["CWE-338"]}, td)
    rows = {r["cwe"]: r for r in rep["per_cwe"]}
    assert [r["cwe"] for r in rep["per_cwe"]] == ["CWE-89", "CWE-330"]
    sql = rows["CWE-89"]
    assert (sql["tp"], sql["fn"], sql["fp"], sql["tn"]) == (1, 1, 1, 0)
    assert sql["recall"] == 0.5 and sql["false_positive_rate"] == 1.0 and sql["score"] == -0.5
    rnd = rows["CWE-330"]
    assert (rnd["tp"], rnd["fn"], rnd["fp"], rnd["tn"]) == (1, 0, 0, 1)
    assert rnd["recall"] == 1.0 and rnd["caught_examples"] == ["BenchmarkTest00004"]
    assert rep["overall"]["real"] == 3 and rep["overall"]["tp"] == 2
    assert rep["other_cwes_in_test_files"] == {"CWE-703": 1}
    assert "in_scope" not in rep  # nothing claimed was passed
    scoped = gb.score_recall(expected, findings, {"CWE-330": ["CWE-338"]}, td, {"CWE-338"})
    assert [r["in_scope"] for r in scoped["per_cwe"]] == [False, True]
    assert scoped["in_scope"]["real"] == 1 and scoped["in_scope"]["recall"] == 1.0
    assert "| CWE-89 | sqli | ? | 2 | 1 | 0.5 |" in gb.render_recall_markdown(
        "x", {**rep, "source": {"url": "u", "sha": "a" * 40, "license": "GPL-2.0"}})


def test_claimed_cwes_come_from_the_pack_and_the_native_table():
    java = gb.claimed_cwes("java")
    assert {"CWE-89", "CWE-78", "CWE-338", "CWE-798"} <= java
    assert "CWE-643" not in java  # XPath injection: no Java rule claims it


# --- end to end on a tiny pinned repository -------------------------------------

def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(cwd), "PATH": "/usr/bin:/bin"})


@pytest.mark.parametrize("pinned", [True, False])
def test_main_scans_only_repositories_at_their_pin(tmp_path, pinned):
    corpus = tmp_path / "corpus"
    repo = corpus / "py-tiny"
    repo.mkdir(parents=True)
    (repo / "client.py").write_text(
        "import requests\n\n\ndef fetch(url):\n    return requests.get(url)\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "x")
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    sha = head if pinned else "0" * 40
    script = tmp_path / "fetch.sh"
    script.write_text(f"get_pinned py-tiny https://example.invalid/t.git {sha} python\n")
    budgets = tmp_path / "budgets.json"
    budgets.write_text(json.dumps({"default_per_kloc": 0.0, "min_count": 1}))
    benches = tmp_path / "benchmarks.json"
    benches.write_text("{}")
    out = tmp_path / "out"
    code = gb.main(["--corpus", str(corpus), "--out", str(out), "--budgets", str(budgets),
                    "--benchmarks", str(benches), "--fetch-script", str(script), "--jobs", "1"])
    rep = json.loads((out / "gold-report.json").read_text())
    assert (out / "gold-summary.md").is_file()
    if pinned:
        assert rep["missing"] == [] and rep["repos"][0]["at_pin"] is True
        assert rep["repos"][0]["lines"] == {"python": {"prod": 3, "test": 0}}
        assert code == (1 if rep["breaches"] else 0)
    else:
        assert rep["repos"] == [] and "py-tiny" in rep["missing"][0]
        assert code == 2
