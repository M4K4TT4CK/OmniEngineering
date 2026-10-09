"""The training tools and setup scripts under tools/: worklist, corpus,
disagreement mining, holdout, the installers and the CI workflow.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from helpers import ROOT

from arbiter.core import Finding, Location


def test_corpus_separates_teaching_material_from_production_code(tmp_path):
    """Example repositories are a third population. Counting starter templates
    as well-maintained production code made the false-positive rate look about
    four times worse than it is."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "arbiter_corpus", Path(__file__).resolve().parents[1] / "tools" / "corpus.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert set(mod.POPULATIONS) == {"clean", "vulnerable", "examples"}
    labels = {name: exp for name, (exp, _) in mod.CORPUS.items()}
    for teaching in ("cdk-examples", "k8s-examples", "cfn-templates",
                     "compose-awesome", "helm-charts"):
        assert labels[teaching] == "examples", f"{teaching} is teaching material"
    # real production code must stay in the measurement group
    for production in ("requests", "flask", "express", "rust-ripgrep"):
        assert labels[production] == "clean"


# ---------------------------------------------------------------------------
# The worklist is the handoff between the half of training that needs nobody
# and the half that needs judgement. If it silently reports nothing, an
# unattended cycle looks identical to a healthy one.
# ---------------------------------------------------------------------------

def _worklist(tmp_path, corpus=None, disc=None, knowledge=None,
              empty_evidence=False) -> str:
    """Run the worklist with every input under the test's control.

    `empty_evidence` points the fix-pair and disagreement inputs at empty files;
    without it the tool reads the repository's real ones and a test asserting
    an empty queue depends on what the last training run happened to find.
    """
    import subprocess
    paths = {}
    for name, data in (("corpus", corpus), ("disc", disc), ("knowledge", knowledge)):
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps(data if data is not None else {}))
        paths[name] = str(p)
    out = tmp_path / "WORKLIST.md"
    cmd = ["python", str(ROOT / "tools" / "worklist.py"),
           "--corpus-summary", paths["corpus"], "--discrimination", paths["disc"],
           "--knowledge", paths["knowledge"], "--out", str(out)]
    if empty_evidence:
        empty = tmp_path / "none.json"
        empty.write_text("{}")
        cmd += ["--fix-pairs", str(empty), "--disagreements", str(empty)]
    subprocess.run(cmd, check=True, capture_output=True, cwd=str(ROOT))
    return out.read_text()


def test_worklist_raises_a_critical_on_good_code_first(tmp_path):
    corpus = {"rows": [
        {"repo": "some-lib", "expectation": "clean", "stack_label": "python",
         "loc": 50_000, "findings": 3, "critical": 1, "high": 0},
        {"repo": "goat", "expectation": "vulnerable", "stack_label": "python",
         "loc": 5_000, "findings": 40, "critical": 9, "high": 4},
    ]}
    text = _worklist(tmp_path, corpus=corpus, disc={"rows": []})
    assert "HIGHEST" in text
    assert "some-lib" in text
    # a critical on the deliberately broken repo is the correct answer, not an item
    assert "goat** (python): 9 critical" not in text


def test_worklist_flags_a_severity_the_measurement_does_not_support(tmp_path):
    disc = {"rows": [{
        "rule": "arbiter/secrets.made-up", "severity": "high", "dimension": "security",
        "judgeable": True, "thin": False, "weighted_ratio": 0.4,
        "clean_hits": 90, "vuln_hits": 2,
    }]}
    text = _worklist(tmp_path, corpus={"rows": []}, disc=disc)
    assert "arbiter/secrets.made-up" in text and "0.4x" in text


def test_worklist_does_not_judge_a_quality_rule(tmp_path):
    """There is no deliberately-badly-documented population, so a low ratio on
    a quality rule is a statement about codebase age, not about the rule."""
    disc = {"rows": [{
        "rule": "arbiter/ast.function-too-long", "severity": "low",
        "dimension": "quality", "judgeable": False, "thin": False,
        "weighted_ratio": 0.2, "clean_hits": 800, "vuln_hits": 33,
    }]}
    text = _worklist(tmp_path, corpus={"rows": []}, disc=disc)
    assert "function-too-long" not in text


