"""Review, adjudication and learning: feedback, calibration, knowledge
pinning, the review front ends and the failure-ledger bridge.
"""
from __future__ import annotations

import json
import re
import sys

import pytest
from helpers import ROOT, _finding

from arbiter.core import Finding, Location
from arbiter.engine import run_scan
from arbiter.policy import load_config

# --------------------------------------------------------------------------
# Learning
# --------------------------------------------------------------------------

def _knowledge_with(rule: str, tp: int, fp: int):
    from arbiter.learn import Knowledge, RuleStats
    k = Knowledge()
    k.rules[rule] = RuleStats(rule_id=rule, true_positives=tp, false_positives=fp)
    return k


def test_feedback_is_recorded_once_per_finding():
    from arbiter.learn import Knowledge, record
    k = Knowledge()
    f = Finding(rule_id="arbiter/x", title="t", location=Location(path="a.py"), evidence="e")
    assert record(k, f, "false_positive", reviewer="tester") is True
    assert record(k, f, "false_positive", reviewer="tester") is False, "one finding must not move the stats twice"
    assert k.rules["arbiter/x"].observations == 1


def test_a_verdict_without_a_reviewer_is_refused():
    """This ledger refuses re-adjudication, so a mark is permanent. A permanent
    mark with nobody's name on it cannot be audited or distrusted later."""
    from arbiter.learn import Knowledge, record
    f = Finding(rule_id="arbiter/x", title="t", location=Location(path="a.py"))
    with pytest.raises(ValueError, match="reviewer"):
        record(Knowledge(), f, "true_positive", reviewer="")
    with pytest.raises(ValueError, match="reviewer"):
        record(Knowledge(), f, "true_positive", reviewer="   ")


class _NotATerminal:
    """stdin as a pipe sees it: readable, and nobody is typing."""

    def isatty(self):
        return False

    def read(self, *a):
        return ""

    def readline(self, *a):
        return ""


def _one_finding_report(tmp_path):
    """A report on disk plus the id of the single finding in it."""
    from arbiter.core import Report
    rep = Report()
    rep.findings.append(Finding(rule_id="arbiter/x", title="t",
                                location=Location(path="a.py", start_line=1)))
    path = tmp_path / "report.json"
    d = rep.to_dict()
    path.write_text(json.dumps(d), encoding="utf-8")
    return path, rep.findings[0].id


def test_a_piped_feedback_invocation_is_refused(tmp_path, monkeypatch, capsys):
    """An agent shelling out, a CI step or a stray script must not be able to
    write a permanent verdict by accident. The ledger refuses re-adjudication,
    so there is no undo for a mark nobody remembers making (REQ-021)."""
    from arbiter.cli import main
    from arbiter.learn import Knowledge
    report, fid = _one_finding_report(tmp_path)
    ledger = tmp_path / "knowledge.json"
    monkeypatch.setattr(sys, "stdin", _NotATerminal())

    code = main(["feedback", fid, "--false-positive", "--report", str(report),
                 "--knowledge", str(ledger), "--reviewer", "tester"])

    assert code != 0
    assert "refuses to adjudicate without a terminal" in capsys.readouterr().err
    # And nothing was written: a refusal that still records is not a refusal.
    assert not ledger.exists() or not Knowledge.load(str(ledger)).adjudicated


def test_an_overridden_batch_verdict_says_it_was_not_typed(tmp_path, monkeypatch):
    """The override exists so a deliberate import is possible. Its value is the
    record it leaves, not the obstacle it fails to be: anything an agent cannot
    pass is something a person cannot pass either."""
    from arbiter.cli import main
    from arbiter.learn import NON_INTERACTIVE_ENTRY_POINTS, Knowledge
    report, fid = _one_finding_report(tmp_path)
    ledger = tmp_path / "knowledge.json"
    monkeypatch.setattr(sys, "stdin", _NotATerminal())

    code = main(["feedback", fid, "--false-positive", "--batch",
                 "--report", str(report), "--knowledge", str(ledger),
                 "--reviewer", "tester"])

    assert code == 0
    verdict = Knowledge.load(str(ledger)).adjudicated[fid]
    assert verdict.verdict == "false_positive"
    assert verdict.reviewer == "tester"
    assert verdict.entry_point == "feedback-batch"
    assert verdict.entry_point in NON_INTERACTIVE_ENTRY_POINTS


