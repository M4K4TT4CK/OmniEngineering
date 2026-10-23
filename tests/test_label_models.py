"""Model labelling and ground truth (ARB-067): no network, no model calls."""
from __future__ import annotations

import importlib.util
import json
import sys

import pytest
from helpers import ROOT


def _load(name: str, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(ROOT / "tools" / "ground_truth"))
g = _load("gt_common", ROOT / "tools" / "ground_truth" / "gt_common.py")
lm = _load("label_models", ROOT / "tools" / "label_models.py")
bgt = _load("benchmarks_gt", ROOT / "tools" / "ground_truth" / "benchmarks_gt.py")
cve = _load("cve_pairs", ROOT / "tools" / "ground_truth" / "cve_pairs.py")
hs = _load("heldout_split", ROOT / "tools" / "ground_truth" / "heldout_split.py")

PY = '''import os
import subprocess

API_KEY = "sk_live_abcdefghijklmnop1234"


def helper(x):
    return x.strip()


def run(request):
    name = helper(request.args["n"])
    # FLAW: shell
    subprocess.call("ls " + name, shell=True)
    return name
'''


# --- excerpts ----------------------------------------------------------------

def test_excerpt_marks_line_shows_imports_helper_and_masks(tmp_path):
    p = tmp_path / "app.py"
    p.write_text(PY)
    ex = g.excerpt(PY, str(p), 14)
    assert ">>   14" in ex
    assert "import subprocess" in ex
    assert "def helper(x):" in ex          # a same-file function the enclosing one calls
    assert "def run(request):" in ex


def test_excerpt_masks_secrets():
    src = 'def f():\n    API_KEY = "sk_live_abcdefghijklmnop1234"\n    return API_KEY\n'
    ex = g.excerpt(src, "a.py", 2)
    assert "sk_live_abcdefghijklmnop1234" not in ex and "[redacted]" in ex


def test_excerpt_without_a_function_is_a_window():
    text = "\n".join(f"line{i}" for i in range(1, 101))
    ex = g.excerpt(text, "notes.md", 50)
    assert ">>   50 line50" in ex and "line19" not in ex and "line20" in ex


def test_blind_removes_comments_and_juliet_names_keeps_lines(tmp_path):
    src = ("public class CWE89_SQL_Injection__x_01 extends AbstractTestCase {\n"
           "    /* FLAW: concatenated */\n"
           "    public void bad() { sink(data); } // POTENTIAL FLAW\n"
           "    private void goodG2B() { }\n}\n")
    p = tmp_path / "CWE89_SQL_Injection__x_01.java"
    out = g.blind(src, str(p), "juliet")
    assert out.count("\n") == src.count("\n")
    assert "FLAW" not in out and "bad" not in out and "good" not in out and "CWE89" not in out
    assert g.blind_path("Java/src/testcases/CWE89_SQL_Injection/s01/CWE89_x_01.java", "juliet") \
        == "src/main/java/app/Case.java"
    assert "Benchmark" not in g.blind("class BenchmarkTest00012 {}", "BenchmarkTest00012.java", "owasp")


def test_label_record_rejects_unknown_verdict():
    with pytest.raises(ValueError):
        g.label({"rule": "r"}, "maybe", labeller="x", source="s", held_out=False, reason="")
    rec = g.label({"id": "f:1", "rule": "r", "path": "a.py", "line": 3}, "tp", labeller="x",
                  source="s", held_out=True, reason="why", excerpt_sha256="h")
    assert {"finding_id", "rule_id", "label", "labeller", "source", "held_out", "reason",
            "excerpt_sha256"} <= set(rec)


# --- ground truth --------------------------------------------------------------

def test_owasp_labels_by_case_truth_and_cwe():
    expected = {"BenchmarkTest00001": {"category": "sqli", "real": True, "cwe": "CWE-89"},
                "BenchmarkTest00002": {"category": "sqli", "real": False, "cwe": "CWE-89"}}
    findings = [
        {"id": "a", "rule": "r.sql", "path": "t/BenchmarkTest00001.java", "line": 5, "cwe": ["CWE-89"]},
        {"id": "b", "rule": "r.sql", "path": "t/BenchmarkTest00002.java", "line": 5, "cwe": ["CWE-564"]},
        {"id": "c", "rule": "r.xss", "path": "t/BenchmarkTest00002.java", "line": 9, "cwe": ["CWE-79"]},
        {"id": "d", "rule": "r.sql", "path": "helpers/Db.java", "line": 1, "cwe": ["CWE-89"]},
    ]
    out = bgt.owasp_labels(findings, expected, {"CWE-89": ["CWE-564"]}, "t", "owasp-benchmark-java")
    assert [(r["finding_id"], r["label"]) for r in out] == [("a", "tp"), ("b", "fp")]
    assert all(r["held_out"] and r["labeller"] == "ground-truth" for r in out)