def test_worklist_flags_a_rule_with_no_controls(tmp_path):
    know = {"rules": {
        "arbiter/secrets.uncontrolled": {
            "synthetic_positives": 500, "synthetic_negatives": 0},
        "arbiter/secrets.controlled": {
            "synthetic_positives": 500, "synthetic_negatives": 500},
    }}
    text = _worklist(tmp_path, corpus={"rows": []}, disc={"rows": []}, knowledge=know)
    assert "arbiter/secrets.uncontrolled" in text
    assert "arbiter/secrets.controlled" not in text


def test_worklist_flags_rules_nothing_has_ever_exercised(tmp_path):
    """A rule that fired on no repository and has no injection trials is an
    assertion, not a measurement — and it is invisible in every other table."""
    text = _worklist(tmp_path, corpus={"rows": []}, disc={"rows": []})
    assert "nothing has ever exercised" in text.lower()


def test_worklist_says_so_when_there_is_nothing_to_do(tmp_path):
    """With every declared rule measured and every check clean, the queue is
    empty — and an empty queue means the corpus has stopped teaching us
    anything, which is itself the finding."""
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "src"))
    from arbiter.controls import load_frameworks
    from arbiter.probes import _load_resource_rules
    # Everything measured means everything: the declared rules, and every
    # arbiter check the shipped control packs map a control to. A pack mapping
    # that nothing exercises is itself a queue item, so leaving those out would
    # make this test assert an impossible state.
    ids = {f"arbiter/resource.{r['id']}" for r in _load_resource_rules()}
    for fw in load_frameworks():
        for c in fw.controls:
            ids |= {ch for ch in c.satisfied_by if ch.startswith("arbiter/")}
    rows = [{"rule": rid, "severity": "medium",
             "dimension": "security", "judgeable": True, "thin": False,
             "weighted_ratio": 40.0, "clean_hits": 1, "vuln_hits": 40}
            for rid in sorted(ids)]
    text = _worklist(tmp_path, corpus={"rows": []}, disc={"rows": rows},
                     empty_evidence=True)
    assert "widen it, not to run it again" in text


def test_worklist_reports_missing_input_rather_than_looking_clean(tmp_path):
    """An unattended cycle that failed halfway must not produce a worklist that
    reads like a clean bill of health."""
    import subprocess
    out = tmp_path / "W.md"
    subprocess.run(
        ["python", str(ROOT / "tools" / "worklist.py"),
         "--corpus-summary", str(tmp_path / "nope.json"),
         "--discrimination", str(tmp_path / "nope2.json"),
         "--knowledge", str(tmp_path / "nope3.json"), "--out", str(out)],
        check=True, capture_output=True, cwd=str(ROOT))
    text = out.read_text()
    assert "Incomplete" in text and "train_cycle.sh" in text


def test_worklist_raises_the_gap_only_a_person_can_close(tmp_path):
    """Calibration reads only the adjudicated ledger. With that ledger empty the
    machinery is inert, and no amount of nightly running will fill it."""
    know = {"rules": {"arbiter/x": {"synthetic_positives": 10, "synthetic_negatives": 10}},
            "adjudicated": {}}
    header = "No real finding has ever been reviewed by a person"
    text = _worklist(tmp_path, corpus={"rows": []}, disc={"rows": []}, knowledge=know)
    assert header in text and "arbiter feedback" in text

    reviewed = dict(know, adjudicated={"f:abcd": {"verdict": "true_positive"}})
    assert header not in _worklist(
        tmp_path, corpus={"rows": []}, disc={"rows": []}, knowledge=reviewed)


