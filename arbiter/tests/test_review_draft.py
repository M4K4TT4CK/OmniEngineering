"""ARB-050: an assistant proposes marks with reasons and never records one.

`docs/mcp.md` rules out any tool that records a verdict. `arbiter_review_draft`
is allowed because it writes a file a person reads -- the queue with proposed
marks and a reason under each -- and the only way that file reaches the
ledger is `arbiter review --apply --reviewer`, run by a person. These tests
hold the tool to that: the knowledge file is byte-identical after it runs,
`review.parse` reads back exactly the proposed marks, and the CLI applies the
draft unchanged, so the two sides share one format. The last test extends the
no-verdict rule to every tool in `TOOLS`, so a tool added later is held to it
without anyone remembering to add a test.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from arbiter import mcp, service
from arbiter.cli import main
from arbiter.engine import run_scan
from arbiter.learn import Knowledge
from arbiter.policy import load_config
from arbiter.review import parse
from arbiter.service import EXIT_OK, ServiceError

FIRST_LINE = "# Review draft: every mark below was proposed by an assistant and nothing has been recorded."


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A repository with findings, its report, and a fresh knowledge file at
    the path the CLI and the service both read (`.arbiter/knowledge.json`,
    relative to the working directory)."""
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "billing.py").write_text(
        'KEY = "sk_live_51H8xQ2LkdIwHu7ix' + "Z" * 20 + '"\n', encoding="utf-8")
    (repo / "README.md").write_text("Start from `missing.py`; see `gone.py` too.\n",
                                    encoding="utf-8")
    report = run_scan([str(repo)], load_config(None), only=["secrets", "doc_drift"])
    assert len(report.findings) >= 3, "the fixture must yield several findings to draft over"
    out = tmp_path / "out"
    out.mkdir()
    report_path = out / "report.json"
    report_path.write_text(json.dumps(report.to_dict()), encoding="utf-8")
    knowledge_path = tmp_path / ".arbiter" / "knowledge.json"
    Knowledge().save(str(knowledge_path))
    return {"root": tmp_path, "report": report, "report_path": report_path,
            "out": out, "knowledge": knowledge_path}


def _ids(ws) -> list[str]:
    return [f.id for f in ws["report"].findings]


def _tree(root: Path) -> set[str]:
    # as_posix: the assertion compares against "out/review-draft.md" on Windows too (FAIL-043).
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def _draft(ws, verdicts, **kw) -> dict:
    return service.review_draft(str(ws["report_path"]), str(ws["out"]), verdicts, **kw)


# ---------------------------------------------------------------------------
# Nothing is recorded
# ---------------------------------------------------------------------------

def test_the_knowledge_file_is_byte_identical_after_the_tool(workspace):
    ids = _ids(workspace)
    before = workspace["knowledge"].read_bytes()
    files_before = _tree(workspace["root"])
    result = _draft(workspace, [
        {"id": ids[0], "mark": "y", "reason": "a live Stripe key in source"},
        {"id": ids[1], "mark": "n", "reason": "a documentation example"},
    ])
    assert workspace["knowledge"].read_bytes() == before
    assert result["recorded"] is False
    # The only new file anywhere is the draft, under output_dir.
    assert _tree(workspace["root"]) - files_before == {"out/review-draft.md"}
    assert Knowledge.load(str(workspace["knowledge"])).adjudicated == {}


def test_no_ledger_file_is_written_for_the_y_marks(workspace):
    ids = _ids(workspace)
    result = _draft(workspace, [{"id": ids[0], "mark": "y", "reason": "real"}])
    assert result["ledger_entries_text"], "a y mark must show what it would draft"
    entries = json.loads(result["ledger_entries_text"])
    assert len(entries) == 1
    assert entries[0]["status"] == "open"
    assert ids[0] in entries[0]["how_detected"]
    assert entries[0]["title"] == workspace["report"].findings[0].title
    assert not list(workspace["root"].rglob("failure-ledger.json"))
    assert not (workspace["root"] / ".ai").exists()


# ---------------------------------------------------------------------------
# The draft is the queue, in the one format the parser reads
# ---------------------------------------------------------------------------