def test_juliet_side_from_method_and_class_names(tmp_path):
    src = ("public class X {\n"
           "    public void bad() throws Throwable\n    {\n        run(a);\n    }\n"
           "    private void goodG2B() throws Throwable\n    {\n        run(b);\n    }\n}\n")
    p = tmp_path / "CWE78_x_01.java"
    p.write_text(src)
    assert bgt.juliet_side(src, str(p), 4) == "bad"
    assert bgt.juliet_side(src, str(p), 8) == "good"
    q = tmp_path / "CWE78_x_81_bad.java"
    other = "public class Y {\n    public void action(String d) throws Throwable\n    {\n        run(d);\n    }\n}\n"
    q.write_text(other)
    assert bgt.juliet_side(other, str(q), 4) == "bad"


def test_cve_classify_tp_persists_and_other_cwe():
    old = ["a", "q = 'x' + v", "b", "os.system(c)", "z"]
    new = ["a", "q = bind(v)", "b", "os.system(c2)", "z"]
    pair = {"cwes": ["CWE-89"]}
    vuln = [{"rule": "sql", "path": "f.py", "line": 2, "cwe": ["CWE-89"]},
            {"rule": "cmd", "path": "f.py", "line": 4, "cwe": ["CWE-78"]},
            {"rule": "sql", "path": "f.py", "line": 5, "cwe": ["CWE-89"]}]   # unchanged line: no label
    fix = [{"rule": "cmd", "path": "f.py", "line": 4, "cwe": ["CWE-78"]}]
    got = [(f["line"], o) for f, o in cve.classify(pair, vuln, fix, {"f.py": (old, new)})]
    assert got == [(2, "cve-fix"), (4, "cve-fix-persists")]
    vuln2 = [{"rule": "cmd", "path": "f.py", "line": 4, "cwe": ["CWE-78"]}]
    assert [o for _f, o in cve.classify(pair, vuln2, [], {"f.py": (old, new)})] == ["cve-fix-other-cwe"]


def test_heldout_split_is_seeded_stratified_and_30_percent():
    gb = _load("gold_benchmark", ROOT / "tools" / "gold_benchmark.py")
    pins = [gb.Pin(f"{lang}-{i}", "u", "0" * 40, lang) for lang, n in
            [("python", 5), ("go", 5), ("java", 5), ("ruby", 4), ("typescript", 5)] for i in range(n)]
    a = hs.split(pins, set())
    assert a == hs.split(pins, set())
    assert len(a["gold_repos"]) == round(24 * 0.3)
    assert all(len(v) >= 1 for v in a["per_language"].values())
    committed = json.loads((ROOT / "tools" / "gold" / "heldout.json").read_text())
    assert committed["seed"] == hs.SEED and len(committed["held_out"]["gold_repos"]) == 7


# --- claims and prompts ----------------------------------------------------------

def test_every_pack_rule_has_a_neutral_claim():
    from arbiter.lang_rules import load_packs
    missing = [f"arbiter/{lang}.{r['id']}" for lang, p in load_packs().items() for r in p["rules"]
               if lm.claim_for(f"arbiter/{lang}.{r['id']}") is None]
    assert missing == []


def test_prompt_never_carries_arbiter_wording(tmp_path):
    """Independence: the labeller sees the claim, path and excerpt only."""
    from arbiter.lang_rules import load_packs
    rule = next(r for r in load_packs()["python"]["rules"] if r["id"] == "command-injection")
    p = tmp_path / "app.py"
    p.write_text(PY)
    f = {"id": "f:x", "rule": "arbiter/python.command-injection", "path": "app.py", "line": 14,
         "title": rule["title"], "cwe": ["CWE-78"], "severity": "high", "confidence": "high"}
    it = lm.make_item(f, tmp_path, "test", False)
    for role in ("prosecutor", "defender"):
        prompt = lm.SYSTEM + lm.role_prompt(it, role)
        for text in (rule["title"], rule["description"], rule["remediation"]):
            assert " ".join(text.split())[:60] not in " ".join(prompt.split())
        assert "high" not in prompt.split("Excerpt:")[0]   # no severity or confidence
    assert "Reviewer A" not in lm.role_prompt(it, "prosecutor")


def test_parse_answer():
    assert lm.parse_answer('x {"label": "TP", "reason": "a  b"} y') == {"label": "tp", "reason": "a b"}
    assert lm.parse_answer('{"label": "maybe"}') is None
    assert lm.parse_answer("no json") is None


def test_secret_shape_describes_without_revealing():
    s = lm.secret_shape('password = "changeme123"')
    assert "changeme123" not in s and "11 characters" in s and "changeme" in s


# --- the labelling loop with a recorded model ------------------------------------

