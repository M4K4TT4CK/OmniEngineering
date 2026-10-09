"""Documentation drift and contract drift: links, prose paths, env vars,
routes and schema columns declared in one place and used in another.
"""
from __future__ import annotations

import pytest
from helpers import _scan_text

from arbiter.engine import run_scan
from arbiter.policy import load_config


def test_relative_parent_links_resolve(tmp_path):
    """terraform-aws-modules: `[examples](../examples)` from .github/ was
    reported broken because '../' was being mangled into '.'."""
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "main.tf").write_text("# x\n")
    found = _scan_text(tmp_path, ".github/contributing.md",
                       "See [the examples](../examples) for usage.\n", ["doc_drift"])
    assert not [f for f in found if "broken-doc-link" in f.rule_id]


def test_rendered_site_links_are_not_checked(tmp_path):
    """terraform-provider-random docs link to ../index.html, which only exists
    on the published website. 36 findings came from this one pattern."""
    found = _scan_text(tmp_path, "docs/resources/id.md",
                       "Back to [the index](../index.html).\n", ["doc_drift"])
    assert not [f for f in found if "broken-doc-link" in f.rule_id]


def test_genuinely_broken_link_still_found(tmp_path):
    found = _scan_text(tmp_path, "README.md", "See [design](design.md).\n", ["doc_drift"])
    assert [f for f in found if "broken-doc-link" in f.rule_id]


def test_dotted_paths_in_prose_resolve(tmp_path):
    """A path named in prose was normalized with lstrip('./'), which strips a
    character set rather than a prefix: `.ai/context-brief.md` collapsed to
    `ai/context-brief.md` and matched nothing. Scanning arbiter with itself,
    159 of this rule's 187 findings came from that one line."""
    (tmp_path / ".ai").mkdir()
    (tmp_path / ".ai" / "context-brief.md").write_text("# brief\n")
    found = _scan_text(tmp_path, "README.md",
                       "Read `.ai/context-brief.md` first.\n", ["doc_drift"])
    assert not [f for f in found if "doc-references-missing-file" in f.rule_id]


def test_missing_file_in_prose_still_found(tmp_path):
    found = _scan_text(tmp_path, "README.md",
                       "Read `.ai/context-brief.md` first.\n", ["doc_drift"])
    assert [f for f in found if "doc-references-missing-file" in f.rule_id]


def test_a_removed_file_named_in_prose_is_history_not_drift(tmp_path):
    """CHANGELOG.md and the decision log name files that were deliberately
    deleted or renamed; 24 self-scan findings said they were missing. Git
    knows the difference between a path that was never there and one that
    was removed, so the probe asks it and reports the second kind as
    information, not as a stale document."""
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    (tmp_path / "old.py").write_text("x = 1\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    import os
    env = {**os.environ, **env}
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "add"], cwd=str(tmp_path), check=True, env=env)
    (tmp_path / "old.py").unlink()
    subprocess.run(["git", "commit", "-q", "-am", "remove"], cwd=str(tmp_path), check=True, env=env)
    found = _scan_text(tmp_path, "CHANGELOG.md",
                       "Removed `old.py`; `never.py` was a typo.\n", ["doc_drift"])
    by_path = {f.evidence.split()[0]: f for f in found
               if "doc-references-missing-file" in f.rule_id}
    assert by_path["path=old.py"].severity == "info"
    assert "removed" in by_path["path=old.py"].title
    assert by_path["path=never.py"].severity == "low"
    assert "not in the repository" in by_path["path=never.py"].title


def test_prose_path_escaping_the_repository_is_not_checked(tmp_path):
    """`../../other/thing.py` names a file outside the repository, which this
    scan cannot speak to either way."""
    found = _scan_text(tmp_path, "README.md",
                       "See `../../other/thing.py` in the sibling repo.\n", ["doc_drift"])
    assert not [f for f in found if "doc-references-missing-file" in f.rule_id]


def test_documented_file_in_a_skipped_directory_is_not_missing(tmp_path):
    """`.arbiter` is in SKIP_DIRS, so its tracked files never enter the
    inventory. Checking prose against the inventory alone called every one of
    them missing: 9 of 31 doc-drift findings when arbiter scanned itself."""
    (tmp_path / ".arbiter").mkdir()
    (tmp_path / ".arbiter" / "knowledge.json").write_text("{}\n")
    found = _scan_text(tmp_path, "README.md",
                       "Calibration lives in `.arbiter/knowledge.json`.\n", ["doc_drift"])
    assert not [f for f in found if "doc-references-missing-file" in f.rule_id]


def test_link_into_a_skipped_directory_is_not_broken(tmp_path):
    """The sibling link rule had the identical defect fifteen lines away."""
    (tmp_path / ".arbiter").mkdir()
    (tmp_path / ".arbiter" / "baseline.json").write_text("{}\n")
    found = _scan_text(tmp_path, "README.md",
                       "See [the baseline](.arbiter/baseline.json).\n", ["doc_drift"])
    assert not [f for f in found if "broken-doc-link" in f.rule_id]


def test_doc_drift_findings_name_their_repository(tmp_path):
    """`Location.short()` builds its `repo:path` prefix from the Location, not
    from the Finding. doc_drift set the id on the Finding alone, and the three
    sibling probes set it on both. Scanning the 36-repository corpus, all 330
    drift findings rendered as bare paths — `python/stepfunctions/README.md`,
    when cdk-examples and k8s-examples each have a `python/` tree. A review
    queue is read by a person, so an unattributable line is an unadjudicable
    one."""
    found = _scan_text(tmp_path, "README.md",
                       "See [design](design.md) and `missing.py`.\n", ["doc_drift"])
    drift = [f for f in found if f.rule_id.startswith("arbiter/drift.")]
    assert drift
    for f in drift:
        assert f.location.repo_id == f.repo_id != ""
        assert f.location.short().startswith(f.repo_id + ":")


