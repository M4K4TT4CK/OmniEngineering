"""ARB-048: what the doc-drift probe must not count as drift.

Scanning Arbiter with itself, `doc_drift` reported an output file name under
an `--out` directory, every path the root `.gitignore` keeps out of a checkout
(`.claude/settings.local.json`, `.ai/project-graph.json`, `.arbiter/cache.json`)
and a handbook that lives in another repository, named beside its URL. None
of those is a file this repository is missing. A path that plainly is
missing must keep firing, or the exclusions have become a way to hide drift.

The fixture scans assert on the probe's own output -- `report.findings`, not
`.active()` -- so a suppression rule can never make these pass by accident.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from arbiter.engine import run_scan
from arbiter.policy import load_config
from arbiter.probes import _gitignored, _ignore_patterns

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "doc-drift"

MISSING = "arbiter/drift.doc-references-missing-file"
BROKEN = "arbiter/drift.broken-doc-link"


def _drift(report):
    """Every doc_drift finding, suppressed or not."""
    return [f for f in report.findings if f.probe == "doc_drift"]


def _scan_text(tmp_path: Path, name: str, content: str, cfg_extra: dict | None = None,
               out_dir: str | None = None):
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text(content, encoding="utf-8")
    cfg = dict(load_config(None))
    cfg.update(cfg_extra or {})
    return _drift(run_scan([str(tmp_path)], cfg, only=["doc_drift"], out_dir=out_dir))


# ---------------------------------------------------------------------------
# The fixture: four exclusions and one real finding
# ---------------------------------------------------------------------------

def test_fixture_reports_exactly_the_missing_file():
    found = _drift(run_scan([str(FIXTURE)], load_config(None), only=["doc_drift"]))
    assert len(found) == 1, [f.evidence for f in found]
    only = found[0]
    assert only.rule_id == MISSING
    assert only.evidence == "path=src/missing.py"
    assert only.location.path == "README.md"
    assert only.severity == "low"


def test_fixture_names_every_excluded_case_so_the_test_proves_something():
    """If somebody trims the README, the single-finding assertion above would
    pass for the wrong reason."""
    text = (FIXTURE / "README.md").read_text(encoding="utf-8")
    for needle in ("`build/report.json`", "`arbiter-out/REPORT.md`",
                   "(git://mirror.example.org/guide/docs/guide.md)", "`docs/handbook.md`",
                   "https://github.com/example/handbook", "`src/missing.py`"):
        assert needle in text, needle
    # The handbook's URL is on the line after its path, which is how the
    # self-scan's own case (`.ai/context-brief.md`) is wrapped.
    lines = text.split("\n")
    at = next(i for i, line in enumerate(lines) if "`docs/handbook.md`" in line)
    assert "https://" not in lines[at] and "https://" in lines[at + 1]


# ---------------------------------------------------------------------------
# Each exclusion on its own
# ---------------------------------------------------------------------------

def test_a_gitignored_directory_is_not_missing(tmp_path):
    (tmp_path / ".gitignore").write_text("build/\n")
    found = _scan_text(tmp_path, "README.md", "The build writes `build/report.json`.\n")
    assert not found


def test_a_gitignored_glob_is_not_missing(tmp_path):
    (tmp_path / ".gitignore").write_text("*.local.json\n")
    found = _scan_text(tmp_path, "README.md", "Copy it to `config.local.json`.\n")
    assert not found


def test_a_gitignored_anchored_path_is_not_missing(tmp_path):
    (tmp_path / ".gitignore").write_text("/.ai/project-graph.json\n")
    found = _scan_text(tmp_path, "README.md", "Rebuilt into `.ai/project-graph.json`.\n")
    assert not found


def test_a_re_included_path_is_still_checked(tmp_path):
    """`!` must not break the parse, and a path git re-includes is one a
    checkout does carry, so naming it when it is absent is still drift."""
    (tmp_path / ".gitignore").write_text(".claude/*\n!.claude/settings.json\n")
    found = _scan_text(tmp_path, "README.md",
                       "Shared: `.claude/settings.json`. Local: `.claude/settings.local.json`.\n")
    assert [f.evidence for f in found] == ["path=.claude/settings.json"]


def test_a_link_into_a_gitignored_directory_is_not_broken(tmp_path):
    (tmp_path / ".gitignore").write_text("site/\n")
    found = _scan_text(tmp_path, "README.md", "See [the rendered site](site/index.md).\n")
    assert not [f for f in found if f.rule_id == BROKEN]


def test_output_under_an_arbiter_out_segment_is_not_missing(tmp_path):
    found = _scan_text(tmp_path, "README.md",
                       "A run writes `arbiter-out/REPORT.md` and `ci/arbiter-out/self/report.json`.\n")
    assert not found


def test_output_under_the_configured_out_dir_is_not_missing(tmp_path):
    found = _scan_text(tmp_path, "README.md", "A run writes `scan-output/REPORT.md`.\n",
                       cfg_extra={"out": "scan-output"})
    assert not found


def test_output_under_this_runs_out_dir_is_not_missing(tmp_path):
    found = _scan_text(tmp_path, "README.md", "A run writes `results/REPORT.md`.\n",
                       out_dir=str(tmp_path / "results"))
    assert not found


def test_an_out_dir_outside_the_tree_excuses_nothing(tmp_path):
    found = _scan_text(tmp_path, "README.md", "A run writes `results/REPORT.md`.\n",
                       out_dir=str(tmp_path.parent / "elsewhere"))
    assert [f.evidence for f in found] == ["path=results/REPORT.md"]


def test_a_link_with_a_scheme_is_not_a_repository_path(tmp_path):
    found = _scan_text(tmp_path, "README.md",
                       "Mirror: [guide](git://mirror.example.org/guide/docs/guide.md).\n")
    assert not found


def test_a_path_beside_a_url_describes_the_other_repository(tmp_path):
    found = _scan_text(tmp_path, "README.md",
                       "Read `docs/handbook.md` in the handbook repository "
                       "(https://github.com/example/handbook).\n")
    assert not found


def test_a_path_with_its_url_wrapped_onto_the_next_line_is_excused(tmp_path):
    """The self-scan case: `.ai/context-brief.md` names the OmniEngineering
    handbook and hard-wraps before the URL."""
    found = _scan_text(tmp_path, "README.md",
                       "Read the integration handbook once: `docs/arbiter-integration.md` in the other\n"
                       "repository (https://github.com/example/other/blob/main/docs/arbiter-integration.md).\n")
    assert not found


def test_a_url_on_the_previous_line_of_the_paragraph_is_excused(tmp_path):
    found = _scan_text(tmp_path, "README.md",
                       "The handbook repository is https://github.com/example/handbook; its\n"
                       "`docs/handbook.md` explains the loop.\n")
    assert not found


def test_a_url_across_a_blank_line_excuses_nothing(tmp_path):
    """A paragraph boundary ends the search: the URL has to be part of the
    same sentence-sized stretch of prose, or every README with a badge line
    would excuse the paragraph beneath it."""
    found = _scan_text(tmp_path, "README.md",
                       "Website: https://example.org\n\nStart from `src/missing.py`.\n")
    assert [f.evidence for f in found] == ["path=src/missing.py"]


def test_a_url_two_lines_away_in_the_paragraph_excuses_nothing(tmp_path):
    found = _scan_text(tmp_path, "README.md",
                       "Start from `src/missing.py`.\n"
                       "It is small.\n"
                       "The website is https://example.org.\n")
    assert [f.evidence for f in found] == ["path=src/missing.py"]


def test_a_url_on_the_line_does_not_excuse_a_relative_link(tmp_path):
    """A relative link is a claim about this tree whatever else the line says."""
    found = _scan_text(tmp_path, "README.md",
                       "See [the guide](docs/guide.md) or https://example.org/guide.\n")
    assert [f for f in found if f.rule_id == BROKEN]


def test_a_plainly_missing_file_still_fires(tmp_path):
    (tmp_path / ".gitignore").write_text("build/\n*.log\n")
    found = _scan_text(tmp_path, "README.md", "Start from `src/missing.py`.\n",
                       cfg_extra={"out": "scan-output"})
    assert [f.evidence for f in found] == ["path=src/missing.py"]
    assert found[0].rule_id == MISSING


def test_a_missing_file_under_a_directory_that_merely_resembles_output_fires(tmp_path):
    found = _scan_text(tmp_path, "README.md", "Start from `my-arbiter-out/notes.md`.\n")
    assert [f.evidence for f in found] == ["path=my-arbiter-out/notes.md"]


# ---------------------------------------------------------------------------
# The .gitignore reader
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel, expected", [
    ("build/report.json", True),
    ("deep/build/report.json", True),
    ("debug.log", True),
    ("logs/run/debug.log", True),
    ("build", False),  # a directory pattern names directories; this is a file
    ("builder/x.py", False),
    ("docs/notes.md", True),
    ("notes.md", False),  # anchored: only at the root
    ("root-only.md", True),
    ("src/root-only.md", False),
    (".arbiter/cache.json", True),
    ("pkg/.arbiter/cache.json", True),
    (".arbiter/knowledge.json", False),
    ("training/x.json", True),
    ("training/WORKLIST.md", False),  # re-included
    ("src/kept.py", False),
])
def test_gitignored_reads_the_shapes_it_promises(tmp_path, rel, expected):
    (tmp_path / ".gitignore").write_text(
        "# comment\n"
        "\n"
        "build/\n"
        "*.log\n"
        "docs/**\n"
        "/root-only.md\n"
        "**/.arbiter/cache.json\n"
        "training/*\n"
        "!training/WORKLIST.md\n"
    )
    patterns = _ignore_patterns(tmp_path)
    assert _gitignored(tmp_path, rel, patterns) is expected
    # Without pre-parsed patterns the helper reads the file itself.
    assert _gitignored(tmp_path, rel) is expected


def test_no_gitignore_ignores_nothing(tmp_path):
    assert _ignore_patterns(tmp_path) == []
    assert _gitignored(tmp_path, "anything.py") is False