class Fake:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, system, prompt, model):
        role = "adjudicator" if "adjudicator" in prompt else "prosecutor" if "prosecutor" in prompt else "defender"
        self.calls.append(role)
        return json.dumps(self.answers[role]), 0.01, "claude-opus-5-5"


def _item(tmp_path, fid="f:1"):
    p = tmp_path / "app.py"
    p.write_text(PY)
    f = {"id": fid, "rule": "arbiter/python.command-injection", "path": "app.py", "line": 14, "cwe": ["CWE-78"]}
    return lm.make_item(f, tmp_path, "gold-model", False)


def test_agreement_skips_adjudicator_and_cache_makes_rerun_free(tmp_path):
    fake = Fake({"prosecutor": {"label": "tp", "reason": "p"}, "defender": {"label": "tp", "reason": "d"}})
    cache = lm.Cache(tmp_path / "c.jsonl")
    lab = lm.Labeller(cache, lm.Budget(5), caller=fake)
    rec = lab.label(_item(tmp_path))
    assert rec["label"] == "tp" and sorted(fake.calls) == ["defender", "prosecutor"]
    assert "adjudicator" not in rec["votes"] and rec["labeller"].endswith("prosecutor+defender/v1")
    fake2 = Fake({})
    lab2 = lm.Labeller(lm.Cache(tmp_path / "c.jsonl"), lm.Budget(5), caller=fake2)
    assert lab2.label(_item(tmp_path))["label"] == "tp" and fake2.calls == []


def test_disagreement_goes_to_adjudicator_with_both_reasons(tmp_path):
    seen = {}

    def caller(system, prompt, model):
        if "adjudicator" in prompt:
            seen["prompt"] = prompt
            return '{"label": "fp", "reason": "adj"}', 0.02, "m"
        lab = "tp" if "prosecutor" in prompt else "fp"
        return json.dumps({"label": lab, "reason": f"{lab}-why"}), 0.01, "m"
    lab = lm.Labeller(lm.Cache(tmp_path / "c.jsonl"), lm.Budget(5), caller=caller)
    rec = lab.label(_item(tmp_path))
    assert rec["label"] == "fp" and rec["votes"]["adjudicator"]["reason"] == "adj"
    assert "tp-why" in seen["prompt"] and "fp-why" in seen["prompt"]
    assert rec["cost_usd"] == pytest.approx(0.04)


def test_budget_stops_before_a_call_that_could_pass_it(tmp_path):
    fake = Fake({"prosecutor": {"label": "tp", "reason": "p"}, "defender": {"label": "tp", "reason": "d"}})
    budget = lm.Budget(0.06)
    lab = lm.Labeller(lm.Cache(tmp_path / "c.jsonl"), budget, caller=fake)
    assert lab.label(_item(tmp_path, "f:1")) is None   # second call would exceed the cap
    assert budget.stopped and fake.calls == ["prosecutor"]
    assert budget.spent == pytest.approx(0.01)


def test_calibration_scores_against_hidden_truth():
    def r(final, truth, p=None, d=None):
        return {"label": final, "truth": truth, "source": "calibration:x",
                "votes": {"prosecutor": {"label": p or final}, "defender": {"label": d or final}}}
    recs = [r("tp", "tp"), r("tp", "tp"), r("tp", "fp"), r("fp", "fp"), r("unsure", "fp")]
    c = lm.calibration(recs)["final"]
    assert c["tp_call_precision"] == pytest.approx(2 / 3, abs=1e-3)
    assert c["unsure"] == 1 and c["accuracy_decided"] == 0.75
    assert c["false_tp_rate"] == pytest.approx(1 / 3, abs=1e-3)


def test_tally_reports_wilson_lower_bound_and_kappa():
    recs = [{"rule_id": "r", "label": "tp", "votes": {"prosecutor": {"label": "tp"}, "defender": {"label": "tp"}}}] * 30
    t = lm.tally(recs, lambda x: x["rule_id"])["r"]
    assert t["tp"] == 30 and t["precision"] == 1.0 and 0.88 < t["wilson_lower"] < 0.9


def test_usage_limit_stops_the_run_instead_of_failing_every_item(tmp_path):
    calls = []

    def caller(system, prompt, model):
        calls.append(1)
        raise RuntimeError("cli error: You've hit your session limit")
    budget = lm.Budget(5)
    lab = lm.Labeller(lm.Cache(tmp_path / "c.jsonl"), budget, caller=caller)
    assert lab.label(_item(tmp_path, "f:1")) is None
    assert lab.label(_item(tmp_path, "f:2")) is None
    assert budget.stopped and budget.limit_hit and len(calls) == 1
    assert lm.Cache(tmp_path / "c.jsonl").data == {}   # failures are never cached


# --- calibration round 2 ---------------------------------------------------

