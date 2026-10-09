"""PR-native output (REQ-039).

GitHub workflow-command annotations, the gate naming the findings that failed
it, and the pull-request comment reaching the job summary only under GitHub
Actions. Every test here carries a control that fails if the feature is
removed, so the suite cannot pass by rendering nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from arbiter.core import Finding, Location, Report
from arbiter.engine import run_scan
from arbiter.policy import evaluate_gate, load_config
from arbiter.report import render_annotations

HIGH_PY = 'TOKEN = "ghp_' + "b" * 36 + '"\n'
LOW_PY = (
    "import ssl\n"
    "def f():\n"
    "    try:\n"
    "        pass\n"
    "    except Exception:\n"
    "        pass\n"
)


def _report(findings: list[Finding], gate: dict | None = None) -> Report:
    rep = Report()
    rep.findings = findings
    rep.gate = gate if gate is not None else {}
    return rep


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line]


# --------------------------------------------------------------------------
# annotations
# --------------------------------------------------------------------------

def test_annotations_mark_gating_findings_as_errors_and_the_rest_as_warnings(tmp_path):
    """Exactly the findings that failed the gate are errors; the rest warn."""
    (tmp_path / "leak.py").write_text(HIGH_PY, encoding="utf-8")
    cfg = load_config(None)
    cfg["gate"] = {"fail_on": {"severity": "high"}}
    cfg["rules"] = [{
        "id": "readme-present", "type": "file_exists", "paths": ["README.md"],
        "title": "README.md is required", "severity": "low",
    }]
    rep = run_scan([str(tmp_path)], cfg, use_adapters=False)
    sev = {f.severity for f in rep.active()}
    assert "critical" in sev or "high" in sev, "no high finding to gate on"
    assert "low" in sev or "medium" in sev, "no low finding for the control"
    assert rep.gate["passed"] is False

    failing = set(rep.gate["failing_ids"])
    assert failing, "the gate failed but named nothing"
    by_id = {f.id: f for f in rep.active()}
    lines = _lines(render_annotations(rep))
    assert len(lines) == len(rep.active())

    errors = [line for line in lines if line.startswith("::error ")]
    warnings = [line for line in lines if line.startswith("::warning ")]
    assert len(errors) == len(failing)
    assert len(warnings) == len(rep.active()) - len(failing)
    for fid in failing:
        f = by_id[fid]
        assert any(f"title={f.rule_id}::" in line for line in errors), fid
    # The error names the file and the line, so the runner can pin it.
    leak = next(f for f in rep.active() if f.location.path == "leak.py")
    assert leak.id in failing
    assert f"::error file=leak.py,line={leak.location.start_line},title={leak.rule_id}::" \
        in render_annotations(rep)
    # Control: the low finding is not an error and is not in failing_ids.
    for f in rep.active():
        if f.id not in failing:
            assert not any(f"title={f.rule_id}::" in line for line in errors)
            assert any(f"title={f.rule_id}::" in line for line in warnings)


def test_annotations_use_notice_for_findings_outside_the_change():
    inside = Finding(rule_id="r/a", title="A", location=Location(path="a.py", start_line=3))
    outside = Finding(rule_id="r/b", title="B", location=Location(path="b.py", start_line=4),
                      tags=["outside-this-change"])
    text = render_annotations(_report([inside, outside], {"passed": True, "failing_ids": []}))
    assert "::warning file=a.py,line=3,title=r/a::A" in text
    assert "::notice file=b.py,line=4,title=r/b::B" in text
    # Control: a failing finding is an error even when outside the change.
    text = render_annotations(_report([inside, outside],
                                      {"passed": False, "failing_ids": [outside.id]}))
    assert "::error file=b.py,line=4,title=r/b::B" in text


def test_annotations_skip_suppressed_findings_and_omit_a_zero_line():
    shown = Finding(rule_id="r/a", title="A", location=Location(path="a.py"))
    hidden = Finding(rule_id="r/b", title="B", location=Location(path="b.py", start_line=2),
                     suppressed=True)
    text = render_annotations(_report([shown, hidden]))
    assert text == "::warning file=a.py,title=r/a::A"
    assert "line=" not in text
    assert "r/b" not in text
    # Control: unsuppressed, the same finding is printed.
    hidden.suppressed = False
    assert "::warning file=b.py,line=2,title=r/b::B" in render_annotations(_report([shown, hidden]))


def test_annotations_escape_workflow_command_syntax():
    """`:` `,` `%` and newlines would otherwise end the command early."""
    f = Finding(rule_id="r/x:y,z", title="50% done: a, b\nsecond line\r",
                location=Location(path="dir,name:x/f.py", start_line=7))
    text = render_annotations(_report([f]))
    assert text == (
        "::warning file=dir%2Cname%3Ax/f.py,line=7,title=r/x%3Ay%2Cz"
        "::50%25 done: a, b%0Asecond line%0D"
    )
    # Controls: nothing raw survived in a property, and no raw newline at all.
    props, _, message = text.partition("::50")
    assert ":" not in props.replace("::warning ", "")
    assert "\n" not in text and "\r" not in text
    assert "%25" in message


def test_annotations_never_print_evidence(tmp_path):
    """A finding names where a credential is and what kind; never its value."""
    value = "sk_live_51H8xQ2LkdIwHu7ix" + "Z" * 20
    (tmp_path / "billing.py").write_text(f'STRIPE_KEY = "{value}"\n', encoding="utf-8")
    rep = run_scan([str(tmp_path)], load_config(None), only=["secrets"], use_adapters=False)
    assert rep.findings, "fixture produced nothing, so this proves nothing"
    text = render_annotations(rep)
    assert text, "nothing rendered, so this proves nothing"
    assert value not in text
    assert value[:12] not in text
    # Control: the finding itself is there, by rule and file.
    assert "file=billing.py" in text


def test_annotations_are_written_beside_the_other_formats(tmp_path):
    from arbiter.report import write_all
    f = Finding(rule_id="r/a", title="A", location=Location(path="a.py", start_line=1))
    written = write_all(_report([f]), str(tmp_path / "out"), ["json", "annotations"])
    assert set(written) == {"json", "annotations"}
    body = Path(written["annotations"]).read_text(encoding="utf-8")
    assert body == "::warning file=a.py,line=1,title=r/a::A\n"
    # Control: not requested, not written.
    written = write_all(_report([f]), str(tmp_path / "out2"), ["json"])
    assert "annotations" not in written
    assert not (tmp_path / "out2" / "annotations.txt").exists()


# --------------------------------------------------------------------------
# the gate names what failed it
# --------------------------------------------------------------------------

def test_the_gate_names_the_findings_that_failed_it():
    crit = Finding(rule_id="r/c", title="c", severity="critical")
    new_high = Finding(rule_id="r/h", title="h", severity="high", status="new")
    old_high = Finding(rule_id="r/o", title="o", severity="high", status="existing")
    low = Finding(rule_id="r/l", title="l", severity="low")
    inferred = Finding(rule_id="r/i", title="i", severity="critical", provenance="inferred")
    rep = _report([crit, new_high, old_high, low, inferred])

    gate = evaluate_gate(rep, {"gate": {"fail_on": {"severity": "critical", "new": "high"}}})
    assert gate["passed"] is False
    assert gate["reasons"] == ["1 finding(s) at or above critical",
                               "2 new finding(s) at or above high"]
    # The two reasons describe crit (severity) and crit + new_high (new).
    assert gate["failing_ids"] == sorted({crit.id, new_high.id})
    assert old_high.id not in gate["failing_ids"]
    assert inferred.id not in gate["failing_ids"], "inferred findings do not gate"

    # A passing gate names nothing.
    gate = evaluate_gate(rep, {"gate": {"fail_on": {"severity": "critical"},
                                        "gate_on_inferred": False}})
    assert gate["failing_ids"] == [crit.id]
    gate = evaluate_gate(_report([low]), {"gate": {"fail_on": {"severity": "critical"}}})
    assert gate["passed"] is True
    assert gate["failing_ids"] == []

    # A coverage reason has no finding to name.
    thin = _report([low])
    thin.scorecard.coverage = 0.1
    gate = evaluate_gate(thin, {"gate": {"fail_on": {"coverage_below": 0.5}}})
    assert gate["passed"] is False and gate["failing_ids"] == []


def test_failing_ids_round_trip_through_the_json_report(tmp_path):
    from arbiter.report import write_all
    f = Finding(rule_id="r/c", title="c", severity="critical")
    rep = _report([f])
    rep.gate = evaluate_gate(rep, {"gate": {"fail_on": {"severity": "critical"}}})
    written = write_all(rep, str(tmp_path), ["json"])
    import json
    back = Report.from_dict(json.loads(Path(written["json"]).read_text(encoding="utf-8")))
    assert back.gate["failing_ids"] == [f.id]
    assert "::error" in render_annotations(back)


# --------------------------------------------------------------------------
# --github and the job summary
# --------------------------------------------------------------------------

def _gate(argv: list[str], capsys) -> tuple[int, str]:
    from arbiter.cli import main
    code = main(["gate", *argv, "--no-adapters"])
    return code, capsys.readouterr().out


@pytest.fixture
def leaky(tmp_path):
    target = tmp_path / "repo"
    target.mkdir()
    (target / "leak.py").write_text(HIGH_PY, encoding="utf-8")
    (target / "arbiter.yaml").write_text(
        "gate:\n  fail_on:\n    severity: high\n", encoding="utf-8")
    return target


def test_gate_writes_the_pr_comment_to_the_step_summary_only_under_github_actions(
        tmp_path, monkeypatch, capsys, leaky):
    summary = tmp_path / "summary.md"
    summary.write_text("# before\n", encoding="utf-8")
    out = str(tmp_path / "out")

    # Control: no GitHub Actions, the file is untouched and nothing annotates.
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code, stdout = _gate([str(leaky), "--out", out, "--format", "json"], capsys)
    assert code == 1
    assert summary.read_text(encoding="utf-8") == "# before\n"
    assert "::error" not in stdout

    # Under GitHub Actions the comment is appended and annotations printed.
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    code, stdout = _gate([str(leaky), "--out", out, "--format", "json"], capsys)
    assert code == 1
    body = summary.read_text(encoding="utf-8")
    assert body.startswith("# before\n")
    assert "**Arbiter: fail**" in body
    assert body.endswith("\n")
    assert "::error file=leak.py,line=1," in stdout
    assert not (tmp_path / "out" / "annotations.txt").read_text(encoding="utf-8").isspace()

    # --no-github overrides the environment: the file is untouched again.
    summary.write_text("# reset\n", encoding="utf-8")
    code, stdout = _gate([str(leaky), "--out", out, "--format", "json", "--no-github"], capsys)
    assert code == 1
    assert summary.read_text(encoding="utf-8") == "# reset\n"
    assert "::error" not in stdout

    # --github forces it on outside Actions; no summary variable, no file.
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    code, stdout = _gate([str(leaky), "--out", out, "--format", "json", "--github"], capsys)
    assert code == 1
    assert "::error file=leak.py,line=1," in stdout
    assert summary.read_text(encoding="utf-8") == "# reset\n"


def test_annotations_print_after_the_console_output(tmp_path, capsys, leaky):
    code, stdout = _gate([str(leaky), "--out", str(tmp_path / "out"),
                          "--format", "console,annotations"], capsys)
    assert code == 1
    assert "::error " in stdout
    assert stdout.index("  arbiter ") < stdout.index("::error "), \
        "annotations must follow the console report so the log reads top-down"


# --------------------------------------------------------------------------
# the repository's own pull-request check
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]


def test_pr_check_runs_the_self_gate_in_github_mode_and_uploads_sarif():
    """The self-gate annotates, summarises and reaches code scanning, and the
    upload is the only step allowed to be advisory: its verdict was already
    delivered by the gate step before it, and a fork without code scanning
    must not turn that verdict into a failure."""
    import yaml
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "pr-check.yml")
                        .read_text(encoding="utf-8"))
    job = wf["jobs"]["check"]
    perms = {**(wf.get("permissions") or {}), **(job.get("permissions") or {})}
    assert perms.get("security-events") == "write", "the SARIF upload needs security-events: write"
    assert perms.get("contents") == "read"

    steps = job["steps"]
    gate = next(s for s in steps if s.get("name") == "Arbiter self-gate")
    assert "--github" in gate["run"]
    assert "sarif" in gate["run"] and "pr-comment" in gate["run"]

    upload = next(s for s in steps if "upload-sarif" in str(s.get("uses", "")))
    assert steps.index(upload) > steps.index(gate), "nothing to upload before the gate ran"
    assert upload["with"]["sarif_file"] == "arbiter-out/report.sarif"
    assert "always()" in upload["if"] and "Linux" in upload["if"]
    assert upload.get("continue-on-error") is True
    # Control: every other step still fails the run.
    others = [s for s in steps if s is not upload and s.get("continue-on-error")]
    assert others == []

    action = yaml.safe_load((ROOT / "ci" / "github-action" / "action.yml")
                            .read_text(encoding="utf-8"))
    run = next(s for s in action["runs"]["steps"] if s.get("id") == "run")["run"]
    assert "--github" in run


def test_sarif_min_severity_keeps_only_findings_at_or_above_it(tmp_path):
    """The code-scanning upload can be trimmed; every other format is untouched by the flag."""
    import json

    from arbiter.core import Finding, Location, Report
    from arbiter.report import write_sarif
    report = Report(system="s", repos=[], findings=[
        Finding(rule_id="x/high", dimension="security", severity="high", title="h", location=Location(path="a.py", start_line=1)),
        Finding(rule_id="x/low", dimension="quality", severity="low", title="l", location=Location(path="b.py", start_line=1)),
    ])
    write_sarif(report, str(tmp_path / "all.sarif"))
    write_sarif(report, str(tmp_path / "medium.sarif"), min_severity="medium")
    rules = lambda name: {r["ruleId"] for r in json.loads((tmp_path / name).read_text(encoding="utf-8"))["runs"][0]["results"]}
    assert rules("all.sarif") == {"x/high", "x/low"}        # control
    assert rules("medium.sarif") == {"x/high"}
