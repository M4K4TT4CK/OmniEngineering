"""Requirement attribution reads the commits that touched the file (ARB-052).

A `req:` tag used to name every requirement the round's commits cited, so a
finding in a five-requirement gate carried five tags and nobody could say
which requirement introduced it. Now the tags on a finding are the ids cited
by the commits since the base that touched its own file, marked
`req-scope:commits`; a file no such commit cited falls back to the round's
ids marked `req-scope:open`, so a reader (and the graph downstream) can tell
attribution from context. Every test here carries the control for the other
scope, because a tagger that cannot stay silent is the defect being fixed.
"""
from __future__ import annotations

import json
import subprocess

from helpers import CLEAN_PY, LEAKY_PY, _git_repo
from test_governance import _commit

from arbiter import incremental
from arbiter.core import Finding, Location, ProbeOutcome, Report
from arbiter.diff import render_pr_comment
from arbiter.engine import run_scan
from arbiter.incremental import (
    REQ_SCOPE_COMMITS,
    REQ_SCOPE_OPEN,
    cited_ids,
    requirement_ids_by_path,
    requirement_ids_since,
    requirement_registry,
)
from arbiter.policy import load_config
from arbiter.report import render_markdown

REGISTRY = ".ai/requirements/requirements.json"


def _registry(prefix: str = "ABC", aliases: dict[str, str] | None = None) -> str:
    return json.dumps({"version": "3.0.0", "requirement_id_prefix": prefix,
                       "id_aliases": aliases or {}, "requirements": []})


def _req_tags(f: Finding) -> set[str]:
    return {t for t in f.tags if t.startswith("req:")}


def _scope_tags(f: Finding) -> set[str]:
    return {t for t in f.tags if t.startswith("req-scope:")}


def _by_path(rep: Report, path: str) -> list[Finding]:
    hits = [f for f in rep.findings if f.location.path == path]
    assert hits, f"{path} produced no finding, so this proves nothing"
    return hits


def _round(tmp_path, name: str = "r"):
    """Three commits on a base: two cite different ids and touch different
    files, the third cites nothing. One of the citing commits uses an old id
    the registry aliases to the current one."""
    root = tmp_path / name
    base = _git_repo(root, {
        REGISTRY: _registry("ABC", {"OLD-7": "ABC-1"}),
        "infra/main.tf": 'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n',
        "src/keep.py": CLEAN_PY,
    })
    _commit(root, {"src/a.py": LEAKY_PY}, "Load the token (OLD-7)\n\nMentions XYZ-9 of another project.")
    _commit(root, {"src/b.py": LEAKY_PY}, "Second loader (ABC-2)\n\nsrc/a.py is named here but not touched.")
    _commit(root, {"src/c.py": LEAKY_PY}, "Tidy up, no requirement")
    return root, base


# ---------------------------------------------------------------------------
# the gate: tags come from the file's own commits

def test_a_finding_carries_only_the_ids_its_files_commits_cite(tmp_path):
    root, base = _round(tmp_path)
    rep = run_scan([str(root)], load_config(None), use_adapters=False, changed_since=base)
    for f in _by_path(rep, "src/a.py"):
        assert _req_tags(f) == {"req:ABC-1"}, f.tags          # the alias resolved, XYZ-9 dropped
        assert _scope_tags(f) == {REQ_SCOPE_COMMITS}
    for f in _by_path(rep, "src/b.py"):
        assert _req_tags(f) == {"req:ABC-2"}, f.tags
        assert _scope_tags(f) == {REQ_SCOPE_COMMITS}
    # Control: a path that a commit body merely names was not touched by it.
    assert not any("req:ABC-2" in f.tags for f in _by_path(rep, "src/a.py"))


def test_a_file_no_commit_cites_falls_back_to_the_round_marked_open(tmp_path):
    root, base = _round(tmp_path)
    rep = run_scan([str(root)], load_config(None), use_adapters=False, changed_since=base)
    for f in _by_path(rep, "src/c.py"):
        assert _req_tags(f) == {"req:ABC-1", "req:ABC-2"}, f.tags
        assert _scope_tags(f) == {REQ_SCOPE_OPEN}
    # Control: a finding in a context file the branch never touched is neither
    # attributed nor given a scope -- it is not this change's.
    outside = [f for f in rep.findings if "outside-this-change" in f.tags]
    assert outside, "no context-file finding to act as the control"
    for f in outside:
        assert _req_tags(f) == set() and _scope_tags(f) == set(), f.tags


def test_a_full_scan_carries_the_open_marker_and_no_attribution(tmp_path):
    root, _base = _round(tmp_path)
    full = run_scan([str(root)], load_config(None), use_adapters=False)
    assert full.scan_scope["mode"] == "full"
    assert full.findings, "nothing to tag"
    for f in full.findings:
        assert _req_tags(f) == set(), f.tags
        assert _scope_tags(f) == {REQ_SCOPE_OPEN}, f.tags
    assert "By requirement" not in render_markdown(full)


def test_a_round_that_cites_nothing_still_marks_the_scope(tmp_path):
    root = tmp_path / "r"
    base = _git_repo(root, {"src/keep.py": CLEAN_PY})
    _commit(root, {"src/a.py": LEAKY_PY}, "no citation at all")
    rep = run_scan([str(root)], load_config(None), use_adapters=False, changed_since=base)
    for f in _by_path(rep, "src/a.py"):
        assert _req_tags(f) == set() and _scope_tags(f) == {REQ_SCOPE_OPEN}, f.tags


