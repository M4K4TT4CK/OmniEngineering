"""Invariants that must hold for every finding, not for one remembered case.

Three separate requirements fixed a missing `repo_id` three separate times.
REQ-014 pinned the rendering layer, REQ-015 found it was every probe, and
REQ-016 found it again at the construction sites, where a flattened
collection had also let one repository's LICENSE satisfy the rule for all of
them. Each fix arrived with a regression test, and each regression test was
about the one call site its bug came from, so none of them caught the next
one. The path separator is the same story: REQ-008 stripped a dot-prefixed
path with `lstrip("./")`, REQ-029 found adapter output arriving with
backslashes, and the boundary between them was never stated.

So these tests assert over the whole output of a scan rather than over a
case. A new probe, a new adapter, or a new construction site is covered the
day it is written, without anyone remembering to cover it.

They run on the committed fixtures, so they need no network and belong in the
blocking pull-request check.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from arbiter.core import Finding, Location

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "arbiter"


@pytest.fixture(scope="session")
def legacy_findings(legacy_report):
    return legacy_report.findings


@pytest.fixture(scope="session")
def system_findings(system_report):
    return system_report.findings


# --------------------------------------------------------------------------
# Path shape
# --------------------------------------------------------------------------

def test_no_finding_anywhere_carries_a_backslash_path(legacy_findings, system_findings):
    """A path is one format everywhere or identity is not stable.

    `fingerprint()` hashes `location.path`, adjudications are keyed by that
    fingerprint, and `record()` refuses to re-adjudicate. A finding that
    reports `a\\b.tf` on Windows and `a/b.tf` elsewhere is therefore two
    permanent identities for one defect, and a verdict recorded on one
    platform can never be matched on the other.
    """
    offenders = [f"{f.rule_id} -> {f.location.path}"
                 for f in legacy_findings + system_findings
                 if "\\" in f.location.path]
    assert not offenders, f"findings carrying backslash paths: {offenders[:10]}"


def test_no_finding_reports_an_absolute_path(legacy_findings, system_findings):
    """Absolute paths leak the scanning machine into a shared baseline.

    They also never match between a developer's checkout and CI, so the
    baseline they are compared against reports everything as new.
    """
    offenders = []
    for f in legacy_findings + system_findings:
        p = f.location.path
        if not p:
            continue
        if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
            offenders.append(f"{f.rule_id} -> {p}")
    assert not offenders, f"findings carrying absolute paths: {offenders[:10]}"


def test_no_finding_points_outside_the_repository(legacy_findings, system_findings):
    offenders = [f"{f.rule_id} -> {f.location.path}"
                 for f in legacy_findings + system_findings
                 if f.location.path.startswith("../") or "/../" in f.location.path]
    assert not offenders, f"findings escaping the repo root: {offenders[:10]}"


def test_location_normalizes_separators_at_construction():
    """The boundary itself, stated directly.

    Normalizing at each producer is what was tried; it was missed four times.
    """
    assert Location(path="a" + chr(92) + "b" + chr(92) + "c.tf").path == "a/b/c.tf"


def test_the_same_finding_has_one_fingerprint_on_either_platform():
    posix = Finding(rule_id="r", title="t", evidence="e",
                    location=Location(path="infra/main.tf"))
    windows = Finding(rule_id="r", title="t", evidence="e",
                      location=Location(path="infra" + chr(92) + "main.tf"))
    assert posix.id == windows.id


# --------------------------------------------------------------------------
# Attribution
# --------------------------------------------------------------------------

def test_every_finding_in_a_system_scan_names_its_repository(system_findings):
    """REQ-016's own acceptance criterion, made standing.

    Without it a cross-repo report renders bare paths, and two repositories
    holding a file of the same name are indistinguishable in the output a
    person adjudicates from.
    """
    unattributed = [f"{f.rule_id} -> {f.location.path}"
                    for f in system_findings if not f.location.repo_id]
    assert not unattributed, (
        f"{len(unattributed)} finding(s) with a blank location.repo_id: "
        f"{unattributed[:10]}")


def test_reported_repo_ids_are_repositories_that_exist(system_findings):
    """A repo_id that names nothing is attribution in form only.

    REQ-016's other half: an IAM-grant finding attributed itself to whichever
    infra repo sorted first, which is a real-looking answer and the wrong one.
    """
    known = {f.repo_id for f in system_findings if f.repo_id}
    stray = {f.location.repo_id for f in system_findings
             if f.location.repo_id and f.location.repo_id not in known}
    assert not stray, f"location.repo_id naming unknown repositories: {sorted(stray)}"


# --------------------------------------------------------------------------
# Encoding, at the source rather than per incident
# --------------------------------------------------------------------------

def _calls_needing_encoding(tree: ast.AST):
    """Yield text reads and writes that never name an encoding.

    Only the builtin `open` counts, never an attribute call that happens to
    share the name: `tarfile.open`, `zipfile.open` and `webbrowser.open` take
    no encoding and are not text. `read_text`/`write_text` are Path methods
    and always accept one.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name != "open":
                continue
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if name not in ("read_text", "write_text"):
                continue
        else:
            continue
        if any(kw.arg == "encoding" for kw in node.keywords):
            continue
        # Binary mode takes no encoding and cannot mis-decode.
        if any(isinstance(a, ast.Constant) and isinstance(a.value, str) and "b" in a.value
               for a in node.args):
            continue
        yield name, node.lineno


def test_every_text_read_and_write_names_its_encoding():
    """REQ-009 and REQ-012 were the same mistake in opposite directions.

    Writing Arbiter's own artifacts under the platform default produced
    undecodable output on Windows; reading scanned files under it silently
    mis-decoded nearly every byte as cp1252, turning an em dash into three
    characters without ever raising. `errors="replace"` does not save you --
    it is what makes the failure quiet.

    A regression test per incident would cover those two call sites. This
    covers the ones not written yet.
    """
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, line in _calls_needing_encoding(tree):
            offenders.append(f"{path.relative_to(ROOT)}:{line} {name}()")
    assert not offenders, (
        "text I/O without an explicit encoding= (platform default decides, and "
        "on Windows that is cp1252):\n  " + "\n  ".join(offenders))
