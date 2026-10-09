"""Reporters and comparison: SARIF, JSON, HTML, Markdown, the PR comment,
diffs and the A/B harness.
"""
from __future__ import annotations

import json
from pathlib import Path

from helpers import LEGACY, _finding

from arbiter.ab import Arm, compare, load_ground_truth, run_ab, score_ground_truth
from arbiter.core import Finding, Location, Report
from arbiter.engine import run_scan
from arbiter.policy import load_config
from arbiter.report import render_html, render_markdown, write_sarif

# --------------------------------------------------------------------------
# reporters
# --------------------------------------------------------------------------

def test_sarif_is_wellformed(legacy_report, tmp_path):
    p = tmp_path / "r.sarif"
    write_sarif(legacy_report, str(p))
    doc = json.loads(p.read_text())
    assert doc["version"] == "2.1.0"
    run = doc["runs"][0]
    rule_ids = {r["id"] for r in run["tool"]["driver"]["rules"]}
    assert rule_ids
    for res in run["results"]:
        assert res["ruleId"] in rule_ids
        assert res["level"] in ("error", "warning", "note", "none")
        assert res["locations"][0]["physicalLocation"]["region"]["startLine"] >= 1
        assert res["fingerprints"]["arbiter/v1"].startswith("f:")


def test_round_trip_through_json(legacy_report):
    again = Report.from_dict(json.loads(json.dumps(legacy_report.to_dict())))
    assert len(again.findings) == len(legacy_report.findings)
    assert {f.id for f in again.findings} == {f.id for f in legacy_report.findings}
    assert again.scorecard.coverage == legacy_report.scorecard.coverage


def test_html_and_markdown_render(legacy_report):
    html = render_html(legacy_report)
    assert "Arbiter" in html and "not a pass" in html
    md = render_markdown(legacy_report)
    assert md.startswith("# Arbiter report")


def test_html_puts_the_read_first_block_above_the_findings(legacy_report):
    """The Markdown report has carried the "Read first" block since it was
    added; the HTML renderer built the same block into a local and never
    interpolated it (FAIL-045), so the HTML page showed a clean-looking table
    with no caveats above it. A skipped probe is the simplest caveat."""
    from arbiter.core import ProbeOutcome
    rep = Report.from_dict(json.loads(json.dumps(legacy_report.to_dict())))
    rep.probes.append(ProbeOutcome(name="semgrep", status="skipped", reason="missing binary: semgrep"))
    html = render_html(rep)
    assert "Read first" in html and "probe(s) not assessed" in html
    assert html.index("Read first") < html.index("<h2>Findings")
    assert "Read first" in render_markdown(rep)


# --------------------------------------------------------------------------
# A/B harness
# --------------------------------------------------------------------------

def test_compare_matches_identical_runs(legacy_report):
    matches, only_a, only_b = compare(legacy_report.active(), legacy_report.active())
    assert not only_a and not only_b
    assert all(m.how == "fingerprint" for m in matches)


def test_compare_reports_how_it_matched():
    a = [Finding(rule_id="x/a", title="Bucket is public", location=Location(path="m.tf", start_line=10))]
    b = [Finding(rule_id="y/b", title="Public bucket detected", location=Location(path="m.tf", start_line=11))]
    matches, only_a, only_b = compare(a, b)
    assert len(matches) == 1 and matches[0].how == "location"
    assert not only_a and not only_b


def test_compare_does_not_match_across_files():
    a = [Finding(rule_id="x/a", title="Same title", location=Location(path="one.tf", start_line=3))]
    b = [Finding(rule_id="y/b", title="Same title", location=Location(path="two.tf", start_line=3))]
    matches, only_a, only_b = compare(a, b)
    assert not matches and len(only_a) == 1 and len(only_b) == 1


def test_ab_two_configs_of_arbiter():
    cfg = load_config(None, str(LEGACY))
    res = run_ab(
        "unit", [str(LEGACY)],
        Arm(name="a", only=["secrets"]),
        Arm(name="b", only=["secrets", "quality"]),
        cfg,
    )
    assert not res.a.error and not res.b.error
    assert len(res.b.findings) > len(res.a.findings)
    assert not res.only_a, "the narrower arm must be a subset of the wider one"
    assert res.only_b


def test_precision_is_withheld_unless_fixture_is_exhaustive():
    truth = load_ground_truth(str(LEGACY))
    res = score_ground_truth(truth, [])
    assert res.exhaustive is False
    assert res.precision is None, "precision is meaningless against a partial ground truth"


def test_diff_survives_a_line_shift(tmp_path):
    from arbiter.diff import diff_reports
    before_dir = tmp_path / "before"
    before_dir.mkdir()
    (before_dir / "c.py").write_text('AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n')
    cfg = load_config(None)
    before = run_scan([str(before_dir)], cfg, only=["secrets"])

    after_dir = tmp_path / "after"
    after_dir.mkdir()
    (after_dir / "c.py").write_text('\n\n\nAWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n')
    after = run_scan([str(after_dir)], cfg, only=["secrets"])

    d = diff_reports(before, after)
    assert not d.new and not d.fixed, "a pure line shift must not churn the baseline"
    assert d.persisting