def test_worklist_flags_a_control_mapped_to_a_check_that_never_fires(tmp_path):
    """A control whose every covering check never fires reads as SATISFIED
    forever, in a compliance report, on any codebase. A permanent pass is the
    worst thing such a report can contain, because it looks like evidence."""
    import subprocess
    import textwrap
    packs = tmp_path / "packs"
    packs.mkdir()
    (packs / "x.yaml").write_text(textwrap.dedent("""
        framework:
          id: TESTFW
          title: Test
          declared_controls: 10
          declared_source: test
        controls:
          - id: XX-1
            title: Mapped to a rule that never fires
            automatable: partial
            machine_scope: nothing
            residual: a person
            satisfied_by: [arbiter/resource.rule-that-does-not-exist]
    """))
    disc = tmp_path / "d.json"
    disc.write_text(json.dumps({"rows": [
        {"rule": "arbiter/resource.something-else", "severity": "low",
         "dimension": "security", "judgeable": True, "thin": False,
         "weighted_ratio": 9.0, "clean_hits": 1, "vuln_hits": 9}]}))
    empty = tmp_path / "e.json"
    empty.write_text("{}")
    out = tmp_path / "W.md"
    r = subprocess.run(
        ["python", str(ROOT / "tools" / "worklist.py"),
         "--discrimination", str(disc), "--corpus-summary", str(empty),
         "--knowledge", str(empty), "--packs", str(packs), "--out", str(out)],
        capture_output=True, text=True, cwd=str(ROOT))
    assert out.exists(), r.stderr[-600:]
    text = out.read_text()
    assert "permanent false pass" in text
    assert "TESTFW" in text and "XX-1" in text


# ---------------------------------------------------------------------------
# Disagreement mining.
#
# A person adjudicates maybe twenty findings before it becomes a chore, so
# which twenty is the whole question. A random twenty confirms what is already
# believed; the informative ones are where two independent tools looked at the
# same line and disagreed, because exactly one of them is wrong.
# ---------------------------------------------------------------------------

def _dis():
    import importlib
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "tools"))
    return importlib.import_module("disagree")


def test_an_external_check_arbiter_has_no_rule_for_is_a_gap_not_a_disagreement():
    """The first version reported 238 'contested' findings on one repository,
    nearly all of them checks Arbiter simply does not cover."""
    d = _dis()
    findings = [
        Finding(rule_id="checkov/CKV_1", title="Ensure TLS 1.2 minimum",
                location=Location(path="a.tf", start_line=3)),
        Finding(rule_id="arbiter/resource.unencrypted-database",
                title="Database storage is not encrypted",
                location=Location(path="b.tf", start_line=1)),
    ]
    res = d.compare(findings)
    gaps = {r["rule"] for r in res["coverage_gap"]}
    assert "checkov/CKV_1" in gaps, "Arbiter has no tls rule here, so this is a gap"
    assert not any(r["rule"] == "checkov/CKV_1" for r in res["contested"])


def test_a_miss_where_arbiter_does_have_a_rule_is_contested():
    d = _dis()
    findings = [
        # Arbiter covers encryption (it fires elsewhere) but not at a.tf
        Finding(rule_id="arbiter/resource.unencrypted-database",
                title="Database storage is not encrypted",
                location=Location(path="b.tf", start_line=1)),
        Finding(rule_id="checkov/CKV_2", title="Ensure RDS is encrypted",
                location=Location(path="a.tf", start_line=3)),
    ]
    res = d.compare(findings)
    assert any(r["rule"] == "checkov/CKV_2" for r in res["contested"])


def test_agreement_is_never_queued():
    d = _dis()
    findings = [
        Finding(rule_id="arbiter/resource.unencrypted-database", title="not encrypted",
                severity="high", location=Location(path="a.tf", start_line=3)),
        Finding(rule_id="checkov/CKV_3", title="Ensure encryption at rest",
                severity="high", location=Location(path="a.tf", start_line=3)),
    ]
    res = d.compare(findings)
    assert res["corroborated"] and not res["contested"]


def test_a_wide_severity_gap_is_reported_separately():
    d = _dis()
    findings = [
        Finding(rule_id="arbiter/resource.unencrypted-database", title="not encrypted",
                severity="critical", location=Location(path="a.tf", start_line=3)),
        Finding(rule_id="checkov/CKV_4", title="Ensure encryption at rest",
                severity="low", location=Location(path="a.tf", start_line=3)),
    ]
    res = d.compare(findings)
    assert res["severity_disagreement"] and not res["corroborated"]


def test_a_finding_never_cites_itself_as_the_other_side():
    d = _dis()
    findings = [Finding(rule_id="semgrep/s3-public", title="public bucket",
                        location=Location(path="a.tf", start_line=1))]
    res = d.compare(findings)
    rows = res["contested"] + res["coverage_gap"]
    assert rows and all(r["rule"] not in r["other_side"] for r in rows)