def test_env_vars_only_checked_inside_a_config_section(tmp_path):
    """`BEGIN_TF_DOCS` in a template marker and `DEBUG_FD` in a changelog were
    both reported as undocumented environment variables."""
    (tmp_path / "app.py").write_text("import os\n")
    found = _scan_text(tmp_path, "README.md",
                       "# Usage\n\nRun it with BEGIN_TF_DOCS markers in place.\n", ["doc_drift"])
    assert not [f for f in found if "documented-env-var" in f.rule_id]


def test_env_var_in_a_config_section_is_checked(tmp_path):
    (tmp_path / "app.py").write_text("import os\nx = os.environ['KNOWN_VAR']\n")
    found = _scan_text(tmp_path, "README.md",
                       "## Environment variables\n\n- `KNOWN_VAR` — used\n- `GHOST_VAR` — not used\n",
                       ["doc_drift"])
    hits = [f for f in found if "documented-env-var" in f.rule_id]
    assert len(hits) == 1 and "GHOST_VAR" in hits[0].evidence


# ---------------------------------------------------------------------------
# Contracts declared in one artifact and implemented in another.
#
# A repository usually holds two descriptions of the same thing, in different
# languages, checked against each other by nobody. Each half is valid on its
# own terms — the spec parses, the routes compile — and no linter compares
# them, because each tool sees one side.
# ---------------------------------------------------------------------------

def test_a_documented_route_with_no_registration_is_reported(tmp_path):
    (tmp_path / "openapi.yaml").write_text(
        "openapi: 3.0.0\npaths:\n"
        "  /users:\n    get: {}\n"
        "  /orders:\n    get: {}\n"
        "  /health:\n    get: {}\n"
        "  /items:\n    get: {}\n")
    (tmp_path / "server.js").write_text(
        "app.get('/users', h)\napp.get('/health', h)\napp.get('/items', h)\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["contract"],
                   use_adapters=False)
    hits = [f for f in rep.active() if "documented-route-not-registered" in f.rule_id]
    assert len(hits) == 1 and hits[0].location.logical == "/orders"


@pytest.mark.parametrize("spec,code,label", [
    ("/users/{id}", "app.get('/users/:id', h)", "express colon param"),
    ("/users/{id}", "@app.get('/users/<int:id>')", "flask angle param"),
    ("/users/{id}", 'r.Get("/users/{id}", h)', "go brace param"),
    ("/users/", "app.get('/users', h)", "trailing slash"),
])
def test_path_parameters_are_normalized_across_frameworks(tmp_path, spec, code, label):
    """OpenAPI templates a parameter as {id}; frameworks spell the same thing
    five ways. Without normalizing, every parameterized route looks missing."""
    d = tmp_path / label.replace(" ", "_")
    d.mkdir()
    (d / "openapi.yaml").write_text(
        f"openapi: 3.0.0\npaths:\n  {spec}:\n    get: {{}}\n"
        "  /a:\n    get: {}\n  /b:\n    get: {}\n  /c:\n    get: {}\n")
    ext = "py" if "@app" in code else ("go" if "r.Get" in code else "js")
    (d / f"server.{ext}").write_text(
        code + "\napp.get('/a', h)\napp.get('/b', h)\napp.get('/c', h)\n")
    rep = run_scan([str(d)], load_config(None), only=["contract"], use_adapters=False)
    missing = [f.location.logical for f in rep.active()
               if "documented-route-not-registered" in f.rule_id]
    assert not missing, f"{label}: {missing}"


def test_an_unreadable_route_table_is_not_assessed_rather_than_all_missing(tmp_path):
    """Saying 'the code implements none of the spec' would be a parser
    limitation wearing the costume of a finding."""
    (tmp_path / "openapi.yaml").write_text(
        "openapi: 3.0.0\npaths:\n  /a:\n    get: {}\n  /b:\n    get: {}\n")
    (tmp_path / "server.js").write_text(
        "const base = '/api'\napp.get(`${base}/a`, h)\napp.get(base + '/b', h)\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["contract"],
                   use_adapters=False)
    assert not [f for f in rep.active() if "not-registered" in f.rule_id]
    na = [f for f in rep.active() if "spec-not-compared" in f.rule_id]
    assert na and na[0].severity == "info" and "NOT checked" in na[0].description


def test_a_dropped_column_still_named_in_code_is_reported(tmp_path):
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0002_drop.py").write_text(
        "operations = [\n    migrations.RemoveField(model_name='faang', name='about'),\n]\n")
    (tmp_path / "views.py").write_text("args = {'about': row.about}\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["contract"],
                   use_adapters=False)
    hits = [f for f in rep.active() if "dropped-column-still-referenced" in f.rule_id]
    assert hits
    assert hits[0].confidence == "low", \
        "a bare name match cannot tell a column from a variable of the same name"
    assert hits[0].related, "the finding must cite the migration as well"


def test_a_column_dropped_then_re_added_is_not_reported(tmp_path):
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0002_drop.py").write_text(
        "migrations.RemoveField(model_name='faang', name='about')\n")
    (tmp_path / "migrations" / "0003_back.py").write_text(
        "migrations.AddField(model_name='faang', name='about')\n")
    (tmp_path / "views.py").write_text("x = row.about\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["contract"],
                   use_adapters=False)
    assert not [f for f in rep.active() if "dropped-column" in f.rule_id]


def test_a_repository_with_no_spec_produces_nothing(tmp_path):
    (tmp_path / "server.js").write_text("app.get('/a', h)\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["contract"],
                   use_adapters=False)
    assert not rep.active()