# ---------------------------------------------------------------------------
# the parser and the registry

def test_ids_are_matched_on_the_registry_prefix_and_resolved_through_aliases():
    aliases = {"REQ-038": "ARB-038"}
    assert cited_ids("Tag per file (ARB-052), closes REQ-038; not FAIL-042 or REQ-7x",
                     "ARB", aliases) == {"ARB-052", "ARB-038"}
    # Controls: an unaliased foreign id is dropped; the default prefix is REQ.
    assert cited_ids("see REQ-001 and XYZ-2", "ABC") == set()
    assert cited_ids("see REQ-001 and XYZ-2") == {"REQ-001"}


def test_the_registry_gives_the_prefix_and_the_aliases(tmp_path):
    root = tmp_path / "r"
    (root / REGISTRY).parent.mkdir(parents=True)
    (root / REGISTRY).write_text(_registry("ARB", {"REQ-001": "ARB-001"}))
    assert requirement_registry(str(root)) == ("ARB", {"REQ-001": "ARB-001"})
    # Controls: no registry, and a registry that is not an object.
    assert requirement_registry(str(tmp_path / "none")) == ("REQ", {})
    (root / REGISTRY).write_text("[1, 2]")
    assert requirement_registry(str(root)) == ("REQ", {})


def test_the_log_is_read_per_path_and_bodies_cannot_forge_a_path(tmp_path):
    root, base = _round(tmp_path)
    prefix, aliases = requirement_registry(str(root))
    by_path, all_ids = requirement_ids_by_path(str(root), base, prefix, aliases)
    assert by_path == {"src/a.py": {"ABC-1"}, "src/b.py": {"ABC-2"}}
    assert all_ids == {"ABC-1", "ABC-2"}
    assert requirement_ids_since(str(root), base, prefix, aliases) == ["ABC-1", "ABC-2"]
    # Controls: an unknown ref and a directory that is not a repository.
    assert requirement_ids_by_path(str(root), "no-such-ref", prefix, aliases) == ({}, set())
    assert requirement_ids_by_path(str(tmp_path / "not-a-repo"), base) == ({}, set())


def test_the_git_log_runs_once_per_repository(tmp_path, monkeypatch):
    """The first design ran a log per changed file. One per repository,
    whatever the size of the change, is the budget."""
    repos = []
    for name in ("one", "two"):
        root, base = _round(tmp_path, name)
        # One ref must name the base in both repositories: a tag does.
        subprocess.run(["git", "-C", str(root), "tag", "base", base], check=True, capture_output=True)
        repos.append(str(root))
    real = subprocess.run
    logs: list[list[str]] = []

    def counting(args, *a, **kw):
        if isinstance(args, (list, tuple)) and "log" in args:
            logs.append(list(args))
        return real(args, *a, **kw)

    monkeypatch.setattr(incremental.subprocess, "run", counting)
    rep = run_scan(repos, load_config(None), use_adapters=False, changed_since="base")
    assert len(logs) == 2, logs
    assert all("--name-only" in call for call in logs)
    assert len({call[2] for call in logs}) == 2, "one log per repository, not two of one"
    assert sum(1 for f in rep.findings if REQ_SCOPE_COMMITS in f.tags) >= 4


# ---------------------------------------------------------------------------
# rendering: the report says which scope applied

def _report(findings: list[Finding], base: str | None = "main") -> Report:
    rep = Report(system="t", findings=findings,
                 probes=[ProbeOutcome(name="secrets", status="ran", dimensions=["security"])])
    rep.scan_scope = {"mode": "partial", "basis": f"changed since {base}", "files_read": 1,
                      "files_total": 2, "fraction_read": 0.5, "changed_since": base}
    rep.gate = {"passed": True, "reasons": []}
    return rep


def test_the_by_requirement_block_names_the_scope_and_the_base():
    attributed = Finding(rule_id="x/one", title="Credential in loader", severity="high",
                         location=Location(path="src/b.py"), evidence="SUPERSECRET-EVIDENCE",
                         tags=["req:ABC-1", REQ_SCOPE_COMMITS])
    context = Finding(rule_id="x/two", title="Mutable action ref", severity="low",
                      location=Location(path="ci.yml"), evidence="EVIDENCE-TWO",
                      tags=["req:ABC-1", "req:ABC-2", REQ_SCOPE_OPEN])
    rep = _report([attributed, context], base="abc123")
    for text in (render_markdown(rep), render_pr_comment(rep)):
        block = text.split("By requirement", 1)[1]
        assert "1 from the commits since `abc123` that touched the file (`req-scope:commits`)" in block
        assert "1 from the requirements open when the scan ran" in block
        assert "context, not attribution (`req-scope:open`)" in block
        assert "SUPERSECRET-EVIDENCE" not in text and "EVIDENCE-TWO" not in text
        # The note sits under the table, after a blank line, so a Markdown
        # renderer does not swallow it as a one-cell table row.
        table_end = block.index("| `ABC-2` |")
        assert "\n\nScope of the `req:` tags:" in block[table_end:]
    # Control: only one scope present, only that scope named.
    only = _report([attributed], base="abc123")
    text = render_markdown(only)
    assert "req-scope:commits" in text and "req-scope:open" not in text
    # Control: tags with no scope marker (an older report) get a table and no note.
    legacy = _report([Finding(rule_id="x/three", title="Old", severity="low",
                              location=Location(path="a.py"), tags=["req:ABC-3"])])
    text = render_markdown(legacy)
    assert "| `ABC-3` |" in text and "Scope of the `req:` tags" not in text