def test_an_interactive_review_without_a_terminal_is_refused(tmp_path, monkeypatch, capsys):
    """There is no coherent override for this one. A keypress UI driven by
    something that is not a keyboard is a batch import wearing another name,
    so the refusal points at --apply, which records itself as one."""
    from arbiter.cli import main
    report, _ = _one_finding_report(tmp_path)
    ledger = tmp_path / "knowledge.json"
    monkeypatch.setattr(sys, "stdin", _NotATerminal())

    code = main(["review", str(report), "--interactive",
                 "--knowledge", str(ledger), "--reviewer", "tester"])

    assert code != 0
    err = capsys.readouterr().err
    assert "review --interactive refuses to adjudicate without a terminal" in err
    assert "--apply" in err
    assert not ledger.exists()


def test_a_schema_1_ledger_migrates_without_losing_a_verdict(tmp_path):
    """Schema 1 stored a bare string. Those verdicts are real evidence and must
    survive, but they carry no attribution and must not acquire a plausible one
    on the way through — an invented name would read as a fact later."""
    from arbiter.learn import UNATTRIBUTED, Knowledge
    old = tmp_path / "knowledge.json"
    old.write_text(json.dumps({
        "schema_version": 1,
        "adjudicated": {"f:aaaa1111": "true_positive",
                        "f:bbbb2222": "false_positive:looked at it, it is a test fixture"},
        "rules": {"arbiter/x": {"true_positives": 1, "false_positives": 1}},
    }), encoding="utf-8")

    k = Knowledge.load(str(old))
    assert set(k.adjudicated) == {"f:aaaa1111", "f:bbbb2222"}, "no verdict may be dropped"
    assert k.adjudicated["f:aaaa1111"].verdict == "true_positive"
    assert k.adjudicated["f:bbbb2222"].verdict == "false_positive"
    assert k.adjudicated["f:bbbb2222"].note == "looked at it, it is a test fixture"
    assert all(v.reviewer == UNATTRIBUTED for v in k.adjudicated.values()), \
        "a migrated verdict must say it has no attribution, not guess one"
    assert k.rules["arbiter/x"].observations == 2, "the counts are untouched"

    # And the migrated ledger round-trips through the new schema unchanged.
    new = tmp_path / "migrated.json"
    k.save(str(new))
    again = Knowledge.load(str(new))
    assert {i: v.to_dict() for i, v in again.adjudicated.items()} == \
           {i: v.to_dict() for i, v in k.adjudicated.items()}
    assert json.loads(new.read_text(encoding="utf-8"))["schema_version"] == 2


def test_calibration_waits_for_enough_observations():
    from arbiter.learn import MIN_OBSERVATIONS, calibrated_confidence
    thin = _knowledge_with("r", 5, 0).rules["r"]
    assert thin.proven is False
    assert calibrated_confidence(thin) is None, "five samples is not evidence"
    # Twenty for twenty clears the observation floor but only supports a lower
    # bound near 0.84 — "medium" is the honest label, not "high".
    thick = _knowledge_with("r", MIN_OBSERVATIONS, 0).rules["r"]
    assert thick.proven is True
    assert 0.80 < thick.precision_lower_bound < 0.90
    assert calibrated_confidence(thick) == "medium"

    # High confidence has to be earned with enough samples to support it.
    many = _knowledge_with("r", 60, 0).rules["r"]
    assert many.precision_lower_bound > 0.90
    assert calibrated_confidence(many) == "high"


def test_learning_adjusts_confidence_but_never_severity():
    from arbiter.learn import apply
    k = _knowledge_with("arbiter/x", 4, 36)          # measured precision 0.10
    f = Finding(rule_id="arbiter/x", title="t", severity="critical", confidence="high",
                location=Location(path="a.py"), evidence="e")
    apply([f], k)
    assert f.confidence == "low", "a rule wrong 90% of the time must not stay high confidence"
    assert f.severity == "critical", "how much it matters is policy, not statistics"
    assert any(t.startswith("precision:") for t in f.tags)


def test_knowledge_version_changes_when_learning_does():
    from arbiter.learn import Knowledge, record
    k = Knowledge()
    before = k.version_hash()
    record(k, Finding(rule_id="arbiter/x", title="t", location=Location(path="a.py")),
           "true_positive", reviewer="tester")
    assert k.version_hash() != before