def test_log_is_not_matched_inside_unrelated_words():
    """`log` as a family keyword matches login, logical, dialog and catalog.
    It put a broken-documentation-link finding in the logging family."""
    d = _dis()
    assert d.family_of("arbiter/drift.broken-doc-link", "Broken documentation link") == ""
    assert d.family_of("arbiter/resource.no-log-retention", "No retention period") == "logging"


def test_the_holdout_is_real_and_spans_the_populations(tmp_path):
    """Every figure in this project was measured on repositories the rules were
    tuned against. That is how a tool ends up fitted to its own practice set."""
    import importlib
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "tools"))
    corpus = importlib.import_module("corpus")
    importlib.reload(corpus)
    assert len(corpus.HOLDOUT) >= 4
    assert corpus.HOLDOUT <= set(corpus.CORPUS), "a held-out repo must be in the corpus"
    pops = {corpus.CORPUS[h][0] for h in corpus.HOLDOUT}
    assert pops == {"clean", "vulnerable", "examples"}, \
        "the held-out numbers are only comparable if every population is represented"
    tuned = set(corpus.CORPUS) - corpus.HOLDOUT
    assert len(tuned) > 3 * len(corpus.HOLDOUT), "most of the corpus must remain for tuning"


def test_worklist_items_are_numbered_consecutively(tmp_path):
    """`item()` closes over the counter with nonlocal, so a loop variable of the
    same name inside main() silently resets it. That happened, and the queue
    printed items 1, 2, 15, 2, 3 — which reads as a broken tool and hides
    whether anything was missed."""
    import re as _re
    import subprocess
    disc = tmp_path / "d.json"
    disc.write_text(json.dumps({"rows": [
        {"rule": "arbiter/resource.x", "severity": "high", "dimension": "security",
         "judgeable": True, "thin": False, "weighted_ratio": 0.2,
         "clean_hits": 90, "vuln_hits": 1}]}))
    pairs = tmp_path / "p.json"
    pairs.write_text(json.dumps({"pairs": [
        {"repo": "r", "fixed_in": "abc", "rule": "arbiter/resource.y",
         "path": "a.tf", "confirmed": None}]}))
    dis = tmp_path / "dis.json"
    dis.write_text(json.dumps({
        "contested": [{"id": "f:1", "rule": "checkov/CKV_1", "only": "checkov",
                       "family": "tls", "other_side": []}],
        "coverage_gap": [{"family": "tls"}]}))
    out = tmp_path / "W.md"
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"rows": []}))
    subprocess.run(["python", str(ROOT / "tools" / "worklist.py"),
                    "--discrimination", str(disc), "--corpus-summary", str(empty),
                    "--knowledge", str(empty), "--fix-pairs", str(pairs),
                    "--disagreements", str(dis), "--out", str(out)],
                   check=True, capture_output=True, cwd=str(ROOT))
    nums = [int(m) for m in _re.findall(r"^### (\d+)\.", out.read_text(), _re.M)]
    assert nums == list(range(1, len(nums) + 1)), nums
    assert len(nums) >= 4


def test_worklist_survives_a_malformed_results_file(tmp_path):
    """It runs unattended every night. A half-written results file must degrade
    to "that input is missing" rather than killing the queue — a missing
    worklist is indistinguishable from a clean one."""
    import subprocess
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"rows": [{"unexpected": "shape"}]}))
    truncated = tmp_path / "trunc.json"
    truncated.write_text('{"rows": [{"repo": ')
    out = tmp_path / "W.md"
    r = subprocess.run(["python", str(ROOT / "tools" / "worklist.py"),
                        "--corpus-summary", str(bad), "--discrimination", str(truncated),
                        "--knowledge", str(bad), "--out", str(out)],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr[-400:]
    assert "Incomplete" in out.read_text()


def test_a_commit_message_ranks_a_fix_pair_but_never_confirms_it():
    """Commit messages are not used to FIND pairs — libraries are full of
    feature commits mentioning encryption that fix nothing. They only order the
    queue so a person's first verdicts land on the clearest cases."""
    import importlib
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "tools"))
    fp = importlib.import_module("fixpairs")
    assert fp.corroborated("arbiter/supply.unpinned-action",
                           "chore: pin GitHub Actions to commit SHA")
    assert fp.corroborated("arbiter/secrets.pg-url",
                           "Remove MONGOLAB_URI and mlab connection string")
    # a rewrite that removed the resource is NOT corroboration
    assert not fp.corroborated("arbiter/resource.k8s-no-security-context",
                               "Modernize manifest: replace ReplicationController")