def test_parse_reads_back_exactly_the_proposed_marks(workspace):
    ids = _ids(workspace)
    result = _draft(workspace, [
        {"id": ids[0], "mark": "y", "reason": "real"},
        {"id": ids[1], "mark": "n", "reason": "not real"},
        {"id": ids[2], "mark": "?"},
    ])
    text = Path(result["draft_path"]).read_text(encoding="utf-8")
    # `?` and blank are both "skip" to the parser, so exactly the y and n come back.
    assert parse(text) == {ids[0]: "true_positive", ids[1]: "false_positive"}
    assert result["proposed"] == {"y": 1, "n": 1, "?": 1}
    assert result["entry_count"] == len(ids)
    assert result["draft_path"] == str(workspace["out"] / "review-draft.md")
    assert result["draft_markdown"] == text


def test_unlisted_findings_stay_blank(workspace):
    ids = _ids(workspace)
    result = _draft(workspace, [{"id": ids[0], "mark": "y", "reason": "real"}])
    text = Path(result["draft_path"]).read_text(encoding="utf-8")
    blank = [line for line in text.split("\n") if line.startswith("[ ] f:")]
    assert len(blank) == len(ids) - 1
    assert f"[y] {ids[0]}" in text


def test_the_reason_sits_on_the_line_under_its_finding(workspace):
    ids = _ids(workspace)
    result = _draft(workspace, [
        {"id": ids[0], "mark": "y", "reason": "a live key;\n  rotate it"},
    ])
    lines = Path(result["draft_path"]).read_text(encoding="utf-8").split("\n")
    at = next(i for i, line in enumerate(lines) if line.startswith(f"[y] {ids[0]}"))
    assert lines[at + 1] == "    reason: a live key; rotate it"
    # The reason is indented, so it can never be read as a mark line.
    assert parse("\n".join(lines[at + 1:at + 2])) == {}


def test_the_header_first_line_says_nothing_has_been_recorded(workspace):
    ids = _ids(workspace)
    result = _draft(workspace, [{"id": ids[0], "mark": "y", "reason": "real"}])
    text = Path(result["draft_path"]).read_text(encoding="utf-8")
    first = text.split("\n", 1)[0]
    assert first == FIRST_LINE
    assert "proposed by an assistant" in first and "nothing has been recorded" in first
    assert "arbiter review" in text and "--reviewer" in text
    assert str(workspace["report_path"]) in text


def test_the_cli_applies_the_draft_unchanged(workspace):
    """The formats agree: what the tool writes, `arbiter review --apply`
    reads, with a person's name on it."""
    ids = _ids(workspace)
    result = _draft(workspace, [
        {"id": ids[0], "mark": "y", "reason": "real"},
        {"id": ids[1], "mark": "n", "reason": "not real"},
        {"id": ids[2], "mark": "?"},
    ])
    code = main(["review", str(workspace["report_path"]), "--apply", result["draft_path"],
                 "--reviewer", "tester", "--knowledge", str(workspace["knowledge"])])
    assert code == EXIT_OK
    recorded = Knowledge.load(str(workspace["knowledge"])).adjudicated
    assert {fid: v.verdict for fid, v in recorded.items()} == {
        ids[0]: "true_positive", ids[1]: "false_positive"}
    assert all(v.reviewer == "tester" for v in recorded.values())


def test_the_draft_draws_the_same_queue_as_review_queue(workspace):
    """Same selection: the ids in the draft are the ids in the blank queue,
    in the same order."""
    queue = service.review_queue(str(workspace["report_path"]), str(workspace["out"]), limit=2)
    draft = _draft(workspace, [], limit=2)
    def ids_in(text):
        return [line.split()[1] for line in text.split("\n") if line.startswith("[")]
    assert ids_in(queue["queue_markdown"]) == ids_in(draft["draft_markdown"])
    assert draft["entry_count"] == queue["entry_count"] == 2


# ---------------------------------------------------------------------------
# Refusals and reports
# ---------------------------------------------------------------------------

def test_unknown_ids_are_reported_not_raised(workspace):
    ids = _ids(workspace)
    result = _draft(workspace, [
        {"id": ids[0], "mark": "y", "reason": "real"},
        {"id": "f:ffffffffffff", "mark": "n", "reason": "not in this report"},
    ])
    assert result["unknown_ids"] == ["f:ffffffffffff"]
    assert result["proposed"] == {"y": 1, "n": 0, "?": 0}
    assert "f:ffffffffffff" not in result["draft_markdown"]