def test_pinned_knowledge_refuses_a_changed_version(tmp_path):
    from arbiter.learn import Knowledge
    kp = tmp_path / "knowledge.json"
    Knowledge().save(str(kp))
    repo = tmp_path / "r"; repo.mkdir(); (repo / "a.py").write_text("x = 1\n")
    with pytest.raises(RuntimeError, match="pinned"):
        run_scan([str(repo)], load_config(None), only=["quality"],
                 knowledge_path=str(kp), pin_knowledge="k:deadbeefdead")


def test_scan_records_the_knowledge_version_it_used(tmp_path):
    repo = tmp_path / "r"; repo.mkdir(); (repo / "a.py").write_text("x = 1\n")
    rep = run_scan([str(repo)], load_config(None), only=["quality"],
                   knowledge_path=str(tmp_path / "k.json"))
    assert rep.learning["knowledge_version"].startswith("k:")


def test_same_inputs_produce_identical_findings(tmp_path):
    """Adaptation happens between runs, never within one."""
    repo = tmp_path / "r"; repo.mkdir()
    (repo / "c.py").write_text('AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\nTODO = 1\n')
    kp = str(tmp_path / "k.json")
    runs = [run_scan([str(repo)], load_config(None), only=["secrets", "quality"],
                     knowledge_path=kp) for _ in range(3)]
    signatures = [tuple(sorted((f.id, f.severity, f.confidence) for f in r.active())) for r in runs]
    assert len(set(signatures)) == 1
    assert len({r.learning["knowledge_version"] for r in runs}) == 1


def test_adaptive_thresholds_respect_a_floor():
    from arbiter.learn import adaptive_threshold, build_profile
    tiny = build_profile([2.0] * 200)        # a codebase of two-line functions
    assert adaptive_threshold(tiny, 120, "p95", 40) == 40, "cannot tighten below the floor"
    huge = build_profile([400.0] * 200)      # uniformly enormous functions
    assert adaptive_threshold(huge, 120, "p95", 40) == 400


def test_adaptive_thresholds_ignore_thin_distributions():
    from arbiter.learn import adaptive_threshold, build_profile
    thin = build_profile([10.0] * 5)
    assert adaptive_threshold(thin, 120, "p95", 40) == 120, "5 samples cannot retune a threshold"


def test_adaptive_is_off_unless_asked(tmp_path):
    repo = tmp_path / "r"; repo.mkdir(); (repo / "a.py").write_text("def f():\n    return 1\n")
    rep = run_scan([str(repo)], load_config(None), only=["ast_metrics"],
                   knowledge_path=str(tmp_path / "k.json"))
    assert rep.learning["adaptive_thresholds"] == ""


def test_review_prefers_rules_close_to_the_proven_threshold(tmp_path):
    """Getting one rule from nineteen to twenty crosses a threshold. One
    observation each on five rules crosses nothing."""
    from arbiter.learn import MIN_OBSERVATIONS, Knowledge, RuleStats
    from arbiter.review import select
    k = Knowledge()
    k.rules["arbiter/near"] = RuleStats(rule_id="arbiter/near",
                                        true_positives=MIN_OBSERVATIONS - 1)
    k.rules["arbiter/done"] = RuleStats(rule_id="arbiter/done",
                                        true_positives=MIN_OBSERVATIONS + 5)
    findings = ([_finding("arbiter/near", f"n{i}.tf") for i in range(5)]
                + [_finding("arbiter/done", f"d{i}.tf") for i in range(5)])
    picked = select(findings, k, limit=3)
    assert all(f.rule_id == "arbiter/near" for f in picked), \
        "an already-proven rule should not consume the budget"


def test_review_spreads_across_rules_not_just_the_loudest(tmp_path):
    from arbiter.learn import Knowledge
    from arbiter.review import select
    findings = ([_finding("arbiter/loud", f"l{i}.tf") for i in range(40)]
                + [_finding("arbiter/quiet", "q.tf")])
    picked = select(findings, Knowledge(), limit=6)
    assert {f.rule_id for f in picked} == {"arbiter/loud", "arbiter/quiet"}


