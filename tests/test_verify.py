"""The model verification pass (verify.py), with recorded replies and no network."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from arbiter import verify
from arbiter.cache import ResultCache
from arbiter.core import Finding, Location, Report
from arbiter.engine import run_scan
from arbiter.inventory import FileInfo, Inventory
from arbiter.judgement import Provider, Unavailable
from arbiter.policy import load_config
from arbiter.report import render_console, render_markdown

APP = '''import subprocess
from flask import Flask, request

app = Flask(__name__)
DB_PASSWORD = "hunter2-very-secret"


@app.route("/run")
def run():
    cmd = request.args.get("cmd")
    return subprocess.check_output(cmd, shell=True)
'''
OTHER = "SENTINEL_FROM_ANOTHER_FILE = 1\n"


@dataclass
class FakeProvider(Provider):
    """Replies in order (the last one repeats); records every prompt."""
    name: str = "fake"
    model: str = "claude-opus-5-5"
    replies: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    usage: tuple = (100, 20)
    stop: str = ""
    fail: bool = False

    def available(self):
        return True, ""

    def ask(self, system, user):
        self.calls.append((system, user))
        if self.fail:
            raise Unavailable("HTTP 529: overloaded")
        text = self.replies[min(len(self.calls), len(self.replies)) - 1] if self.replies else ""
        return verify.Reply(text=text, input_tokens=self.usage[0], output_tokens=self.usage[1],
                            stop_reason=self.stop)


def _reply(verdict, reason="because", line=11):
    return json.dumps({"verdict": verdict, "reason": reason, "line": line})


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "app.py").write_text(APP)
    (tmp_path / "other.py").write_text(OTHER)
    inv = Inventory()
    for name in ("app.py", "other.py"):
        fi = FileInfo(path=name, abspath=str(tmp_path / name), repo_id="root", language="python",
                      lines=len((tmp_path / name).read_text().splitlines()))
        inv.files.append(fi)
        inv.by_repo.setdefault("root", []).append(fi)
    return tmp_path, inv


def _finding(line=11, severity="high", rule="arbiter/python.command-injection", **kw):
    return Finding(rule_id=rule, title="Shell command built from request input", severity=severity,
                   location=Location(path="app.py", start_line=line), evidence=f"ev{line}{rule}",
                   description="A request value reaches a shell.", remediation="Pass an argv list.",
                   **kw)


def _cfg(**over):
    return {"verify": {**verify.DEFAULTS, **over}}


@pytest.mark.parametrize("raw, want", [
    (_reply("confirmed"), "confirmed"),
    (_reply("rejected", "cmd is a constant"), "rejected"),
    (_reply("unsure"), "unsure"),
    ("I think this is fine.", "unsure"),
    ('{"verdict": "probably", "reason": "x"}', "unsure"),
    ('{"verdict": "confirmed", "reason": ', "unsure"),
])
def test_recorded_replies(tree, raw, want):
    _root, inv = tree
    f = _finding()
    fake = FakeProvider(replies=[raw])
    summary = verify.run([f], inv, _cfg(), provider=fake)
    assert f.verification["verdict"] == want
    assert f.verification["model"] == "claude-opus-5-5"
    assert f.verification["prompt_version"] == verify.PROMPT_VERSION
    assert f.verification["cached"] is False
    assert summary[want] == 1 and summary["calls"] == 1
    if raw.startswith("{\"verdict\": \"confirmed\", \"reason\": \"because\""):
        assert f.verification["line"] == 11


def test_line_outside_excerpt_is_dropped(tree):
    _root, inv = tree
    f = _finding()
    verify.run([f], inv, _cfg(), provider=FakeProvider(replies=[_reply("confirmed", line=9999)]))
    assert f.verification["verdict"] == "confirmed"
    assert f.verification["line"] is None


def test_refusal_is_unsure_and_not_cached(tree):
    _root, inv = tree
    cache = ResultCache()
    f = _finding()
    verify.run([f], inv, _cfg(), provider=FakeProvider(replies=[""], stop="refusal"), cache=cache)
    assert f.verification["verdict"] == "unsure"
    assert not cache.entries.get(verify.CACHE_BUCKET)


def test_no_provider_is_not_verified_never_a_pass(tree, monkeypatch):
    _root, inv = tree
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fs = [_finding(11), _finding(5, severity="medium", rule="arbiter/secrets.assigned-credential")]
    summary = verify.run(fs, inv, _cfg())
    assert {f.verification["verdict"] for f in fs} == {"not_verified"}
    assert all("ANTHROPIC_API_KEY" in f.verification["reason"] for f in fs)
    assert summary["not_verified"] == 2 and summary["calls"] == 0
    assert "ANTHROPIC_API_KEY" in summary["reason"]


def test_cache_hit_makes_zero_calls(tree):
    root, inv = tree
    path = root / ".arbiter" / "verify-cache.json"
    first = FakeProvider(replies=[_reply("rejected", "constant")])
    f1 = _finding()
    verify.run([f1], inv, _cfg(cache_path=str(path)), provider=first)
    assert len(first.calls) == 1 and path.is_file()

    second = FakeProvider(replies=[_reply("confirmed")])
    f2 = _finding()
    summary = verify.run([f2], inv, _cfg(cache_path=str(path)), provider=second)
    assert second.calls == []
    assert summary["cache_hits"] == 1 and summary["calls"] == 0
    assert f2.verification["verdict"] == "rejected" and f2.verification["cached"] is True

    # A changed excerpt is a different question.
    (root / "app.py").write_text(APP.replace('"cmd"', '"command"'))
    from arbiter.probes import clear_read_cache
    clear_read_cache()
    third = FakeProvider(replies=[_reply("confirmed")])
    verify.run([_finding()], inv, _cfg(cache_path=str(path)), provider=third)
    assert len(third.calls) == 1


def test_cache_key_covers_model_and_prompt_version(tree, monkeypatch):
    _root, inv = tree
    f = _finding()
    ex = verify.excerpt_for(f, inv, 120)
    k = verify.cache_key(f, ex, "claude-opus-5-5")
    assert k != verify.cache_key(f, ex, "claude-sonnet-5-5")
    assert k != verify.cache_key(f, ex, "claude-opus-5-5", fp_notes="a new FP note")
    monkeypatch.setattr(verify, "PROMPT_VERSION", "verify-v2")
    assert k != verify.cache_key(f, ex, "claude-opus-5-5")


def test_budget_max_findings(tree):
    _root, inv = tree
    fs = [_finding(11), _finding(10, severity="medium")]
    fake = FakeProvider(replies=[_reply("confirmed")])
    summary = verify.run(fs, inv, _cfg(max_findings=1), provider=fake)
    assert len(fake.calls) == 1
    assert fs[0].verification["verdict"] == "confirmed"      # high goes first
    assert fs[1].verification == {**fs[1].verification, "verdict": "not_verified", "reason": "budget"}
    assert summary["not_verified"] == 1 and summary["reason"] == "budget"


def test_budget_max_tokens(tree):
    _root, inv = tree
    fs = [_finding(11), _finding(10, severity="medium")]
    fake = FakeProvider(replies=[_reply("confirmed")])
    verify.run(fs, inv, _cfg(max_tokens=10), provider=fake)
    assert fake.calls == []
    assert [f.verification["reason"] for f in fs] == ["budget", "budget"]


def test_repeated_provider_errors_stop_calling(tree):
    _root, inv = tree
    fs = [_finding(n, severity="medium", rule=f"arbiter/r{n}") for n in range(1, 8)]
    fake = FakeProvider(fail=True)
    verify.run(fs, inv, _cfg(), provider=fake)
    assert len(fake.calls) == verify.MAX_CONSECUTIVE_ERRORS
    assert {f.verification["verdict"] for f in fs} == {"not_verified"}


def test_prompt_masks_secrets_and_stays_in_one_file(tree):
    _root, inv = tree
    fake = FakeProvider(replies=[_reply("confirmed")])
    verify.run([_finding(5, rule="arbiter/secrets.assigned-credential")], inv, _cfg(), provider=fake)
    system, user = fake.calls[0]
    assert "hunter2-very-secret" not in user and "[redacted]" in user
    assert "SENTINEL_FROM_ANOTHER_FILE" not in user and "SENTINEL" not in system
    assert "other.py" not in user
    assert "untrusted" in system


def test_excerpt_is_enclosing_function_plus_imports():
    text = "\n".join(["import os", "from x import y"] + [f"a{i} = {i}" for i in range(3, 200)])
    ex = verify.build_excerpt(text, 100, 20, span=(95, 105), path="m.py")
    assert ex.start == 95 and ex.end == 105
    assert {1, 2} <= ex.lines and "    1| import os" in ex.text
    assert "a150" not in ex.text
    # no span, or a span longer than the bound: a window of the bound
    ex = verify.build_excerpt(text, 100, 20, span=(3, 199))
    assert ex.end - ex.start + 1 == 20 and ex.start <= 100 <= ex.end


def test_candidates_filter(tree):
    fs = [_finding(1, severity="low"), _finding(2), _finding(3, rule="arbiter/quality.long-function"),
          _finding(4, provenance="inferred"), _finding(6)]
    fs[4].suppressed = True
    got = verify.candidates(fs, {"verify": {**verify.DEFAULTS, "skip_rules": ["arbiter/quality.*"]}})
    assert [f.location.start_line for f in got] == [2]


def test_fp_notes_read_and_absence_tolerated(tmp_path):
    assert verify.load_fp_notes({"verify": {"fp_notes_path": str(tmp_path / "nope.json")}}) == {}
    p = tmp_path / "precision.json"
    p.write_text(json.dumps({"rules": {"arbiter/x": {"fp_notes": ["constant argv", "test fixture"]},
                                       "arbiter/y": {"tp": 3}}}))
    notes = verify.load_fp_notes({"verify": {"fp_notes_path": str(p)}})
    assert notes == {"arbiter/x": "- constant argv\n- test fixture"}
    p.write_text("not json")
    assert verify.load_fp_notes({"verify": {"fp_notes_path": str(p)}}) == {}


def test_fp_notes_reach_the_prompt(tree, tmp_path):
    _root, inv = tree
    p = tmp_path / "precision.json"
    p.write_text(json.dumps({"rules": {"arbiter/python.command-injection":
                                       {"fp_notes": "argv is a literal list"}}}))
    fake = FakeProvider(replies=[_reply("confirmed")])
    verify.run([_finding()], inv, _cfg(fp_notes_path=str(p)), provider=fake)
    assert "argv is a literal list" in fake.calls[0][1]


def test_request_body_fits_opus_5_5(monkeypatch):
    sent = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"content": [{"type": "thinking", "thinking": ""},
                                           {"type": "text", "text": _reply("unsure")}],
                               "usage": {"input_tokens": 50, "cache_read_input_tokens": 7,
                                         "output_tokens": 9},
                               "stop_reason": "end_turn"}).encode()

    def fake_urlopen(req, timeout=0):
        sent["body"] = json.loads(req.data)
        sent["headers"] = dict(req.headers)
        return Resp()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(verify.urllib.request, "urlopen", fake_urlopen)
    p = verify.build_verify_provider({"verify": dict(verify.DEFAULTS)})
    reply = p.ask("sys", "user")
    body = sent["body"]
    assert body["model"] == "claude-opus-5-5"
    assert "temperature" not in body and "thinking" not in body
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["effort"] == "medium"
    assert reply.input_tokens == 57 and reply.output_tokens == 9
    assert json.loads(reply.text)["verdict"] == "unsure"


def test_old_report_without_verification_loads():
    d = _finding().to_dict()
    d.pop("verification")
    f = Finding.from_dict(d)
    assert f.verification == {}
    g = _finding()
    g.verification = {"verdict": "rejected", "reason": "r"}
    assert Finding.from_dict(g.to_dict()).verification == g.verification
    assert g.id == _finding().id  # the verdict is not part of identity


# ---------------------------------------------------------------------------
# through the engine
# ---------------------------------------------------------------------------

def _scan(root, profile, **kw):
    cfg = load_config(None)
    return run_scan([str(root)], cfg, profile=profile, use_adapters=False, use_cache=False, **kw)


def test_offline_and_ci_never_verify(tree, monkeypatch):
    root, _inv = tree
    called = []
    monkeypatch.setattr(verify, "run", lambda *a, **k: called.append(1) or {})
    for profile in ("offline", "ci"):
        rep = _scan(root, profile, model_verify=True)
        assert "verification" not in rep.scan_scope
        assert all(not f.verification for f in rep.findings)
    assert called == []


def test_assisted_profile_verifies_and_reports(tree, monkeypatch):
    root, _inv = tree
    fake = FakeProvider(replies=[_reply("rejected", "cmd is a constant here", line=None)])
    monkeypatch.setattr(verify, "build_verify_provider", lambda config: fake)
    rep = _scan(root, "assisted")
    s = rep.scan_scope["verification"]
    assert s["candidates"] >= 1 and s["rejected"] == s["candidates"] == len(fake.calls)
    f = next(f for f in rep.active() if f.rule_id == "arbiter/python.command-injection")
    assert f.verification["verdict"] == "rejected"
    assert f in rep.active()  # never hidden
    md = render_markdown(rep)
    assert "model: rejected -- cmd is a constant here" in md
    assert "rejected" in md.split("## Findings")[0]
    out = render_console(rep, color=False)
    assert "model verification (claude-opus-5-5" in out
    # round trip through JSON keeps the verdict
    back = Report.from_dict(json.loads(json.dumps(rep.to_dict())))
    assert any(x.verification.get("verdict") == "rejected" for x in back.findings)
    # the gate still sees the finding: tiers are Phase 1's decision
    assert rep.gate["considered"] == len(rep.active())


def test_assisted_without_key_says_not_model_verified(tree, monkeypatch):
    root, _inv = tree
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rep = _scan(root, "assisted")
    s = rep.scan_scope["verification"]
    assert s["not_verified"] == s["candidates"] >= 1
    md = render_markdown(rep)
    assert "were not model-verified (ANTHROPIC_API_KEY is not set)" in md
    assert rep.integrity["ok"]


def test_default_profile_report_unchanged(tree):
    root, _inv = tree
    rep = _scan(root, None)
    assert "verification" not in rep.scan_scope
    assert all(f.verification == {} for f in rep.findings)