def test_ground_truth_sample_skips_items_an_earlier_round_used(tmp_path, monkeypatch):
    root = tmp_path / "gt"
    root.mkdir()
    (root / "A.java").write_text("class A {\n  void f() {\n    run(x);\n  }\n}\n", encoding="utf-8")
    recs = [g.label({"id": f"id{i}", "rule": "arbiter/java.command-injection", "path": "A.java", "line": 3},
                    "tp" if i % 2 else "fp", labeller="ground-truth", source="owasp-java", held_out=True,
                    reason="") for i in range(6)]
    g.write_jsonl(tmp_path / "gt.jsonl", recs)
    monkeypatch.setitem(lm.GT_ROOTS, "owasp-java", str(root))
    its = lm.items_from_ground_truth([tmp_path / "gt.jsonl"], 0, 1, exclude={"id0", "id1"})
    assert sorted(it["finding_id"] for it in its) == ["id2", "id3", "id4", "id5"]
    assert all(it["source"] == "calibration:owasp-java" and "truth" in it for it in its)


def test_planted_blind_drops_markers_docstrings_and_cwe_names():
    src = ('"""CWE-798: credentials written into the module."""\nimport os\n\n'
           'PW = "q8Vz!m2Lr7#tKw4P"  # expect: arbiter/secrets.assigned-credential\n')
    out = g.blind(src, "cwe25/cwe798_hardcoded.py", "planted")
    assert "expect" not in out and "CWE" not in out and "written into" not in out
    assert out.count("\n") == src.count("\n") and 'PW = "q8Vz' in out
    assert g.blind_path("src/main/java/planted/cwe25/Cwe918Ssrf.java", "planted") == "src/main/java/app/Ssrf.java"
    assert g.blind_path("cwe25/cwe798_hardcoded.py", "planted") == "hardcoded.py"


def test_twin_line_follows_test_title_then_function_name():
    bad = 'it("drains a batch", async () => {\n  await drain([job]);\n});\n'
    good = 'const x = 1;\nit("drains a batch", async () => {\n  expect(await drain([job])).toBe(0);\n});\n'
    assert lm.twin_line(bad, good, "q.test.ts", 1, "typescript") == 2
    bad_py = "import os\n\ndef verify(sig):\n    return True\n"
    good_py = "import os\nimport hmac\n\ndef verify(sig):\n    return hmac.compare_digest(sig, KEY)\n"
    assert lm.twin_line(bad_py, good_py, "a.py", 4, "python") == 5


def test_fact_truth_sha_pin_lockfile_and_test_path(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("steps:\n  - uses: actions/checkout@v4\n"
                               "  - uses: actions/setup-go@0aaccfd150d50ccaeb58ebd88d36e91967a5f35b\n",
                               encoding="utf-8")
    act = {"rule_id": "arbiter/supply.unpinned-action", "path": ".github/workflows/ci.yml"}
    assert lm.fact_truth({**act, "line": 2}, tmp_path)[0] == "tp"
    assert lm.fact_truth({**act, "line": 3}, tmp_path)[0] == "fp"
    (tmp_path / "package.json").write_text('{"dependencies": {"x": "^1.0.0"}}\n', encoding="utf-8")
    npm = {"rule_id": "arbiter/supply.unpinned-npm-dep", "path": "package.json", "line": 1}
    assert lm.fact_truth(npm, tmp_path) is None          # no lockfile: no fact decides it
    (tmp_path / "package-lock.json").write_text("{}\n", encoding="utf-8")
    assert lm.fact_truth(npm, tmp_path)[0] == "fp"
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conf.py").write_text('PASSWORD = "hunter22"\n', encoding="utf-8")
    sec = {"rule_id": "arbiter/secrets.assigned-credential", "path": "tests/conf.py", "line": 1}
    assert lm.fact_truth(sec, tmp_path)[0] == "fp"
    assert lm.fact_truth({**sec, "path": "app/conf.py"}, tmp_path) is None


def test_server_throttle_is_retried_not_taken_for_a_usage_limit(tmp_path):
    calls = []

    def caller(system, prompt, model):
        calls.append(1)
        if len(calls) <= 2:
            raise RuntimeError("cli error: API Error: Server is temporarily limiting requests "
                               "(not your usage limit) · Rate limited. Retry after 0.5s")
        return '{"label": "tp", "reason": "x"}', 0.01, "claude-opus-5-5"
    budget = lm.Budget(5)
    lab = lm.Labeller(lm.Cache(tmp_path / "c.jsonl"), budget, caller=caller, sleep=lambda s: None)
    rec = lab.ask(_item(tmp_path, "f:1"), "prosecutor")
    assert rec["label"] == "tp" and len(calls) == 3
    assert not budget.stopped and not budget.limit_hit