def test_review_spreads_across_files_within_a_rule(tmp_path):
    """Twenty samples of the same mistake in one file are not twenty
    independent observations."""
    from arbiter.learn import Knowledge
    from arbiter.review import select
    findings = ([_finding("arbiter/r", "same.tf", line=i) for i in range(10)]
                + [_finding("arbiter/r", f"other{i}.tf") for i in range(3)])
    picked = select(findings, Knowledge(), limit=4)
    assert len({f.location.path for f in picked}) >= 4


def test_review_spreads_across_repositories_within_a_rule():
    """Twenty findings of one rule from one repository measure that repository,
    not the rule. Drawn from the 36-repository corpus, three of the six queues
    took 19, 18 and 16 of 20 from a single repository, and one of those was
    entirely teaching material — which the corpus tooling states is useless as a
    false-positive measure. File spread alone cannot see this, because one
    repository supplies plenty of distinct files."""
    from arbiter.learn import Knowledge
    from arbiter.review import select
    findings = ([Finding(rule_id="arbiter/r", title="t", repo_id="loud",
                         location=Location(path=f"a{i}.tf", start_line=1,
                                           repo_id="loud"))
                 for i in range(30)]
                + [Finding(rule_id="arbiter/r", title="t", repo_id=rid,
                           location=Location(path="b.tf", start_line=1, repo_id=rid))
                   for rid in ("quiet1", "quiet2", "quiet3")])
    picked = select(findings, Knowledge(), limit=6)
    assert len({f.repo_id for f in picked}) == 4, \
        "one repository with thirty findings must not crowd out three with one each"


def test_review_never_re_asks_an_adjudicated_finding(tmp_path):
    """One disputed finding must not move the statistics as many times as
    somebody clicks."""
    from arbiter.learn import Knowledge, Verdict
    from arbiter.review import select
    f = _finding("arbiter/r")
    k = Knowledge()
    k.adjudicated[f.id] = Verdict(verdict="true_positive", reviewer="tester")
    assert select([f], k, limit=5) == []


def test_review_round_trip_records_marks(tmp_path):
    from arbiter.learn import Knowledge
    from arbiter.review import apply as apply_marks
    from arbiter.review import render, select
    findings = [_finding("arbiter/a", "a.tf"), _finding("arbiter/b", "b.tf"),
                _finding("arbiter/c", "c.tf")]
    k = Knowledge()
    picked = select(findings, k, limit=3)
    text = render(picked, k, "review.md")
    marks = {picked[0].id: "y", picked[1].id: "n"}  # third left blank
    out = []
    for line in text.split("\n"):
        for fid, mark in marks.items():
            if line.startswith(f"[ ] {fid}"):
                line = f"[{mark}]" + line[3:]
        out.append(line)
    res = apply_marks("\n".join(out), findings, k, reviewer="tester")
    assert res["recorded"] == 2, "a blank mark must not be recorded either way"
    assert k.rules["arbiter/a"].true_positives == 1
    assert k.rules["arbiter/b"].false_positives == 1
    assert "arbiter/c" not in k.rules


def test_review_ignores_marks_for_findings_not_in_the_report(tmp_path):
    from arbiter.learn import Knowledge
    from arbiter.review import apply as apply_marks
    res = apply_marks("[y] f:deadbeef1234\n", [_finding("arbiter/a")], Knowledge(),
                      reviewer="tester")
    assert res["recorded"] == 0 and res["unknown"] == ["f:deadbeef1234"]


def test_review_reports_which_rules_became_proven(tmp_path):
    from arbiter.learn import MIN_OBSERVATIONS, Knowledge, RuleStats
    from arbiter.review import apply as apply_marks
    from arbiter.review import newly_proven
    k = Knowledge()
    k.rules["arbiter/a"] = RuleStats(rule_id="arbiter/a",
                                     true_positives=MIN_OBSERVATIONS - 1)
    f = _finding("arbiter/a")
    before = {r: s.observations for r, s in k.rules.items()}
    apply_marks(f"[y] {f.id}\n", [f], k, reviewer="tester")
    assert newly_proven(k, before) == ["arbiter/a"]


def test_review_can_adjudicate_external_tool_findings(tmp_path):
    """Checkov ships no severities, so its checks are exactly the ones whose
    precision most needs a human answer."""
    from arbiter.learn import Knowledge
    from arbiter.review import apply as apply_marks
    f = _finding("checkov/CKV_AWS_16")
    k = Knowledge()
    apply_marks(f"[n] {f.id}\n", [f], k, reviewer="tester")
    assert k.rules["checkov/CKV_AWS_16"].false_positives == 1


