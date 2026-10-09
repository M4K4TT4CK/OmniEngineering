"""Incremental scanning: what a partial scan may and may not claim about
the repository, and probe scopes.
"""
from __future__ import annotations

import pytest
from helpers import CLEAN_PY, LEAKY_PY, _git_repo

from arbiter.engine import run_scan
from arbiter.policy import load_config

# ===========================================================================
# Incremental scanning
#
# The speed is the easy half. The half worth testing is that a scan which read
# 199 of 2,293 files cannot produce a sentence anyone could quote as being
# about the repository.
# ===========================================================================

def test_every_probe_declares_a_scope_we_understand():
    from arbiter.adapters import register_adapters
    from arbiter.probes import REGISTRY
    register_adapters()
    bad = [p.name for p in REGISTRY if p.scope not in ("file", "repo", "change")]
    assert bad == [], f"probes with an unrecognised scope: {bad}"


def test_every_shipped_adapter_declares_its_scope_rather_than_inheriting_one():
    """An inherited default is not a decision. The value must be right *and*
    written down, because the next person to add an adapter copies a manifest
    and needs to see that the question was asked (REQ-022)."""
    from arbiter.adapters import PACKS, _toml_loads
    manifests = sorted(PACKS.glob("*.adapter.toml"))
    assert len(manifests) == 5, f"expected five shipped adapters, found {len(manifests)}"
    for p in manifests:
        data = _toml_loads(p.read_text(encoding="utf-8"))
        assert "scope" in data, f"{p.name} does not declare a scope"
        # Nothing has measured subset-exactness for any external analyzer, so
        # nothing may claim it. This assertion is the evidence gate: it fails
        # the day someone declares "file" without a measurement to point at.
        assert data["scope"] == "repo", (
            f"{p.name} claims scope {data['scope']!r}; that asserts the tool "
            "returns identical findings from a file subset, which has never "
            "been measured for any of these")


def test_an_adapter_backed_probe_carries_the_scope_its_manifest_declared():
    from arbiter.adapters import SCOPE_REASON, load_all, register_adapters
    from arbiter.probes import REGISTRY
    register_adapters()
    declared = {a.name: a.scope for a in load_all()}
    seen = {p.name: p for p in REGISTRY if p.name in declared}
    assert set(seen) == set(declared), "an adapter registered no probe"
    for name, probe in seen.items():
        assert probe.scope == declared[name]
        # And it must not borrow the native probes' explanation, which is a
        # different and untrue claim about why it was held back.
        assert probe.scope_reason == SCOPE_REASON
        assert "relationships between files" not in probe.scope_reason


def test_an_adapter_with_an_unrecognised_scope_is_refused():
    from arbiter.adapters import _scope_of
    assert _scope_of({"name": "x"}) == "repo"
    assert _scope_of({"name": "x", "scope": "file"}) == "file"
    with pytest.raises(ValueError, match="must be 'file' or 'repo'"):
        _scope_of({"name": "x", "scope": "Repo"})


def test_a_partial_scan_does_not_run_repo_scoped_probes(tmp_path):
    base = _git_repo(tmp_path / "r", {"a.py": CLEAN_PY}, {"b.py": LEAKY_PY})
    rep = run_scan([str(tmp_path / "r")], load_config(None), use_adapters=False,
                   changed_since=base)
    assert rep.scan_scope["mode"] == "partial"
    from arbiter.probes import REGISTRY
    repo_scoped = {p.name for p in REGISTRY if p.scope == "repo"}
    for oc in rep.probes:
        if oc.name in repo_scoped:
            assert oc.status == "skipped"
            assert "cannot answer from a subset" in oc.reason


def test_a_partial_scan_withholds_the_grade(tmp_path):
    base = _git_repo(tmp_path / "r", {"a.py": CLEAN_PY}, {"b.py": CLEAN_PY})
    rep = run_scan([str(tmp_path / "r")], load_config(None), use_adapters=False,
                   changed_since=base)
    assert rep.scorecard.withheld is True
    assert rep.scorecard.overall is None
    assert "partial scan" in rep.scorecard.withheld_reason


def test_a_partial_scan_claims_nothing_complete_about_the_repository(tmp_path):
    base = _git_repo(tmp_path / "r", {"a.py": CLEAN_PY}, {"b.py": CLEAN_PY})
    rep = run_scan([str(tmp_path / "r")], load_config(None), use_adapters=False,
                   changed_since=base)
    assert rep.integrity["ok"], rep.integrity["violations"]
    complete = [c["id"] for c in rep.claims if c["scope"] == "complete"]
    # Only the coverage measurement, which is a statement ABOUT the
    # incompleteness rather than a claim weakened by it.
    assert complete == ["coverage"], complete
    for c in rep.claims:
        if c["kind"] == "probe_clean":
            assert "found nothing in the files it was given" in c["statement"]


def test_ci_11_catches_a_partial_scan_that_claims_completeness(tmp_path):
    """The invariant, not the code path, is what makes this safe to ship."""
    base = _git_repo(tmp_path / "r", {"a.py": CLEAN_PY}, {"b.py": CLEAN_PY})
    rep = run_scan([str(tmp_path / "r")], load_config(None), use_adapters=False,
                   changed_since=base)
    from arbiter.claims import verify
    # Forge the report a naive implementation would produce: partial scan,
    # every claim asserted as though the whole repository had been read.
    for c in rep.claims:
        c["scope"] = "complete"
    import arbiter.claims as cl
    saved = cl.build_claims
    cl.build_claims = lambda r: [cl.Claim(
        id=c["id"], kind=c["kind"], statement=c["statement"], scope="complete",
        basis=c["basis"], abstained=[]) for c in rep.claims]
    try:
        violations = verify(rep, load_config(None))
    finally:
        cl.build_claims = saved
    assert any(v.invariant == "CI-11" for v in violations)


