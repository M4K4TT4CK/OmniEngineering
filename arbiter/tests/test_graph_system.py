"""Fingerprints, inventory, the resource graph, the read cache and the
cross-repository system scan over fixtures/system.
"""
from __future__ import annotations

from helpers import LEGACY

from arbiter.ab import load_ground_truth, score_ground_truth
from arbiter.core import Finding, Location
from arbiter.engine import run_scan
from arbiter.policy import load_config
from arbiter.review import where

# --------------------------------------------------------------------------
# fingerprints
# --------------------------------------------------------------------------

def test_fingerprint_ignores_line_number():
    a = Finding(rule_id="r", title="t", evidence="x=1", location=Location(path="a.py", start_line=10))
    b = Finding(rule_id="r", title="t", evidence="x=1", location=Location(path="a.py", start_line=400))
    assert a.id == b.id, "a reformat must not invalidate the baseline"


def test_fingerprint_tracks_evidence():
    a = Finding(rule_id="r", title="t", evidence="port=8080", location=Location(path="a.tf"))
    b = Finding(rule_id="r", title="t", evidence="port=8443", location=Location(path="a.tf"))
    assert a.id != b.id, "a changed value is a different finding"


def test_fingerprint_is_repo_scoped():
    a = Finding(rule_id="r", title="t", repo_id="infra", location=Location(path="x.py"))
    b = Finding(rule_id="r", title="t", repo_id="app", location=Location(path="x.py"))
    assert a.id != b.id


def test_queue_line_names_the_repository():
    """`Location.short()` builds its prefix from the Location, which most probes
    leave blank — 3,227 of 3,564 findings in a 36-repository corpus scan, across
    secrets, supply_chain and resource_policy alike. The Finding carries the id
    in every one of those cases. Two corpus repositories each have a `python/`
    tree, so a bare `python/stepfunctions/README.md` named nothing a reader
    could open."""
    blank = Finding(rule_id="r", title="t", repo_id="juice-shop",
                    location=Location(path="docs/x.md", start_line=7))
    assert where(blank) == "juice-shop:docs/x.md:7"

    # A Location that already carries the id is left alone, not double-prefixed.
    both = Finding(rule_id="r", title="t", repo_id="app",
                   location=Location(path="x.py", start_line=3, repo_id="app"))
    assert where(both) == "app:x.py:3"

    # Repo-level: there is nothing to point at but the repository itself, and
    # naming it beats the bare "-" that Location.short() returns.
    whole = Finding(rule_id="r", title="t", repo_id="infra", location=Location())
    assert where(whole) == "infra"


# --------------------------------------------------------------------------
# inventory & graph
# --------------------------------------------------------------------------

def test_dot_github_is_not_skipped(legacy_report):
    paths = [f.location.path for f in legacy_report.findings]
    assert any(p.startswith(".github/") for p in paths), "CI config must be inventoried"


def test_output_directory_is_not_scanned(tmp_path):
    """A scan reads the working tree and --out defaults to `arbiter-out`
    inside it, so the second run reported on the first run's rendering: 28 of
    101 unsuppressed findings, 24 of them in the very rule being adjudicated."""
    (tmp_path / "app.py").write_text("x = 1\n")
    out = tmp_path / "arbiter-out"
    out.mkdir()
    # A source file, not the markdown rendering: the suppression probe no
    # longer reads documentation (REQ-033), and the control must still trip.
    (out / "leftover.py").write_text("x = 1  # noqa\n")
    cfg = dict(load_config(None))

    # The control: without out_dir the previous run's output is read back.
    # Without this assertion the test below could pass for any reason.
    leaked = run_scan([str(tmp_path)], cfg, only=["assurance"]).active()
    assert [f for f in leaked if f.location.path.startswith("arbiter-out")]

    kept = run_scan([str(tmp_path)], cfg, only=["assurance"], out_dir=str(out)).active()
    assert not [f for f in kept if f.location.path.startswith("arbiter-out")]


# --------------------------------------------------------------------------
# probes
# --------------------------------------------------------------------------

def test_known_defects_are_all_found(legacy_report):
    truth = load_ground_truth(str(LEGACY))
    assert truth is not None
    result = score_ground_truth(truth, legacy_report.active())
    assert result.missed == [], f"regressed on planted defects: {result.missed}"
    assert result.recall == 1.0


def test_interface_findings_need_two_repos(legacy_report, system_report):
    assert not [f for f in legacy_report.findings if f.dimension == "interface"]
    seams = [f for f in system_report.findings if f.dimension == "interface"]
    assert len(seams) >= 2


def test_all_six_seam_checks_fire(system_report):
    rules = {f.rule_id.split(".")[-1] for f in system_report.findings if f.dimension == "interface"}
    assert {
        "constant-disagreement",
        "env-var-never-provided",
        "env-var-provided-but-unused",
        "permission-not-granted",
        "permission-unused",
        "port-not-exposed",
    } <= rules


def test_seam_findings_are_severity_ordered(system_report):
    """A missing grant breaks the app; an unused grant is hygiene."""
    by_rule = {f.rule_id.split(".")[-1]: f for f in system_report.findings}
    assert by_rule["permission-not-granted"].severity == "high"
    assert by_rule["permission-unused"].severity == "low"