def test_diff_detects_real_change(tmp_path):
    from arbiter.diff import diff_reports
    a = tmp_path / "a"; a.mkdir(); (a / "c.py").write_text("X = 1\n")
    b = tmp_path / "b"; b.mkdir()
    (b / "c.py").write_text('X = 1\nGH_TOKEN = "ghp_' + "a" * 36 + '"\n')
    cfg = load_config(None)
    d = diff_reports(run_scan([str(a)], cfg, only=["secrets"]),
                     run_scan([str(b)], cfg, only=["secrets"]))
    assert len(d.new) >= 1
    assert d.worst_new() in ("critical", "high")


def test_pr_comment_names_what_was_not_assessed(legacy_report):
    from arbiter.diff import render_pr_comment
    text = render_pr_comment(legacy_report)
    assert "Arbiter" in text
    assert "Not assessed" in text


# ---------------------------------------------------------------------------
# A report has to say what it looked at, not just what it found.
#
# Without per-language line counts the only denominator available is
# whole-repository size, and that is how a Kubernetes rule scores a perfect
# record inside a 400,000-line Go project containing forty lines of YAML: the
# other 399,960 lines were never eligible to fail. Rates computed that way are
# not wrong by a little, they are answering a different question.
# ---------------------------------------------------------------------------

def test_report_breaks_lines_down_by_language_and_role(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\ny = 2\n")
    (tmp_path / "deploy.yaml").write_text("kind: Pod\nmetadata:\n  name: a\n")
    (tmp_path / "README.md").write_text("# hi\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["quality"],
                   use_adapters=False)

    assert rep.loc_by_language.get("python") == 2
    assert rep.loc_by_language.get("yaml") == 3
    assert rep.loc_by_language.get("markdown") == 1
    assert rep.loc_by_role.get("docs") == 1
    # the breakdown must account for every line the whole-repo figure claims
    assert sum(rep.loc_by_language.values()) == sum(rep.loc_by_role.values())
    assert sum(rep.loc_by_language.values()) == sum(r.loc for r in rep.repos)


def test_language_breakdown_survives_serialization(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["quality"],
                   use_adapters=False)
    d = json.loads(json.dumps(rep.to_dict()))
    assert d["loc_by_language"]["python"] == 1
    assert d["loc_by_role"]["source"] == 1


# ---------------------------------------------------------------------------
# Batch adjudication.
#
# Calibration reads the adjudicated ledger and nothing else, and that ledger
# sat empty because adjudicating meant copying fingerprints one at a time.
# These tests cover the sampling, which is the part that makes twenty
# adjudications worth more than twenty random ones.
# ---------------------------------------------------------------------------

def test_text_artifacts_are_written_as_utf8(tmp_path):
    """Written without an explicit encoding these took the platform default,
    so on Windows the renderings — which emit em dashes — came out as cp1252
    and would not decode as UTF-8 anywhere else. Reports travel into
    accreditation packages and pull requests, so they cross machines."""
    from arbiter.report import write_all
    rep = Report()
    rep.findings.append(_finding("arbiter/x", path="a.tf"))
    written = write_all(rep, str(tmp_path), ["html", "markdown"])
    for path in written.values():
        # the assertion is that this does not raise UnicodeDecodeError
        Path(path).read_bytes().decode("utf-8")


def test_evidence_from_a_utf8_source_file_is_not_mangled(tmp_path):
    """Files belonging to the target were read with errors='replace' and no
    encoding, so the codec was the platform default. cp1252 decodes almost
    every byte without erroring, so it did not fail loudly, it mis-decoded
    silently: an em dash reached a generated review queue as `â€”`."""
    (tmp_path / "app.py").write_bytes(
        "# static analysis — silenced here  # noqa\n".encode("utf-8"))
    cfg = dict(load_config(None))
    found = run_scan([str(tmp_path)], cfg, only=["assurance"]).active()
    evidence = " ".join(f.evidence or "" for f in found)
    assert "—" in evidence
    assert "â" not in evidence


def test_no_output_format_reprints_a_secret(tmp_path):
    """The documented promise, machine-checked.

    A finding tells you where a credential is and what kind it is. Reprinting
    the value into a build log would turn the scanner into the leak.
    """
    from arbiter.report import write_all
    value = "sk_live_51H8xQ2LkdIwHu7ix" + "Z" * 20
    (tmp_path / "billing.py").write_text(f'STRIPE_KEY = "{value}"\n')
    rep = run_scan([str(tmp_path)], load_config(None), only=["secrets"], use_adapters=False)
    assert rep.findings, "fixture produced nothing, so this proves nothing"
    out = tmp_path / "out"
    written = write_all(rep, str(out), ["json", "sarif", "html", "markdown", "annotations"])
    assert len(written) == 5
    for kind, path in written.items():
        assert value not in Path(path).read_text(), f"{kind} reprinted the secret"
    from arbiter.report import render_console
    assert value not in render_console(rep, color=False)