def test_partial_and_full_agree_exactly_on_the_files_both_read(tmp_path):
    """scope='file' is a claim of exactness, not an approximation.

    If a file-scoped probe gave a different answer with fewer files present,
    it was mislabelled and the partial scan would be quietly wrong.
    """
    root = tmp_path / "r"
    base = _git_repo(root, {
        "keep.py": LEAKY_PY,
        "infra/main.tf": 'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n',
    }, {"changed.py": LEAKY_PY.replace("b" * 36, "c" * 36)})
    full = run_scan([str(root)], load_config(None), use_adapters=False)
    part = run_scan([str(root)], load_config(None), use_adapters=False,
                    changed_since=base)
    from arbiter.incremental import git_changed, narrow
    from arbiter.inventory import acquire_one, build_inventory
    info, _ = acquire_one(str(root))
    paths, note = git_changed(str(root), base)
    assert note == ""
    nar, _ = narrow(build_inventory([info]), {"root": paths})
    read = {f.path for f in nar.files}
    from arbiter.probes import REGISTRY
    fs = {p.name for p in REGISTRY if p.scope == "file"}
    F = {f.id for f in full.findings if f.probe in fs and f.location.path in read}
    P = {f.id for f in part.findings if f.probe in fs}
    assert F == P, f"missing={F - P} extra={P - F}"
    assert P, "the fixture produced no file-scoped findings, so this proves nothing"


def test_a_ref_that_does_not_exist_refuses_rather_than_scanning_nothing(tmp_path):
    """An empty diff and a failed diff look identical downstream.

    Reporting the second as a clean partial scan would be the worst possible
    failure: a green gate that read no files at all.
    """
    _git_repo(tmp_path / "r", {"a.py": LEAKY_PY})
    with pytest.raises(RuntimeError) as e:
        run_scan([str(tmp_path / "r")], load_config(None), use_adapters=False,
                 changed_since="no-such-ref")
    assert "not a commit" in str(e.value)


def test_a_non_git_target_refuses_incremental_scanning(tmp_path):
    (tmp_path / "a.py").write_text(LEAKY_PY)
    with pytest.raises(RuntimeError) as e:
        run_scan([str(tmp_path)], load_config(None), use_adapters=False,
                 changed_since="main")
    assert "not a git repository" in str(e.value)


def test_context_files_are_kept_but_ordinary_config_is_not(tmp_path):
    """The first version of this kept every yaml and read 55% of Traefik."""
    from arbiter.incremental import is_context
    from arbiter.inventory import FileInfo, classify

    def fi(path):
        lang = {"yml": "yaml", "yaml": "yaml", "json": "json", "tf": "terraform",
                "mod": "unknown", "txt": "unknown"}.get(path.rsplit(".", 1)[-1], "unknown")
        return FileInfo(path=path, abspath="", repo_id="root", language=lang,
                        role=classify(path, lang))

    for p in ("go.mod", "package.json", "requirements.txt", "infra/main.tf",
              ".github/workflows/ci.yml", "Dockerfile", "poetry.lock"):
        assert is_context(fi(p)), f"{p} must be kept: a probe reasons across it"
    for p in ("integration/fixtures/a.yml", "testdata/big.json",
              "deploy/k8s/service.yaml", "docs/conf.yaml"):
        assert not is_context(fi(p)), (
            f"{p} was kept; it is an ordinary config file with its own "
            "pre-existing findings, not context for the changed ones")


def test_only_files_is_a_partial_scan_too(tmp_path):
    (tmp_path / "a.py").write_text(LEAKY_PY)
    (tmp_path / "b.py").write_text(LEAKY_PY.replace("b" * 36, "d" * 36))
    rep = run_scan([str(tmp_path)], load_config(None), use_adapters=False,
                   only_files=["a.py"])
    assert rep.scan_scope["mode"] == "partial"
    assert rep.scorecard.withheld is True
    paths = {f.location.path for f in rep.active() if f.probe == "secrets"}
    assert paths == {"a.py"}, paths


def test_findings_in_untouched_context_files_are_tagged(tmp_path):
    """A pull request must not be blamed for a lockfile it did not touch.

    Context files are read so the file-scoped probes can reason, not because
    anybody asked about them. Findings landing there are real and stay in the
    report; they are tagged so a reader -- and anyone tuning a gate -- can tell
    them apart from what the change introduced.
    """
    root = tmp_path / "r"
    base = _git_repo(root, {
        "infra/main.tf": 'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n',
        "src/a.py": CLEAN_PY,
    }, {"src/b.py": LEAKY_PY})
    rep = run_scan([str(root)], load_config(None), use_adapters=False,
                   changed_since=base)
    tagged = {f.location.path for f in rep.active() if "outside-this-change" in f.tags}
    untagged = {f.location.path for f in rep.active() if "outside-this-change" not in f.tags}
    assert "src/b.py" in untagged, untagged
    assert all(p != "src/b.py" for p in tagged), tagged
    assert any(p.startswith("infra/") for p in tagged), tagged


def test_a_full_scan_tags_nothing_as_outside_the_change(tmp_path):
    (tmp_path / "a.py").write_text(LEAKY_PY)
    rep = run_scan([str(tmp_path)], load_config(None), use_adapters=False)
    assert rep.scan_scope["mode"] == "full"
    assert not any("outside-this-change" in f.tags for f in rep.findings)