def test_constant_disagreement_cites_both_repos(system_report):
    f = next(f for f in system_report.findings if "constant-disagreement" in f.rule_id)
    repos = {f.location.repo_id} | {r.repo_id for r in f.related}
    assert repos == {"infra", "app"}
    assert "512" in f.description and "1024" in f.description


def test_unused_permission_cites_the_grant_it_found(system_report):
    """The rule collected service names into a set and threw the grant site
    away, so it could only point at the string `iam:s3` and had to guess a
    repository — the alphabetically first infrastructure one, which is the
    wrong one whenever the grant is not in it."""
    f = next(f for f in system_report.findings if "permission-unused" in f.rule_id)
    assert f.location.path, "the finding must name the file granting the permission"
    assert f.location.logical.startswith("iam:")
    assert f.location.repo_id == f.repo_id != ""


def test_every_finding_names_its_repository_in_the_location(tmp_path):
    """REQ-014 fixed this for doc_drift and REQ-015 worked around it in the
    review queue; this is the invariant itself. `Location.short()` builds its
    `repo:path` prefix from the Location, so a probe that sets the id on the
    Finding alone renders an unqualified path everywhere the queue is not:
    SARIF, the HTML report and the console. Measured on a 36-repository corpus
    scan before the fix, 3,227 of 3,564 findings carried a blank one.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "src" / "app.py").write_text(
        'api_key = "Xk39Fj2LmQ8vTz01"\n'
        + "".join(f"# TODO: item {i}\n" for i in range(6))
    )
    (tmp_path / "requirements.txt").write_text("requests\n")
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "on: push\njobs:\n  b:\n    steps:\n      - uses: actions/checkout@v4\n"
    )
    (tmp_path / "README.md").write_text("See [design](design.md).\n")

    cfg = dict(load_config(None))
    found = run_scan([str(tmp_path)], cfg,
                     only=["secrets", "supply_chain", "doc_drift", "quality"]).active()

    assert len({f.probe for f in found}) >= 3, "need several probes represented"
    unqualified = [f for f in found if f.location.repo_id != f.repo_id != ""]
    assert not unqualified, (
        "these findings do not name their repository in the Location: "
        + ", ".join(f"{f.rule_id} @ {f.location.path}" for f in unqualified)
    )


def test_renamed_cdk_output_directory_is_still_generated():
    """CDK_OUTDIR lets a project redirect synth output to a renamed directory
    (`cdk.out.chk`, seen on a real system); classify() must recognise it the
    same as the default `cdk.out`, or everything inside it -- including a
    Lambda asset bundle's vendored dependencies -- reads as first-party
    source, the other half of what made the false positive above possible."""
    from arbiter.inventory import classify
    assert classify("cdk.out.chk/asset.abc123/ecdsa/keys.py", "python") == "generated"
    assert classify("cdk.out/asset.abc123/ecdsa/keys.py", "python") == "generated"


def test_cdk_synthesized_template_is_iac_not_generated():
    """The synthesized template is the one thing under cdk.out* this scanner
    deliberately wants (SKIP_DIRS says so explicitly) -- it must not be
    swallowed by the directory-wide "generated" classification that correctly
    applies to everything else CDK writes alongside it."""
    from arbiter.inventory import classify
    assert classify("cdk.out.chk/MyStack.template.json", "json") == "iac"


def test_the_read_cache_never_serves_one_scans_bytes_for_another(tmp_path):
    """A dozen probes each read every file, so reads are cached. A long-lived
    process doing several scans must not get the previous contents for a path
    that has since changed on disk."""
    a = tmp_path / "a"
    a.mkdir()
    (a / "app.py").write_text("PASSWORD = 'first-scan-value-123'\n")
    r1 = run_scan([str(a)], load_config(None), only=["secrets"], use_adapters=False)
    assert [f for f in r1.active()]

    # same path, different contents — as happens when the fix-pair miner
    # exports two commits into the same temporary directory
    (a / "app.py").write_text("x = 1\n")
    r2 = run_scan([str(a)], load_config(None), only=["secrets"], use_adapters=False)
    assert not [f for f in r2.active()], "stale bytes were served from the cache"


def test_the_read_cache_is_correct_without_anyone_clearing_it(tmp_path):
    """The regression this exists for.

    The first version of the cache keyed on path alone and relied on every
    caller clearing it between scans. The injection harness does not call
    run_scan — it invokes probes directly and writes all twenty thousand
    generated cases to the SAME path — so case two was served case one's bytes,
    recall on four rules fell from 1.0000 to 0.0000, and the harness reported
    those numbers without complaint. It would have invalidated every piece of
    training evidence in the project.

    So this calls _read directly, with no scan and no clearing, exactly as the
    harness does."""
    import time as _t

    from arbiter.probes import _read

    class F:
        def __init__(self, p):
            self.abspath = str(p)

    target = tmp_path / "case.tf"
    f = F(target)

    target.write_text("first")
    assert _read(f) == "first"

    # Same path, new content — and nothing clears anything.
    _t.sleep(0.01)
    target.write_text("second")
    assert _read(f) == "second", "stale bytes served for a changed file"

    # Same length, different bytes, to catch a size-only key.
    _t.sleep(0.01)
    target.write_text("thirdX"[:6])
    target.write_text("fourth")
    assert _read(f) == "fourth"


def test_a_missing_file_reads_as_empty_not_as_a_crash(tmp_path):
    from arbiter.probes import _read

    class F:
        abspath = str(tmp_path / "nope.tf")

    assert _read(F()) == ""