@pytest.mark.parametrize("verdicts, why", [
    ("not a list", "must be a list"),
    ([{"id": "f:abc", "mark": "x", "reason": "r"}], "mark must be"),
    ([{"id": "f:abc", "mark": "y"}], "needs a reason"),
    ([{"id": "", "mark": "y", "reason": "r"}], "has no id"),
    ([{"id": "f:abc", "mark": "y", "reason": "r"}, {"id": "f:abc", "mark": "n", "reason": "r"}],
     "listed twice"),
])
def test_malformed_proposals_are_refused(workspace, verdicts, why):
    with pytest.raises(ServiceError, match=why):
        _draft(workspace, verdicts)
    assert not (workspace["out"] / "review-draft.md").exists()


def test_a_missing_report_is_refused(workspace):
    with pytest.raises(ServiceError, match="no report at"):
        service.review_draft(str(workspace["out"] / "nope.json"), str(workspace["out"]), [])


def test_nothing_to_review_writes_no_draft(workspace):
    ids = _ids(workspace)
    result = _draft(workspace, [{"id": ids[0], "mark": "y", "reason": "real"}],
                    rule="no-such-rule")
    assert result["draft_path"] == "" and result["entry_count"] == 0
    assert result["unknown_ids"] == [ids[0]]
    assert not (workspace["out"] / "review-draft.md").exists()


# ---------------------------------------------------------------------------
# The MCP surface
# ---------------------------------------------------------------------------

def test_the_tool_is_dispatched_and_confined_like_the_others(workspace):
    ids = _ids(workspace)
    root = workspace["root"]
    result = mcp.dispatch("arbiter_review_draft", {
        "report_path": "out/report.json", "output_dir": "drafts",
        "verdicts": [{"id": ids[0], "mark": "y", "reason": "real"}],
    }, root=root)
    assert Path(result["draft_path"]) == root / "drafts" / "review-draft.md"
    with pytest.raises(ServiceError):
        mcp.dispatch("arbiter_review_draft", {
            "report_path": "out/report.json", "output_dir": "/",
            "verdicts": []}, root=root)


def test_the_no_verdict_rule_covers_every_tool(workspace):
    """The no-verdict test from the service layer, extended to the draft tool:
    every tool in TOOLS leaves the knowledge file byte-identical. A tool
    added without arguments here fails, so it cannot slip past the rule."""
    ids = _ids(workspace)
    report_path = str(workspace["report_path"])
    arguments = {
        "arbiter_scan": {"target": str(workspace["root"] / "repo"),
                         "output_dir": str(workspace["out"] / "scan"), "only": "secrets"},
        "arbiter_gate": {"target": str(workspace["root"] / "repo"),
                         "output_dir": str(workspace["out"] / "gate"), "only": "secrets"},
        "arbiter_review_queue": {"report_path": report_path,
                                 "output_dir": str(workspace["out"] / "queue")},
        "arbiter_review_draft": {"report_path": report_path,
                                 "output_dir": str(workspace["out"] / "draft"),
                                 "verdicts": [{"id": ids[0], "mark": "y", "reason": "real"}]},
    }
    assert set(arguments) == {tool["name"] for tool in mcp.TOOLS} == set(mcp.HANDLERS), \
        "a new tool must be exercised here"
    before = workspace["knowledge"].read_bytes()
    for name, args in arguments.items():
        mcp.dispatch(name, args)
        assert workspace["knowledge"].read_bytes() == before, f"{name} changed the ledger"
    assert set(service.OPERATIONS) == {"scan", "gate", "review_queue", "review_draft"}
    assert set(mcp.HANDLERS) == {"arbiter_scan", "arbiter_gate", "arbiter_review_queue",
                                 "arbiter_review_draft"}
    for name in dir(service):
        assert "apply" not in name.lower(), f"service grew {name}"
        assert "adjudicat" not in name.lower(), f"service grew {name}"
    public = {n for n in dir(mcp) if not n.startswith("_")}
    for banned in ("record", "apply", "adjudicate", "verdict", "feedback"):
        assert not any(banned in n.lower() for n in public), f"{banned} is reachable"
    for tool in mcp.TOOLS:
        assert "apply" not in json.dumps(tool["inputSchema"]).lower()
        for field in tool["inputSchema"]["properties"]:
            if field.endswith(("_dir", "_path")) or field == "target":
                assert field in mcp.PATH_ARGUMENTS, f"{tool['name']}.{field} is not confined"