# ---------------------------------------------------------------------------
# The setup scripts.
#
# These exist because two things that were done by hand in a sandbox had no
# reproducible form in the repository: installing the five external analyzers,
# and creating the GitHub repository. Both are tested here for the properties
# that matter — the installer must never fail a build, and the bootstrap must
# never destroy history.
#
# The scripts are run through the bash that PATH resolves, never the bare name
# "bash": on Windows, CreateProcess searches System32 before PATH, and
# System32\bash.exe is the WSL launcher, which prints an install prompt in
# UTF-16 and exits 1 whether or not Git Bash is installed. shutil.which walks
# PATH only, where the runner puts Git's bin directory, so it finds the real
# shell. The first run of the Windows matrix (REQ-024) failed exactly this way.
# ---------------------------------------------------------------------------

def _bash():
    import shutil
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    return bash


def test_install_script_never_fails_a_build(tmp_path):
    """A missing analyzer is a coverage fact, not an error: Arbiter records it
    as not assessed and the coverage figure drops. Exiting non-zero here would
    turn an honest gap into a broken pipeline."""
    import subprocess
    r = subprocess.run([_bash(), str(ROOT / "tools" / "install_tools.sh"), "nosuchtool"],
                       capture_output=True, text=True, cwd=str(ROOT), timeout=180)
    assert r.returncode == 0, r.stderr[-400:]


def test_install_script_pins_every_version():
    text = (ROOT / "tools" / "install_tools.sh").read_text()
    for tool in ("CHECKOV", "SEMGREP", "BANDIT", "RUFF", "GITLEAKS"):
        assert re.search(rf"{tool}_VERSION:-\d+\.\d+", text), f"{tool} is not pinned"


def test_bootstrap_refuses_when_it_is_not_a_repository(tmp_path):
    import shutil
    import subprocess
    (tmp_path / "tools").mkdir()
    shutil.copy(ROOT / "tools" / "bootstrap_repo.sh", tmp_path / "tools")
    r = subprocess.run([_bash(), "tools/bootstrap_repo.sh", "--dry-run"],
                       capture_output=True, text=True, cwd=str(tmp_path), timeout=60)
    assert r.returncode == 1 and "No .git" in r.stdout


def test_bootstrap_never_force_pushes_or_rewrites_history():
    """The whole risk in a script like this is that it resolves a conflict by
    discarding one side."""
    text = (ROOT / "tools" / "bootstrap_repo.sh").read_text()
    for dangerous in ("--force", "-f ", "push -f", "reset --hard", "filter-branch",
                      "rebase --onto", "git rm"):
        assert dangerous not in text, f"bootstrap contains {dangerous!r}"


def test_bootstrap_is_idempotent_about_an_existing_remote(tmp_path):
    import shutil
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    (tmp_path / "f.txt").write_text("x")
    (tmp_path / "tools").mkdir()
    shutil.copy(ROOT / "tools" / "bootstrap_repo.sh", tmp_path / "tools")
    for cmd in (["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-q", "-m", "init"],
                ["git", "remote", "add", "origin", "https://example.com/pre.git"]):
        subprocess.run(cmd, cwd=str(tmp_path), check=True)
    r = subprocess.run([_bash(), "tools/bootstrap_repo.sh", "--dry-run"],
                       capture_output=True, text=True, cwd=str(tmp_path), timeout=60)
    assert "Leaving it alone" in r.stdout
    url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(tmp_path),
                         capture_output=True, text=True).stdout.strip()
    assert url == "https://example.com/pre.git", "an existing remote was modified"


