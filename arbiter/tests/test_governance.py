"""The governance probe and requirement attribution (REQ-038).

A failure ledger is a set of promises: this file was where the defect lived,
this test is what keeps it out. These tests hold Arbiter to reading those
promises against a change, and every one carries a control -- the case that
must NOT be reported -- because a check that cannot stay silent is noise, and
tools/mutate_tests.py puts the status filter back to make sure the silence is
earned rather than accidental.
"""
from __future__ import annotations

import json
import subprocess

from helpers import CLEAN_PY, LEAKY_PY, _git_repo

from arbiter.core import Finding, Location, ProbeOutcome, Report
from arbiter.diff import render_pr_comment
from arbiter.engine import run_scan
from arbiter.policy import load_config
from arbiter.report import render_console, render_markdown

LEDGER = ".ai/failures/failure-ledger.json"
OPEN_RULE = "arbiter/governance.open-failure-untested"
MISSING_RULE = "arbiter/governance.regression-test-missing"


def _ledger(*entries: dict) -> str:
    return json.dumps({"version": "1.0.0", "failure_id_prefix": "FAIL", "failures": list(entries)})


def _entry(fid: str, status: str, affected: list[str], tests: list[str]) -> dict:
    return {"id": fid, "status": status, "title": f"title of {fid}",
            "affected": affected, "regression_tests": tests}


def _commit(root, files: dict[str, str], message: str) -> None:
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", message], check=True, capture_output=True)


def _outcome(rep: Report, name: str) -> ProbeOutcome:
    return next(p for p in rep.probes if p.name == name)


def _rule(rep: Report, rule_id: str) -> list[Finding]:
    return [f for f in rep.findings if f.rule_id == rule_id]


# ---------------------------------------------------------------------------
# applicability

def test_a_repo_without_a_ledger_records_governance_as_not_applicable(tmp_path):
    """Nothing to govern is a different fact from governed and clean. The
    first leaves the coverage denominator and prints as n/a; the second is a
    probe that ran. A report must never confuse them."""
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "a.py").write_text(CLEAN_PY)
    rep = run_scan([str(bare)], load_config(None), use_adapters=False)
    oc = _outcome(rep, "governance")
    assert oc.status == "skipped"
    assert oc.applicable is False
    assert "ledger" in oc.reason
    assert "n/a" in render_console(rep, color=False).split("NOT ASSESSED", 1)[1]
    assert rep.integrity.get("violations", []) == [] or not rep.integrity

    # Control: with a ledger present the probe ran and is applicable.
    governed = tmp_path / "governed"
    governed.mkdir()
    (governed / "a.py").write_text(CLEAN_PY)
    (governed / LEDGER).parent.mkdir(parents=True)
    (governed / LEDGER).write_text(_ledger())
    rep2 = run_scan([str(governed)], load_config(None), use_adapters=False)
    oc2 = _outcome(rep2, "governance")
    assert oc2.status == "ran" and oc2.applicable is True


# ---------------------------------------------------------------------------
# open-failure-untested

def _ledger_with_three_entries() -> str:
    return _ledger(
        _entry("FAIL-001", "open", ["src/a.py"], ["tests/test_a.py::test_a"]),
        _entry("FAIL-002", "mitigated", ["src/b.py"], ["tests/test_b.py::test_b"]),
        _entry("FAIL-003", "fixed", ["src/c.py"], ["tests/test_c.py::test_c"]),
    )


