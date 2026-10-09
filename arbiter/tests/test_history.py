"""Run history and the trend dashboard (REQ-042).

Every test has a control: the property is shown to hold, and then shown to
be the thing that varies, so a test that passes on an empty string cannot
pass here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from arbiter import history
from arbiter.cli import main
from arbiter.core import Finding, Location, RepoInfo, Report, Scorecard

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "fixtures" / "legacy-platform"

# The planted credentials in fixtures/legacy-platform/app/config.py. If the
# fixture changes, this test must change with it, so the values are spelled
# out here rather than read back from the file.
PLANTED = ["AKIAIOSFODNN7EXAMPLE", "hunter2-Zx91qKp4vWmTn83LcRd7"]


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text(
        'AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n', encoding="utf-8")
    return repo


def _scan(target: Path, out: Path, *extra: str, cmd: str = "scan") -> int:
    args = [cmd, str(target), "--out", str(out), "--only", "secrets",
            "--no-adapters", "--format", "json", *extra]
    if cmd == "scan":
        args.append("--no-open")
    return main(args)


def _lines(out: Path) -> list[str]:
    p = out / history.HISTORY_FILE
    if not p.exists():
        return []
    return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _report(commit: str = "abc123def456", score: float | None = 82.4,
            coverage: float = 0.91, withheld: bool = False) -> Report:
    r = Report(system="demo", profile="offline", started_at="2026-10-09T12:00:00+00:00",
               duration_s=1.5, repos=[RepoInfo(id="root", path="/x", commit=commit)])
    r.scorecard = Scorecard(overall=score, coverage=coverage, withheld=withheld,
                            withheld_reason="coverage below threshold" if withheld else "")
    r.findings = [
        Finding(rule_id="arbiter/secrets.aws", title="AWS key", severity="high",
                location=Location(path="app/config.py", start_line=3), evidence="AKIA..."),
        Finding(rule_id="arbiter/quality.long", title="long file", severity="low",
                location=Location(path="src/big.py", start_line=1), status="existing"),
    ]
    r.gate = {"passed": True, "reasons": []}
    return r


# --------------------------------------------------------------------------

def test_every_scan_appends_one_history_line(tmp_path):
    repo = _repo(tmp_path)
    out = tmp_path / "out"

    assert _scan(repo, out) == 0
    assert len(_lines(out)) == 1
    assert _scan(repo, out) == 0
    assert len(_lines(out)) == 2, "two scans, two lines"
    # A gate appends too, whichever way it goes.
    _scan(repo, out, cmd="gate")
    assert len(_lines(out)) == 3

    rows = history.load(out / history.HISTORY_FILE)
    assert [r["findings_total"] for r in rows] == [1, 1, 1]
    assert rows[0]["counts"]["critical"] == 1 and rows[0]["mode"] == "full"

    # Control: --no-history leaves the file exactly as it was, on scan and gate.
    before = (out / history.HISTORY_FILE).read_bytes()
    assert _scan(repo, out, "--no-history") == 0
    _scan(repo, out, "--no-history", cmd="gate")
    assert (out / history.HISTORY_FILE).read_bytes() == before
    # ...and a fresh directory scanned with it never gets a history file.
    fresh = tmp_path / "fresh"
    assert _scan(repo, fresh, "--no-history") == 0
    assert (fresh / "report.json").exists()
    assert not (fresh / history.HISTORY_FILE).exists()


def test_history_lines_carry_no_evidence_or_paths(tmp_path):
    out = tmp_path / "out"
    assert _scan(LEGACY, out) in (0, 1)
    lines = _lines(out)
    assert len(lines) == 1
    line = lines[0]

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    paths = sorted({f["location"]["path"] for f in report["findings"] if f["location"]["path"]})
    titles = sorted({f["title"] for f in report["findings"]})
    evidence = [f["evidence"] for f in report["findings"] if f.get("evidence")]

    # Control: the planted secrets are in the fixture and the scan found
    # them, so there is a finding whose detail could leak; the report carries
    # the paths, the titles and the (masked) evidence, as it should.
    fixture_text = (LEGACY / "app" / "config.py").read_text(encoding="utf-8")
    assert all(secret in fixture_text for secret in PLANTED)
    assert "app/config.py" in paths, "the fixture's secret was not found; the test proves nothing"
    report_text = json.dumps(report)
    assert paths and all(p in report_text for p in paths)
    assert titles and all(t in report_text for t in titles)
    assert evidence and all(ev in report_text for ev in evidence)

    for secret in PLANTED:
        assert secret not in line
    for p in paths:
        assert p not in line, f"finding path {p!r} leaked into the history line"
    for t in titles:
        assert t not in line, f"finding title {t!r} leaked into the history line"
    for ev in evidence:
        assert ev not in line
    row = json.loads(line)
    assert set(row) == {"time", "commit", "system", "profile", "mode", "grade", "score", "coverage",
                        "counts", "new_high_or_above", "gate_passed", "duration_s", "findings_total"}
    assert row["findings_total"] == len([f for f in report["findings"] if not f["suppressed"]]) > 0
    assert row["counts"]["critical"] + row["counts"]["high"] >= 1


def test_dashboard_is_self_contained(tmp_path):
    out = tmp_path / "out"
    history.append(_report(commit="feedbeef1234", score=71.0), str(out))
    history.append(_report(commit="cafef00d5678", score=None, withheld=True), str(out))
    history.append(_report(commit="d15ea5e00042", score=80.2), str(out))
    history.append(_report(commit="0badc0de9abc", score=88.6), str(out))
    page = tmp_path / "dashboard.html"
    assert main(["dashboard", "--history", str(out / history.HISTORY_FILE), "--out", str(page)]) == 0
    text = page.read_text(encoding="utf-8")

    for token in ("http://", "https://", "src=", "<link", "@import"):
        assert token not in text, f"{token!r} would reach outside the page"
    assert "<script" not in text

    # Control: the page does carry the data -- the latest grade, every
    # commit, the withheld gap and both charts.
    assert "89/100" in text
    for commit in ("feedbeef1234", "cafef00d5678", "0badc0de9abc"):
        assert commit in text
    assert "withheld" in text
    assert text.count("<svg") == 2
    assert text.count("<polyline") >= 2  # score and coverage lines
    # The run order is newest first.
    assert text.index("0badc0de9abc") < text.index("cafef00d5678") < text.index("feedbeef1234")
    # No finding detail is on the page because none reached the history.
    assert "app/config.py" not in text and "AWS key" not in text


def test_dashboard_renders_an_empty_history(tmp_path):
    missing = tmp_path / "no-such-dir" / "history.jsonl"
    page = tmp_path / "dashboard.html"
    assert main(["dashboard", "--history", str(missing), "--out", str(page)]) == 0
    text = page.read_text(encoding="utf-8")
    assert "No runs recorded yet" in text
    assert "<svg" not in text and "<table" not in text
    assert "http" not in text

    # Control: one row and the empty state is gone, replaced by the charts.
    history.append(_report(), str(missing.parent))
    assert main(["dashboard", "--history", str(missing), "--out", str(page)]) == 0
    text = page.read_text(encoding="utf-8")
    assert "No runs recorded yet" not in text
    assert "<svg" in text and "<table" in text


def test_corrupt_history_lines_are_skipped(tmp_path):
    path = tmp_path / "history.jsonl"
    good = json.dumps(history.row_from(_report(commit="aaa111")))
    good2 = json.dumps(history.row_from(_report(commit="bbb222")))
    path.write_text("\n".join([
        good,
        "{not json at all",
        good2[: len(good2) // 2],       # truncated mid-write
        "",
        "[1, 2, 3]",                    # valid JSON, not a run
        '{"commit": "no-time"}',        # a dict missing the one required key
        good2,
    ]) + "\n", encoding="utf-8")
    rows = history.load(path)
    assert [r["commit"] for r in rows] == ["aaa111", "bbb222"]
    # The page still renders from what survived.
    text = history.render_dashboard(rows, "t")
    assert "aaa111" in text and "bbb222" in text

    # Control: with every line intact, every line is loaded.
    path.write_text(good + "\n" + good2 + "\n", encoding="utf-8")
    assert len(history.load(path)) == 2
    assert history.load(tmp_path / "absent.jsonl") == []


def test_a_history_row_states_what_the_report_states():
    row = history.row_from(_report())
    assert row["grade"] == "82" and row["score"] == 82.4
    assert row["coverage"] == 0.91 and row["gate_passed"] is True
    assert row["counts"] == {"critical": 0, "high": 1, "medium": 0, "low": 1, "info": 0}
    assert row["new_high_or_above"] == 1  # the low finding is existing; the high one is new
    assert row["findings_total"] == 2 and row["duration_s"] == 1.5
    assert row["commit"] == "abc123def456" and row["mode"] == "full"

    # Control: a withheld grade is "withheld", and its score is null rather
    # than a number the report refused to state.
    held = history.row_from(_report(score=55.0, withheld=True))
    assert held["grade"] == "withheld" and held["score"] is None
    with pytest.raises(AssertionError):
        assert held["grade"] == "55"