def test_the_workflow_installs_the_analyzers_from_the_script():
    """Inline pip lines in CI drift from what a laptop installs. One script."""
    wf = (ROOT / ".github" / "workflows" / "train.yml").read_text()
    assert "tools/install_tools.sh" in wf
    assert "pip install checkov" not in wf, "CI must not install analyzers inline"


# The five files the nightly job accumulates across runs. Per-run logs are
# stamped and deliberately ignored; these are the evidence and must survive.
ACCUMULATING = (
    ".arbiter/knowledge.json",
    ".arbiter/external-severity.json",
    "training/WORKLIST.md",
    "training/fix-pairs.json",
    "training/disagreements.json",
)


def _is_ignored(path: str) -> bool:
    import subprocess
    return subprocess.run(["git", "check-ignore", "-q", path],
                          cwd=str(ROOT), capture_output=True).returncode == 0


def test_the_accumulating_training_files_are_committable(tmp_path):
    """The nightly job's only product is these five files. A gitignore rule that
    swallows one turns a successful cycle into a lost night (REQ-023)."""
    for path in ACCUMULATING:
        assert not _is_ignored(path), f".gitignore excludes {path}, which the job commits"
    # And the converse: a stamped per-run log must stay out, or the repository
    # grows by a cycle's worth of tables every night for no later reader.
    assert _is_ignored("training/corpus-2026-01-01T000000Z/summary.json")
    assert _is_ignored("training/discriminate-2026-01-01T000000Z.json")


def test_the_training_job_checks_it_can_write_back_before_it_measures():
    """Run 1 measured for fifty-five minutes and committed nothing, because the
    thing that was broken was only discovered at the end. The check is instant
    and the cycle is not, so the check goes first."""
    wf = (ROOT / ".github" / "workflows" / "train.yml").read_text(encoding="utf-8")
    assert "check_writeback.sh" in wf, "the job does not verify it can save results"
    assert wf.index("check_writeback.sh") < wf.index("train_cycle.sh"), (
        "the write-back check runs after the cycle, which is the defect it exists "
        "to prevent")
    cycle = (ROOT / "tools" / "train_cycle.sh").read_text(encoding="utf-8")
    assert "check_writeback.sh" in cycle, "a laptop run skips the check CI makes"


def test_the_writeback_check_refuses_a_ledger_it_could_not_commit(tmp_path):
    """The check has to actually fail on the arrangement that cost run 1 —
    `training/` ignored as a directory — and pass once the negations are back."""
    import shutil
    import subprocess
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")

    repo = tmp_path / "r"
    (repo / "tools").mkdir(parents=True)
    shutil.copy(ROOT / "tools" / "check_writeback.sh", repo / "tools")
    for path in ACCUMULATING:
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)

    def check():
        return subprocess.run([bash, "tools/check_writeback.sh"], cwd=str(repo),
                              capture_output=True, text=True)

    # The exact mistake: the directory itself ignored, so `git add` errors out.
    (repo / ".gitignore").write_text("training/\n", encoding="utf-8")
    bad = check()
    assert bad.returncode != 0, "an unsaveable ledger was allowed to start a cycle"
    assert "IGNORED" in bad.stderr
    assert "training/WORKLIST.md" in bad.stderr

    # Ignoring the per-run logs while keeping the accumulating files is correct.
    (repo / ".gitignore").write_text(
        "training/*\n!training/WORKLIST.md\n!training/fix-pairs.json\n"
        "!training/disagreements.json\n", encoding="utf-8")
    assert check().returncode == 0, check().stderr


def test_the_writeback_check_lists_what_gitignore_keeps():
    """Two places name these files. They drift silently unless something reads
    both — and the drift shows up as a lost night, months later."""
    script = (ROOT / "tools" / "check_writeback.sh").read_text(encoding="utf-8")
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    kept = {line[1:].strip() for line in ignore.splitlines()
            if line.startswith("!training/") and not line.endswith(".gitkeep")}
    for path in kept:
        assert path in script, f".gitignore keeps {path}; the write-back check ignores it"
    for path in ACCUMULATING:
        assert path in script, f"{path} is not checked before a cycle starts"