def test_a_changed_affected_file_without_its_regression_test_is_reported(tmp_path):
    root = tmp_path / "r"
    base = _git_repo(root, {
        LEDGER: _ledger_with_three_entries(),
        "src/a.py": CLEAN_PY, "src/b.py": CLEAN_PY, "src/c.py": CLEAN_PY,
        "tests/test_a.py": CLEAN_PY, "tests/test_b.py": CLEAN_PY, "tests/test_c.py": CLEAN_PY,
    }, {
        "src/a.py": CLEAN_PY + "x = 1\n",            # open, test untouched -> reported
        "src/b.py": CLEAN_PY + "y = 2\n",            # mitigated, but its test moved too
        "tests/test_b.py": CLEAN_PY + "z = 3\n",
        "src/c.py": CLEAN_PY + "w = 4\n",            # fixed: not this rule's business
    })
    rep = run_scan([str(root)], load_config(None), use_adapters=False, changed_since=base)
    assert _outcome(rep, "governance").status == "ran"
    hits = _rule(rep, OPEN_RULE)
    assert [(f.location.path, f.location.logical) for f in hits] == [("src/a.py", "FAIL-001")]
    f = hits[0]
    assert f.severity == "medium" and f.dimension == "drift"
    assert f.evidence == "title of FAIL-001"
    assert "regression test" in f.remediation
    assert "outside-this-change" not in f.tags
    # Controls, spelled out: the entry whose test changed with it, and the
    # fixed entry, are both silent. The mutation harness drops the status
    # filter and expects the second of these to fail.
    assert not any(f.location.logical == "FAIL-002" for f in hits)
    assert not any(f.location.logical == "FAIL-003" for f in hits)


def test_a_full_scan_emits_no_open_failure_untested_finding(tmp_path):
    """The rule is about what a change touched. With no change there is no
    question, and an open entry is not a finding by itself."""
    root = tmp_path / "r"
    base = _git_repo(root, {
        LEDGER: _ledger(_entry("FAIL-001", "open", ["src/a.py"], ["tests/test_a.py::test_a"])),
        "src/a.py": CLEAN_PY, "tests/test_a.py": CLEAN_PY,
    }, {"src/a.py": CLEAN_PY + "x = 1\n"})
    full = run_scan([str(root)], load_config(None), use_adapters=False)
    assert full.scan_scope["mode"] == "full"
    assert _outcome(full, "governance").status == "ran"
    assert _rule(full, OPEN_RULE) == []
    # Control: the same tree, asked about the change, does report it.
    part = run_scan([str(root)], load_config(None), use_adapters=False, changed_since=base)
    assert part.scan_scope["changed_since"] == base
    assert len(_rule(part, OPEN_RULE)) == 1


# ---------------------------------------------------------------------------
# regression-test-missing

def test_a_fixed_entry_whose_regression_test_vanished_is_reported(tmp_path):
    root = tmp_path / "r"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_here.py").write_text(CLEAN_PY)
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text(CLEAN_PY)
    (root / LEDGER).parent.mkdir(parents=True)
    (root / LEDGER).write_text(_ledger(
        _entry("FAIL-001", "fixed", ["src/a.py"], ["tests/test_gone.py::test_x"]),
        _entry("FAIL-002", "fixed", ["src/a.py"], ["tests/test_here.py::test_y"]),
        _entry("FAIL-003", "open", ["src/a.py"], ["tests/test_gone.py::test_z"]),
    ))
    rep = run_scan([str(root)], load_config(None), use_adapters=False)
    hits = _rule(rep, MISSING_RULE)
    assert [(f.location.path, f.location.logical, f.evidence) for f in hits] == [
        (LEDGER, "FAIL-001", "tests/test_gone.py::test_x")]
    assert hits[0].severity == "medium" and hits[0].dimension == "drift"
    # Controls: the fixed entry whose test is present, and the open entry
    # (its missing test is a different problem, not this rule's).
    assert not any(f.location.logical == "FAIL-002" for f in hits)
    assert not any(f.location.logical == "FAIL-003" for f in hits)


# ---------------------------------------------------------------------------
# requirement attribution

def test_findings_in_a_change_carry_the_requirement_ids_cited_since_base(tmp_path):
    from arbiter.incremental import requirement_ids_since, requirement_prefix
    root = tmp_path / "r"
    base = _git_repo(root, {
        "infra/main.tf": 'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n',
        "src/a.py": CLEAN_PY,
    })
    _commit(root, {"src/b.py": LEAKY_PY}, "Add the token loader (REQ-007)\n\nAlso touches REQ-7x no, and REQ-012.")
    assert requirement_ids_since(str(root), base) == ["REQ-007", "REQ-012"]
    assert requirement_ids_since(str(root), "no-such-ref") == []
    assert requirement_ids_since(str(tmp_path / "not-a-repo"), base) == []
    assert requirement_prefix(str(root)) == "REQ"

    rep = run_scan([str(root)], load_config(None), use_adapters=False, changed_since=base)
    in_change = [f for f in rep.findings if f.location.path == "src/b.py"]
    assert in_change, "the leaky file produced no finding, so this proves nothing"
    for f in in_change:
        assert "req:REQ-007" in f.tags and "req:REQ-012" in f.tags, f.tags
    # Control: a finding in an unchanged context file is not this change's.
    outside = [f for f in rep.findings if "outside-this-change" in f.tags]
    assert outside, "no context-file finding to act as the control"
    assert not any(t.startswith("req:") for f in outside for t in f.tags)
    # Control: a full scan attributes nothing.
    full = run_scan([str(root)], load_config(None), use_adapters=False)
    assert not any(t.startswith("req:") for f in full.findings for t in f.tags)