# ---------------------------------------------------------------------------
# The adjudicator front ends.
#
# The interface is not a nicety here. Calibration reads exactly one ledger —
# findings a person judged — and that ledger sat at zero for the tool's entire
# life, in a tool built around calibration, because adjudicating meant copying
# fingerprints one at a time.
# ---------------------------------------------------------------------------

def _review_page(tmp_path, n=3):
    from arbiter.learn import Knowledge
    from arbiter.review import select
    from arbiter.review_ui import render_html
    (tmp_path / "app.py").write_text("\n".join(f"line {i}" for i in range(1, 40)))
    findings = [Finding(rule_id=f"arbiter/r{i}", title=f"Finding {i}",
                        description="why this matters", remediation="do the thing",
                        evidence=f"e{i}", location=Location(path="app.py", start_line=10 + i))
                for i in range(n)]
    k = Knowledge()
    picked = select(findings, k, limit=n)
    return render_html(picked, k, {"root": str(tmp_path)}, "arbiter review --apply review.md"), picked


def test_review_page_is_self_contained(tmp_path):
    """No server, no network, no build step. The reports worth adjudicating are
    often the ones you cannot send anywhere."""
    page, _ = _review_page(tmp_path)
    assert "<script src=" not in page and "<link" not in page
    assert "http://" not in page.replace("http://www.w3.org", "")
    for host in ("cdn.", "googleapis", "unpkg", "jsdelivr"):
        assert host not in page


def test_review_page_embeds_code_context(tmp_path):
    """Adjudicating from a list encourages skimming, and a skimmed verdict is
    worse than none — this ledger is the only thing calibration reads."""
    page, _ = _review_page(tmp_path)
    payload = json.loads(re.search(r"window\.__FINDINGS__ = (\[.*?\]);", page, re.S).group(1))
    assert payload and all(p["context"] for p in payload)
    lines = {ln for p in payload for ln, _ in p["context"]}
    assert payload[0]["line"] in lines, "the finding's own line must be shown"


def test_review_page_survives_an_unreadable_file(tmp_path):
    from arbiter.learn import Knowledge
    from arbiter.review_ui import render_html
    f = Finding(rule_id="arbiter/r", title="t", evidence="e",
                location=Location(path="gone.py", start_line=3))
    page = render_html([f], Knowledge(), {"root": str(tmp_path)}, "cmd")
    payload = json.loads(re.search(r"window\.__FINDINGS__ = (\[.*?\]);", page, re.S).group(1))
    assert payload[0]["context"] == [] and payload[0]["context_note"]


def test_review_page_escapes_finding_text(tmp_path):
    """Finding titles carry evidence lifted from the scanned repository, which
    is not the tool's own text and must never reach the page as markup."""
    from arbiter.learn import Knowledge
    from arbiter.review_ui import render_html
    f = Finding(rule_id="arbiter/r", title="<script>alert(1)</script>",
                description="</textarea><img onerror=alert(1)>", evidence="e",
                location=Location(path="a.py"))
    page = render_html([f], Knowledge(), {"root": str(tmp_path)}, "cmd")
    body = page.split("window.__FINDINGS__")[0]
    assert "<script>alert(1)</script>" not in body
    payload = json.loads(re.search(r"window\.__FINDINGS__ = (\[.*?\]);", page, re.S).group(1))
    assert payload[0]["title"] == "<script>alert(1)</script>", "escaped at render, not mangled"


def test_review_page_output_is_the_same_format_apply_reads(tmp_path):
    """The page writes exactly what `arbiter review --apply` already parses, so
    there is one format and one parser rather than two that can drift."""
    from arbiter.learn import Knowledge
    from arbiter.review import apply as apply_marks
    from arbiter.review import parse
    _, picked = _review_page(tmp_path)
    text = "\n".join(f"[{'y' if i % 2 == 0 else 'n'}] {f.id}  {f.title}"
                     for i, f in enumerate(picked))
    assert len(parse(text)) == len(picked)
    k = Knowledge()
    assert apply_marks(text, picked, k, reviewer="tester")["recorded"] == len(picked)