def _pr_check_workflow():
    import yaml
    path = ROOT / ".github" / "workflows" / "pr-check.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_test_suite_runs_on_windows_in_ci():
    """The gap REQ-024 exists to close. A developer's machine is not a control."""
    job = _pr_check_workflow()["jobs"]["check"]
    oses = job["strategy"]["matrix"]["os"]
    assert any("windows" in o for o in oses),         "no automation runs on Windows; REQ-006 is how that ends"
    assert any("ubuntu" in o for o in oses),         "Linux is the deployment target and must not be dropped for Windows"


def test_the_windows_run_is_required_rather_than_advisory():
    """An advisory check is a check nobody reads. It also has to not be
    cancelled by the other platform failing, or the matrix reports one result
    and hides the one it was added for."""
    job = _pr_check_workflow()["jobs"]["check"]
    assert not job.get("continue-on-error"), "the whole job is advisory"
    assert job["strategy"].get("fail-fast") is False,         "a Linux failure cancels Windows, which is the result being sought"
    for step in job["steps"]:
        if "upload-sarif" in str(step.get("uses", "")):
            # Publishes the self-gate's SARIF to code scanning (REQ-039); the
            # verdict it carries was already delivered by the gate step above
            # it, and code scanning is not enabled on every fork or mirror.
            # test_annotations.py holds the upload step to its own shape.
            continue
        assert not step.get("continue-on-error"),             f"step {step.get('name', '?')!r} cannot fail the run"


def test_ci_records_why_each_platform_skipped_what_it_skipped():
    """Windows legitimately skips things Linux does not. A skip that is silently
    absent from the log reads the same as a test that was never collected."""
    job = _pr_check_workflow()["jobs"]["check"]
    tests = next(s for s in job["steps"] if s.get("name") == "Tests")
    assert "-rs" in tests["run"].split(),         "pytest is not asked to print skip reasons, so skips vanish from the log"
    assert tests.get("env", {}).get("PYTHONIOENCODING") == "utf-8",         "Windows writes the console codepage without this; REQ-007 was that bug"


# ---------------------------------------------------------------------------
# The holdout comparison, corrected.
#
# It reported Kubernetes as 2.09x worse on held-out code. Three things were
# wrong with that and all three had already been fixed elsewhere in this
# project: it stack-matched on the repository LABEL (Argo CD is labelled
# kubernetes and is 52% Go), it counted findings rather than weighting them
# (1,099 of Argo CD's 1,152 Kubernetes findings are discounted testdata), and
# one repository per side is not a sample. Corrected, the held-out side is
# quieter than the tuned one. The whole signal was the measurement.
# ---------------------------------------------------------------------------

def _corpus_module():
    import importlib
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "tools"))
    m = importlib.import_module("corpus")
    importlib.reload(m)
    return m


def test_corpus_rows_carry_weight_and_language_breakdown(tmp_path):
    """Without these the holdout comparison can only count findings and match
    on a repository label, which is what produced the wrong answer."""
    (tmp_path / "repo").mkdir()
    src = tmp_path / "repo" / "requests.txt"
    src.write_text("x\n")
    # The fields the comparison depends on must exist on every row.
    required = {"weight", "weight_by_language", "loc_by_language", "holdout"}
    code = (ROOT / "tools" / "corpus.py").read_text()
    for field in required:
        assert f'"{field}"' in code, f"rows do not carry {field}"


def test_holdout_report_is_weighted_not_a_raw_count():
    code = (ROOT / "tools" / "corpus.py").read_text()
    assert "SEV_WEIGHT" in code and "CONFIDENCE_FACTOR" in code, \
        "the holdout comparison must weight findings by what they are worth"
    assert "weight_by_language" in code, \
        "language-matched comparison needs per-language weight"


def test_holdout_report_matches_on_language_not_repository_label():
    """A repository label says nothing about what is in the repository."""
    code = (ROOT / "tools" / "corpus.py").read_text()
    assert "LANGUAGE-MATCHED" in code
    assert "stack_label" not in code.split("DOES THE TUNING GENERALIZE")[1], \
        "the comparison still matches on the repo label"


def test_holdout_report_says_when_one_repo_is_not_a_sample():
    code = (ROOT / "tools" / "corpus.py").read_text()
    assert "not a comparison" in code
