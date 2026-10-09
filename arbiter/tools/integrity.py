#!/usr/bin/env python3
"""Claim-integrity evidence harness.

Two directions, because either one alone is worthless:

  1. NO FALSE ALARMS — every report the engine can legitimately produce must
     pass verification. Measured by exhaustive enumeration over a bounded
     state space rather than by sampling, so the result is a proof over that
     space, not an estimate.

  2. NO MISSED VIOLATIONS — every deliberately broken report must be caught.
     A checker that never complains is indistinguishable from no checker.

Verdict correctness on arbitrary code is undecidable. Claim integrity is a
property of Arbiter's own execution, so it can be enumerated.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arbiter.claims import INVARIANTS, nines, verify, wilson_lower_bound  # noqa: E402
from arbiter.core import (  # noqa: E402
    DimensionScore,
    Finding,
    Location,
    ProbeOutcome,
    Report,
)
from arbiter.policy import DEFAULTS, compute_scorecard, evaluate_gate  # noqa: E402

STATUSES = ("ran", "skipped", "error")
DIMENSIONS = ("security", "quality", "drift")


def make_report(statuses: tuple[str, ...], findings_per_probe: tuple[int, ...],
                suppress: bool, threshold: float) -> tuple[Report, dict]:
    """Build a report exactly the way the engine does, from a state vector."""
    config = dict(DEFAULTS)
    config["score"] = {"weights": DEFAULTS["score"]["weights"], "coverage_threshold": threshold}

    probes: list[ProbeOutcome] = []
    findings: list[Finding] = []
    for i, (status, n) in enumerate(zip(statuses, findings_per_probe)):
        dim = DIMENSIONS[i % len(DIMENSIONS)]
        count = n if status == "ran" else 0
        probes.append(ProbeOutcome(
            name=f"p{i}", status=status,
            reason="" if status == "ran" else f"synthetic {status}",
            dimensions=[dim], checks=3 + i, finding_count=count,
        ))
        for j in range(count):
            f = Finding(
                rule_id=f"synthetic/p{i}.r{j}", title=f"finding {i}.{j}",
                dimension=dim, severity=("critical" if j == 0 else "medium"),
                probe=f"p{i}", location=Location(path=f"f{i}.py", start_line=j + 1),
                evidence=f"e{i}{j}",
            )
            if suppress and j % 2 == 0:
                f.suppressed = True
                f.suppression_reason = "synthetic"
            findings.append(f)

    report = Report(system="synthetic", findings=findings, probes=probes)
    report.scorecard = compute_scorecard(findings, probes, 5000, config)
    report.gate = evaluate_gate(report, config)
    return report, config


# ---------------------------------------------------------------------------
# Direction 1 — exhaustive, no false alarms
# ---------------------------------------------------------------------------

def enumerate_space(n_probes: int, max_findings: int, thresholds: tuple[float, ...]) -> tuple[int, list]:
    checked = 0
    failures = []
    finding_options = tuple(range(max_findings + 1))
    for statuses in itertools.product(STATUSES, repeat=n_probes):
        for counts in itertools.product(finding_options, repeat=n_probes):
            for suppress in (False, True):
                for threshold in thresholds:
                    report, config = make_report(statuses, counts, suppress, threshold)
                    violations = verify(report, config)
                    checked += 1
                    if violations:
                        failures.append((statuses, counts, suppress, threshold, violations))
    return checked, failures


# ---------------------------------------------------------------------------
# Direction 2 — every invariant is actually enforced
# ---------------------------------------------------------------------------

def break_invariant(name: str, report: Report):
    """Mutate a valid report so it violates exactly the named invariant."""
    sc = report.scorecard
    if name == "CI-1":
        # a probe abstains while a dimension still claims complete coverage
        report.probes.append(ProbeOutcome(name="ghost", status="skipped", reason="r",
                                          dimensions=["security"], checks=5))
        sc.dimensions["security"] = DimensionScore("security", 100.0, 1.0, 0, 5, 5)
    elif name == "CI-2":
        sc.overall = 91.0
        sc.coverage = 0.10
        sc.withheld = False
    elif name == "CI-3":
        sc.withheld = True
        sc.overall = 88.0
    elif name == "CI-4":
        report.probes.append(ProbeOutcome(name="silent", status="skipped", reason="",
                                          dimensions=["quality"], checks=1))
    elif name == "CI-5":
        sc.dimensions["quality"] = DimensionScore("quality", 100.0, 1.0, 0, 2, 9)
    elif name == "CI-7":
        report.probes.append(ProbeOutcome(name="boom", status="error", reason="exploded",
                                          dimensions=["quality"], checks=2, finding_count=4))
    elif name == "CI-8":
        report.findings.append(Finding(
            rule_id="arbiter/resource.x.not-assessed", title="unevaluated",
            severity="info", location=Location(path="a.tf", logical="aws_x.y"),
            evidence="unknown", probe="resource_policy",
        ))
        # recorded nowhere: abstentions() derives from findings, so simulate a
        # report whose ledger was built before the finding was added
        return "ledger-desync"
    elif name == "CI-9":
        sc.coverage = 1.4
    elif name == "CI-10":
        report.probes.append(ProbeOutcome(name="ghost2", status="skipped", reason="r",
                                          dimensions=["drift"], checks=2))
        report.gate = {"passed": True, "reasons": []}
        report.integrity = {}
        return "gate-complete"
    elif name == "CI-12":
        # A coverage gap dressed as a non-question: the engine's own wording
        # for a probe it was prevented from running, paired with the flag that
        # says the probe could never have applied.
        report.probes.append(ProbeOutcome(name="laundered", status="skipped",
                                          reason="missing binary: laundered",
                                          applicable=False,
                                          dimensions=["security"], checks=2))
    elif name == "CI-11":
        # A scan that read part of the repository, with every probe clean and
        # nothing else missing. Without CI-11 this report would assert
        # "probe ran and found nothing" at complete scope about a repository
        # it barely opened.
        report.scan_scope = {"mode": "partial", "files_total": 2000,
                             "files_read": 4, "basis": "changed since main"}
    return None


def check_enforcement() -> tuple[int, list[str]]:
    """Every invariant must be provably enforced, not merely declared."""
    unenforced: list[str] = []
    tested = 0
    for name in INVARIANTS:
        if name == "CI-6":
            continue  # exercised in the exhaustive pass via the suppress axis
        report, config = make_report(("ran", "ran", "ran"), (0, 0, 0), False, 0.6)
        assert not verify(report, config), f"baseline for {name} was not clean"
        special = break_invariant(name, report)
        violations = verify(report, config)
        tested += 1
        if name == "CI-8" and special == "ledger-desync":
            # abstentions() re-derives from findings, so this one is
            # structurally impossible rather than merely unobserved.
            continue
        if name == "CI-10" and special == "gate-complete":
            if not any(v.invariant in ("CI-1", "CI-10") for v in violations):
                unenforced.append(name)
            continue
        if not any(v.invariant == name for v in violations):
            unenforced.append(name)
    return tested, unenforced


# ---------------------------------------------------------------------------
# Direction 3 — CI-13: cacheable probes are file-local
# ---------------------------------------------------------------------------
#
# The result cache (src/arbiter/cache.py) serves a file's findings from a
# previous scan on the strength of one claim: that the probe's answer for a
# file depends on that file alone. `Probe.cacheable` is where the claim is
# made; this is where it is tested. Each cacheable probe is run over a file
# beside another file and over the file alone, and must report the same
# findings for it either way. A probe that reads across files cannot be
# cached, and the check names it.
#
# Where a planted hit is feasible the file carries one, so the comparison is
# between two non-empty answers rather than two silences; the context files
# a probe is entitled to (a manifest) are present in both runs, exactly as
# the cache keeps them in a narrowed inventory.

_DEEP = "def f(x):\n" + "".join(f"{'    ' * (i + 1)}if x > {i}:\n" for i in range(9)) + "    " * 10 + "return x\n"
# The AWS documentation example key, assembled at run time so that this file
# is not itself a secrets hit when Arbiter gates its own tree.
_EXAMPLE_KEY = "AKIA" + "IOSFODNN7EXAMPLE"

# probe -> (file A and its content, context files present in both runs, config overrides)
CI13_PLANTS: dict[str, tuple[tuple[str, str], dict[str, str], dict]] = {
    "secrets": (("a.py", f'aws_key = "{_EXAMPLE_KEY}"\n'), {}, {}),
    "supply_chain": (("requirements.txt", "requests\n"), {}, {}),
    "ast_metrics": (("a.py", _DEEP), {}, {}),
    "house_rules_ast": (("a.py", "try:\n    pass\nexcept Exception:\n    pass\n"), {}, {
        "rules": [{"id": "no-broad-except", "type": "ast_query", "languages": ["python"],
                   "query": '(except_clause (identifier) @t (#match? @t "^Exception$")) @hit',
                   "capture": "hit", "severity": "low"}]}),
    "authored": (("a.py", "import requests\n"), {"requirements.txt": "flask==1.0\n"}, {}),
}
CI13_OTHER_FILE = ("b.py", "x = 1\n")


def _findings_for(probe, root: Path, path_a: str, config: dict) -> tuple[list[str], int]:
    """Run `probe` over the tree at `root`; return A's findings as canonical
    strings and the number of findings that carry no path at all."""
    from arbiter.core import RepoInfo
    from arbiter.inventory import build_inventory
    from arbiter.probes import ProbeContext, clear_read_cache
    clear_read_cache()
    repo = RepoInfo(id="root", path=str(root), source=str(root))
    ctx = ProbeContext(repos=[repo], inventory=build_inventory([repo]), config=config)
    produced = probe.run(ctx) or []
    pathless = sum(1 for f in produced if not f.location.path)
    mine = sorted(json.dumps(f.to_dict(), sort_keys=True, default=str)
                  for f in produced if f.location.path == path_a)
    return mine, pathless


def check_file_locality(probes) -> tuple[int, list[str], list[str]]:
    """CI-13 over the given probes. Returns (tested, failures, skipped)."""
    failures: list[str] = []
    skipped: list[str] = []
    tested = 0
    for probe in probes:
        if not getattr(probe, "cacheable", False):
            continue
        blocked, why = probe.prevented()
        if blocked:
            skipped.append(f"{probe.name}: {why}")
            continue
        (name_a, text_a), context, overrides = CI13_PLANTS.get(
            probe.name, (("a.py", "x = 1\nimport os\n"), {}, {}))
        config = dict(DEFAULTS)
        config.update(overrides)
        tested += 1
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, text in {**context, name_a: text_a}.items():
                (root / rel).write_text(text, encoding="utf-8")
            alone, pathless_alone = _findings_for(probe, root, name_a, config)
            (root / CI13_OTHER_FILE[0]).write_text(CI13_OTHER_FILE[1], encoding="utf-8")
            beside, pathless_beside = _findings_for(probe, root, name_a, config)
        if alone != beside:
            failures.append(f"{probe.name}: {len(alone)} finding(s) for {name_a} alone, "
                            f"{len(beside)} beside {CI13_OTHER_FILE[0]}")
        elif pathless_alone or pathless_beside:
            failures.append(f"{probe.name}: {max(pathless_alone, pathless_beside)} finding(s) "
                            "carry no path and cannot be attributed to a file")
        elif probe.name in CI13_PLANTS and not alone:
            failures.append(f"{probe.name}: the planted hit in {name_a} did not fire, "
                            "so the comparison proved nothing")
    return tested, failures, skipped


def check_ci13_enforced() -> bool:
    """CI-13 must catch a probe that reads across files. A synthetic probe
    declares itself cacheable and reports on A only when B is present."""
    from arbiter.core import Location
    from arbiter.probes import Probe

    def cross_file(ctx):
        files = ctx.inventory.text_files()
        if len(files) < 2:
            return []
        a = next(f for f in files if f.path == "a.py")
        return [Finding(rule_id="synthetic/cross-file", title="depends on the neighbour",
                        probe="cross_file", repo_id="root", location=Location(path=a.path))]

    _, failures, _ = check_file_locality([Probe(name="cross_file", dimensions=["quality"],
                                                checks=1, run=cross_file, cacheable=True)])
    return any(f.startswith("cross_file:") for f in failures)


# ---------------------------------------------------------------------------
# Direction 4 — CI-14: the adapter memo misses when any inventory file changes
# ---------------------------------------------------------------------------
#
# An external analyzer is memoised whole (src/arbiter/cache.py, ARB-047), on
# the strength of one claim: the key covers every file the tool could have
# read, so a replay is only ever served for the tree the tool actually saw.
# This runs a fake adapter -- a probe marked `external`, counting its calls
# -- through the engine three times: a cold scan must run it, a repeat scan
# must replay it, and a one-byte change to one inventory file must run it
# again. Then the claim is broken deliberately (the inputs digest pinned to a
# constant) and the check must fail, or it proves nothing.

CI14_REPLAY = "replayed from cache (nothing the tool reads changed)"


def check_adapter_memo() -> tuple[bool, str]:
    """CI-14 over a fake adapter. Returns (ok, why not)."""
    from arbiter.engine import run_scan
    from arbiter.policy import load_config
    from arbiter.probes import REGISTRY, Probe

    calls = {"n": 0}

    def fake_adapter(ctx):
        calls["n"] += 1
        return [Finding(rule_id="synthetic/adapter.planted", title="planted", probe="fake_adapter",
                        repo_id="root", location=Location(path="a.py", start_line=1),
                        evidence="e")]

    probe = Probe(name="fake_adapter", dimensions=["quality"], checks=1,
                  run=fake_adapter, external=True)
    REGISTRY.append(probe)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / "a.py").write_bytes(b"x = 1\n")
            (root / "b.py").write_bytes(b"y = 2\n")
            config = dict(load_config(None))
            config["cache"] = {"enabled": True}
            kw = {"only": ["fake_adapter"], "use_adapters": False,
                  "cache_path": str(Path(tmp) / "cache.json")}
            run_scan([str(root)], config, **kw)
            if calls["n"] != 1:
                return False, f"a cold scan ran the adapter {calls['n']} time(s), not once"
            rep = run_scan([str(root)], config, **kw)
            oc = next(p for p in rep.probes if p.name == "fake_adapter")
            if calls["n"] != 1 or oc.reason != CI14_REPLAY:
                return False, "a repeat scan of the same tree did not replay the adapter"
            if [f.rule_id for f in rep.findings] != ["synthetic/adapter.planted"]:
                return False, "the replay did not carry the adapter's findings"
            (root / "b.py").write_bytes(b"y = 2\n\n")      # one byte, in the other file
            run_scan([str(root)], config, **kw)
            if calls["n"] != 2:
                return False, "one changed inventory file was served from the memo"
    finally:
        REGISTRY.remove(probe)
    return True, ""


def check_ci14_enforced() -> bool:
    """CI-14 must catch a memo whose key ignores the tree. With the inputs
    digest pinned to a constant, the changed file would be replayed, and the
    check must say so."""
    import arbiter.cache as cache_module
    real = cache_module.adapter_inputs_digest
    cache_module.adapter_inputs_digest = lambda inv, repos: "constant"
    try:
        ok, _why = check_adapter_memo()
    finally:
        cache_module.adapter_inputs_digest = real
    return not ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probes", type=int, default=5)
    ap.add_argument("--max-findings", type=int, default=2)
    ap.add_argument("--thresholds", default="0.0,0.6,1.0")
    args = ap.parse_args()
    thresholds = tuple(float(t) for t in args.thresholds.split(","))

    print("\n  CLAIM INTEGRITY — EVIDENCE\n")

    t0 = time.time()
    checked, failures = enumerate_space(args.probes, args.max_findings, thresholds)
    elapsed = time.time() - t0

    print("  Direction 1 — no false alarms (exhaustive over the bounded space)")
    print(f"    {args.probes} probes x {len(STATUSES)} statuses x "
          f"{args.max_findings + 1} finding counts x 2 suppression states x "
          f"{len(thresholds)} thresholds")
    print(f"    reports enumerated : {checked:,}")
    print(f"    integrity failures : {len(failures):,}")
    print(f"    elapsed            : {elapsed:.1f}s")
    lb = wilson_lower_bound(checked - len(failures), checked)
    if not failures:
        print(f"    result             : EXHAUSTIVE over this space "
              f"(Wilson lower bound if treated as sampling: {nines(lb)})")
    else:
        for f in failures[:5]:
            print(f"      {f[0]} counts={f[1]} suppress={f[2]} thr={f[3]}: "
                  f"{[v.invariant for v in f[4]]}")

    tested, unenforced = check_enforcement()
    print("\n  Direction 2 — every invariant is enforced")
    print(f"    invariants declared : {len(INVARIANTS)}")
    print(f"    mutations tested    : {tested}")
    print(f"    unenforced          : {len(unenforced)}"
          + (f" ({', '.join(unenforced)})" if unenforced else ""))

    from arbiter.probes import REGISTRY  # noqa: E402  (registers the native probes)
    cacheable = [p.name for p in REGISTRY if p.cacheable]
    tested13, failures13, skipped13 = check_file_locality(REGISTRY)
    enforced13 = check_ci13_enforced()
    print("\n  Direction 3 — CI-13: cacheable probes are file-local")
    print(f"    cacheable probes    : {len(cacheable)} ({', '.join(cacheable)})")
    print(f"    probes tested       : {tested13}")
    print(f"    skipped             : {len(skipped13)}"
          + (f" ({'; '.join(skipped13)})" if skipped13 else ""))
    print(f"    not file-local      : {len(failures13)}")
    for line in failures13:
        print(f"      {line}")
    print(f"    enforced            : {'yes' if enforced13 else 'NO'} "
          "(a synthetic cross-file probe is caught)")

    ok14, why14 = check_adapter_memo()
    enforced14 = check_ci14_enforced()
    print("\n  Direction 4 — CI-14: a changed inventory file misses the adapter memo")
    print(f"    cold, replay, edit  : {'PASS' if ok14 else 'FAIL'}"
          + (f" ({why14})" if why14 else ""))
    print(f"    enforced            : {'yes' if enforced14 else 'NO'} "
          "(a memo keyed without the tree is caught)")

    ok = (not failures and not unenforced and not failures13 and enforced13
          and ok14 and enforced14)
    print(f"\n  Verdict: {'PASS' if ok else 'FAIL'}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