def test_the_requirement_prefix_comes_from_the_registry(tmp_path):
    from arbiter.incremental import requirement_ids_since, requirement_prefix
    root = tmp_path / "r"
    base = _git_repo(root, {
        ".ai/requirements/requirements.json": json.dumps(
            {"version": "3.0.0", "requirement_id_prefix": "ABC", "requirements": []}),
        "src/a.py": CLEAN_PY,
    })
    _commit(root, {"src/b.py": LEAKY_PY}, "Leak something (ABC-3) and mention REQ-001")
    assert requirement_prefix(str(root)) == "ABC"
    assert requirement_ids_since(str(root), base, prefix=requirement_prefix(str(root))) == ["ABC-3"]
    rep = run_scan([str(root)], load_config(None), use_adapters=False, changed_since=base)
    tags = {t for f in rep.findings if f.location.path == "src/b.py" for t in f.tags}
    assert "req:ABC-3" in tags
    assert "req:REQ-001" not in tags


# ---------------------------------------------------------------------------
# rendering

def _report(findings: list[Finding], mode: str = "partial") -> Report:
    rep = Report(system="t", findings=findings,
                 probes=[ProbeOutcome(name="secrets", status="ran", dimensions=["security"])])
    rep.scan_scope = {"mode": mode, "basis": "changed since main", "files_read": 1,
                      "files_total": 2, "fraction_read": 0.5} if mode == "partial" else {"mode": "full"}
    rep.gate = {"passed": True, "reasons": []}
    return rep


def test_pr_comment_and_markdown_summarise_findings_by_requirement():
    attributed = Finding(rule_id="x/one", title="Credential in loader", severity="high",
                         location=Location(path="src/b.py"), evidence="SUPERSECRET-EVIDENCE",
                         tags=["req:REQ-007"])
    attributed2 = Finding(rule_id="x/two", title="Mutable action ref", severity="low",
                          location=Location(path="ci.yml"), evidence="EVIDENCE-TWO",
                          tags=["req:REQ-007"])
    unattributed = Finding(rule_id="x/three", title="Untagged in-change finding", severity="medium",
                           location=Location(path="src/c.py"), evidence="EVIDENCE-THREE")
    outside = Finding(rule_id="x/four", title="Lockfile finding nobody asked about",
                      severity="critical", location=Location(path="poetry.lock"),
                      tags=["outside-this-change"])
    rep = _report([attributed, attributed2, unattributed, outside])
    for text in (render_markdown(rep), render_pr_comment(rep)):
        assert "By requirement" in text
        block = text.split("By requirement", 1)[1].split("\n\n", 2)[1]
        assert "`REQ-007` | 0 | 1 | 0 | 1 | 0 |" in block, block
        assert "Credential in loader; Mutable action ref" in block
        assert "`unattributed` | 0 | 0 | 1 | 0 | 0 |" in block, block
        assert "Untagged in-change finding" in block
        # Titles only, never evidence; and a context-file finding is not in the change.
        assert "SUPERSECRET-EVIDENCE" not in text and "EVIDENCE-TWO" not in text
        assert "Lockfile finding nobody asked about" not in block
    # Control: nothing attributed, no block -- a report without a change to
    # read per requirement must not grow an empty table.
    plain = _report([unattributed, outside], mode="full")
    assert "By requirement" not in render_markdown(plain)
    assert "By requirement" not in render_pr_comment(plain)