def test_terminal_review_records_the_same_verdicts(tmp_path, monkeypatch):
    from arbiter import review_ui
    from arbiter.learn import Knowledge
    _, picked = _review_page(tmp_path, n=3)
    keys = iter(["y", "n", "s"])
    monkeypatch.setattr(review_ui, "_getch", lambda: next(keys))
    marks = review_ui.run_terminal(picked, Knowledge(), {"root": str(tmp_path)})
    assert marks[picked[0].id] == "true_positive"
    assert marks[picked[1].id] == "false_positive"
    assert picked[2].id not in marks, "skip records nothing"


def test_terminal_review_can_go_back(tmp_path, monkeypatch):
    from arbiter import review_ui
    from arbiter.learn import Knowledge
    _, picked = _review_page(tmp_path, n=2)
    keys = iter(["y", "b", "n", "q"])
    monkeypatch.setattr(review_ui, "_getch", lambda: next(keys))
    marks = review_ui.run_terminal(picked, Knowledge(), {"root": str(tmp_path)})
    assert marks[picked[0].id] == "false_positive", "going back must undo the mark"


# ---------------------------------------------------------------------------
# The failure-ledger bridge (REQ-035).
# ---------------------------------------------------------------------------

def _two_findings():
    return [
        Finding(rule_id="arbiter/secrets.aws-access-key", title="AWS access key committed",
                severity="critical", description="A live-looking key in source.",
                location=Location(path="app/config.py", start_line=12)),
        Finding(rule_id="arbiter/ast.high-complexity", title="High complexity",
                severity="info", description="cx=30.",
                location=Location(path="app/big.py", start_line=1)),
    ]


def test_true_positive_verdicts_draft_open_ledger_entries(tmp_path):
    from arbiter.ledger import draft_entries
    findings = _two_findings()
    path = tmp_path / ".ai" / "failures" / "failure-ledger.json"
    written = draft_entries(findings, [findings[0].id], path, today="2026-10-09")
    assert written == ["FAIL-001"]
    ledger = json.loads(path.read_text(encoding="utf-8"))
    entry = ledger["failures"][0]
    assert entry["status"] == "open" and entry["severity"] == "critical"
    assert entry["affected"] == ["app/config.py"]
    assert findings[0].id in entry["how_detected"]
    assert "app/config.py:12" in entry["symptom"]
    # a false positive, or an unmarked finding, draws nothing
    assert draft_entries(findings, [findings[1].id][:0], path) == []


def test_the_same_verdict_applied_twice_adds_nothing(tmp_path):
    from arbiter.ledger import draft_entries
    findings = _two_findings()
    path = tmp_path / "ledger.json"
    draft_entries(findings, [findings[0].id], path)
    assert draft_entries(findings, [findings[0].id], path) == []
    assert len(json.loads(path.read_text(encoding="utf-8"))["failures"]) == 1


def test_a_drafted_entry_satisfies_the_workspace_ledger_check(tmp_path):
    """The file is OmniEngineering's; its own `omni failure check` is the
    authority on whether an entry is well formed."""
    import subprocess

    from arbiter.ledger import draft_entries
    findings = _two_findings()
    (tmp_path / ".ai" / "failures").mkdir(parents=True)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "config.py").write_text("KEY = 'x'\n")
    (tmp_path / "app" / "big.py").write_text("pass\n")
    draft_entries(findings, [findings[0].id], tmp_path / ".ai" / "failures" / "failure-ledger.json")
    r = subprocess.run([sys.executable, str(ROOT / "omni"), "failure", "check"],
                       cwd=str(tmp_path), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    # info is not a ledger severity: the bridge writes the nearest honest one
    assert draft_entries(findings, [findings[1].id],
                         tmp_path / ".ai" / "failures" / "failure-ledger.json") == ["FAIL-002"]
    r = subprocess.run([sys.executable, str(ROOT / "omni"), "failure", "check"],
                       cwd=str(tmp_path), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_review_apply_reports_which_ids_it_recorded(tmp_path):
    from arbiter.learn import Knowledge
    from arbiter.review import apply, render
    findings = _two_findings()
    knowledge = Knowledge()
    text = render(findings, knowledge, "r.json")
    marked = text.replace(f"[ ] {findings[0].id}", f"[y] {findings[0].id}")
    res = apply(marked, findings, knowledge, reviewer="t")
    assert res["recorded"] == 1
    assert res["recorded_ids"]["true_positive"] == [findings[0].id]
    assert res["recorded_ids"]["false_positive"] == []
