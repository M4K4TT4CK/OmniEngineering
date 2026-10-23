"""Every rule id the scanner emits maps to the probe that emits it, and a
language rule's `controls:` declaration is a check for that control.

`controls._probe_of("house/x")` returned "house". No probe has that name (the
house rules come from `house_rules`, and the `ast_query` ones from
`house_rules_ast`), so a control mapped to a house rule always read as not
assessed, the same defect FAIL-053 fixed for `arbiter/<lang>.*` (FAIL-055).

Language-pack rules declare `controls: [NIST-800-218:PW.5]`, and evaluation
read only the pack's `satisfied_by`, so the declaration did nothing.
"""
from __future__ import annotations

import re

import pytest
import yaml
from helpers import ROOT

from arbiter import ast as ts
from arbiter import controls
from arbiter.core import Finding, Location, ProbeOutcome
from arbiter.engine import run_scan
from arbiter.lang_rules import load_packs
from arbiter.policy import load_config
from arbiter.probes import REGISTRY

SRC = ROOT / "src" / "arbiter"


def _probe_names() -> set[str]:
    from arbiter.adapters import register_adapters
    register_adapters()
    return {p.name for p in REGISTRY}


def _emitted_prefixes() -> set[str]:
    """Every `<source>/<head>` a finding's rule id can start with.

    Read from the source, not listed by hand, so a new probe that emits a new
    prefix is in this set the day it lands: literal `rule_id="arbiter/x.y"`
    strings and rule-id constants, `house/`, one per language pack, and one
    per adapter manifest (`semgrep/...`).
    """
    out: set[str] = set()
    for path in SRC.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        out |= {f"arbiter/{h}" for h in re.findall(r'"arbiter/([a-z_]+)\.[a-z0-9-]', text)}
        if 'rule_id=f"house/' in text:
            out.add("house/")
    out |= {f"arbiter/{pack}" for pack in load_packs()}
    out |= {f"{p.name.split('.', 1)[0]}/" for p in (SRC / "packs" / "adapters").glob("*.toml")}
    return out


def test_the_enumeration_finds_every_kind_of_prefix():
    found = _emitted_prefixes()
    for expected in ("arbiter/resource", "arbiter/secrets", "arbiter/authored", "arbiter/assurance",
                     "arbiter/python", "arbiter/go", "house/", "semgrep/", "checkov/"):
        assert expected in found, f"{expected} not enumerated: {sorted(found)}"


@pytest.mark.parametrize("prefix", sorted(_emitted_prefixes()))
def test_every_emitted_prefix_maps_to_a_registered_probe(prefix):
    check = prefix + ("x" if prefix.endswith("/") else ".x")
    for probe in controls._probes_of(check):
        assert probe in _probe_names(), f"{check} -> {probe}, which is not a probe"
    assert controls._probe_of(check) in _probe_names()


def test_a_house_rule_maps_to_the_probe_for_its_type():
    house = {"needs-license": "file_exists", "no-bare-except": "ast_query"}
    assert controls._probe_of("house/x") == "house_rules"
    assert controls._probe_of("house/needs-license", house) == "house_rules"
    assert controls._probe_of("house/no-bare-except", house) == "house_rules_ast"
    # Read without its configuration, a house rule needs both probes.
    assert controls._probes_of("house/x") == ("house_rules", "house_rules_ast")
    assert controls._probes_of("house/no-bare-except", house) == ("house_rules_ast",)


def _fw(tmp_path, *checks: str, fid: str = "HOUSE-TEST", cid: str = "C-1"):
    p = tmp_path / f"{fid}.yaml"
    p.write_text(yaml.safe_dump({
        "framework": {"id": fid, "title": "t", "declared_controls": 1},
        "controls": [{"id": cid, "automatable": "partial", "satisfied_by": list(checks)}],
    }))
    return controls.load_pack(p)


def test_a_clean_house_rule_run_satisfies_its_control(tmp_path):
    fw = _fw(tmp_path, "house/needs-license")
    house = {"needs-license": "file_exists"}
    ran = [ProbeOutcome(name="house_rules"), ProbeOutcome(name="house_rules_ast", status="skipped",
                                                          reason="missing python package")]
    assert controls.evaluate(fw, [], ran, house=house)["controls"][0]["state"] == controls.SATISFIED
    # Without the configuration the type is unknown, so the AST probe not
    # running blocks it: not a false pass for an ast_query rule.
    row = controls.evaluate(fw, [], ran)["controls"][0]
    assert row["state"] == controls.NOT_ASSESSED and "missing python package" in row["reason"]
    # A house id the configuration does not define was never checked.
    row = controls.evaluate(fw, [], ran, house={})["controls"][0]
    assert row["state"] == controls.NOT_ASSESSED and "no house rule" in row["reason"]


def test_a_real_scan_reports_a_house_rule_control_as_assessed(tmp_path):
    """End to end, both house probes, with the engine passing the config."""
    if not ts.available() or "python" not in ts.cached_grammars():
        pytest.skip("python grammar not cached")
    packs = tmp_path / "packs"
    packs.mkdir()
    (packs / "fw.yaml").write_text(yaml.safe_dump({
        "framework": {"id": "HOUSE-TEST", "title": "t", "declared_controls": 2},
        "controls": [
            {"id": "C-1", "automatable": "partial", "satisfied_by": ["house/needs-readme"]},
            {"id": "C-2", "automatable": "partial", "satisfied_by": ["house/no-bare-except"]},
        ],
    }))
    code = tmp_path / "code"
    code.mkdir()
    (code / "README.md").write_text("# x\n")
    (code / "a.py").write_text("def f():\n    return 1\n")
    cfg = dict(load_config(None))
    cfg["rules"] = [
        {"id": "needs-readme", "type": "file_exists", "paths": ["README.md"]},
        {"id": "no-bare-except", "type": "ast_query", "languages": ["python"],
         "query": "(except_clause) @hit"},
    ]
    cfg["controls"] = {"frameworks": ["HOUSE-TEST"], "packs": [str(packs)]}
    rep = run_scan([str(code)], cfg, only=["house_rules", "house_rules_ast"],
                   use_adapters=False, use_cache=False)
    counts = {c["framework"]: c["counts"] for c in rep.controls}["HOUSE-TEST"]
    assert counts["satisfied"] == 2 and counts["not_assessed"] == 0, counts


def test_a_real_scan_finding_names_the_probe_its_rule_maps_to(tmp_path):
    """The mapping agrees with what the scanner actually records: every
    finding's `probe` is the probe `_probe_of` names for its rule id."""
    if not ts.available():
        pytest.skip("tree-sitter not installed")
    cfg = dict(load_config(None))
    cfg["rules"] = [
        {"id": "needs-changelog", "type": "file_exists", "paths": ["CHANGELOG.md"]},
        {"id": "no-bare-except", "type": "ast_query", "languages": ["python"],
         "query": "(except_clause) @hit"},
    ]
    targets = [str(ROOT / "fixtures" / "planted" / lang / "bad")
               for lang in sorted(load_packs())
               if ts.ts_name(lang) in ts.cached_grammars()]
    rep = run_scan(targets, cfg, use_adapters=False, use_cache=False)
    house = controls.house_rule_types(cfg)
    seen = set()
    for f in rep.findings:
        if not f.probe or f.rule_id.startswith("arbiter/assurance.cache-"):
            continue
        assert controls._probe_of(f.rule_id, house) == f.probe, (f.rule_id, f.probe)
        seen.add(f.probe)
    assert {"house_rules", "house_rules_ast", "secrets"} <= seen, seen


# ---------------------------------------------------------------------------
# `controls:` declared by language rules
# ---------------------------------------------------------------------------

def test_language_rule_declarations_are_collected():
    declared = controls.declared_checks()
    assert "arbiter/python.http-no-timeout" in declared["NIST-800-218:PW.5"]
    assert "arbiter/python.sql-injection" in declared["NIST-800-53r5:SI-10"]
    assert any(ch.startswith("arbiter/go.") for ch in declared["NIST-800-218:PW.8"])


def test_a_declared_rule_assesses_its_control_per_language(tmp_path):
    fw = _fw(tmp_path, fid="NIST-800-218", cid="PW.5")
    assert not fw.controls[0].satisfied_by
    ran = [ProbeOutcome(name=f"lang_{p}") for p in load_packs()]
    res = controls.evaluate(fw, [], ran)
    row = res["controls"][0]
    # No `satisfied_by` in the pack: before, that was no_coverage.
    assert row["state"] == controls.SATISFIED, row
    assert "arbiter/python.http-no-timeout" in row["declared_by"]
    assert {lang: c["state"] for lang, c in row["languages"].items()} == {
        p: controls.SATISFIED for p in load_packs()}

    # One pack fired: the control is violated, by that language only.
    hit = Finding(rule_id="arbiter/go.http-default-client", title="t",
                  location=Location(path="main.go", start_line=3))
    row = controls.evaluate(fw, [hit], ran)["controls"][0]
    assert row["state"] == controls.VIOLATED
    assert row["languages"]["go"]["state"] == controls.VIOLATED
    assert row["languages"]["python"]["state"] == controls.SATISFIED

    # A pack whose grammar is missing leaves the control not assessed; one
    # whose language is absent from the codebase does not count either way.
    outcomes = [ProbeOutcome(name="lang_python"),
                ProbeOutcome(name="lang_java", status="skipped",
                             reason="missing tree-sitter grammar: java")]
    for p in load_packs():
        if p not in ("python", "java"):
            outcomes.append(ProbeOutcome(name=f"lang_{p}", status="skipped",
                                         reason=f"no {p} detected", applicable=False))
    row = controls.evaluate(fw, [], outcomes)["controls"][0]
    assert row["state"] == controls.NOT_ASSESSED and "grammar: java" in row["reason"]
    assert row["languages"]["java"]["state"] == controls.NOT_ASSESSED
    assert row["languages"]["go"]["state"] == controls.ABSENT


def test_a_declaration_does_not_override_a_not_automatable_control(tmp_path):
    """The pack's judgement stands; the disagreement is shown, not dropped."""
    p = tmp_path / "fw.yaml"
    p.write_text(yaml.safe_dump({
        "framework": {"id": "NIST-800-53r5", "title": "t", "declared_controls": 1},
        "controls": [{"id": "IA-2", "automatable": "none", "residual": "x"}],
    }))
    row = controls.evaluate(controls.load_pack(p), [], [])["controls"][0]
    assert row["state"] == controls.NOT_AUTOMATABLE
    assert row["declared_by"] and "declare this control" in row["reason"]


def test_shipped_declarations_reach_the_shipped_packs():
    """Every `FRAMEWORK:CONTROL` a language rule declares either names a
    control a shipped pack enumerates, or is on this known list (no pack for
    OWASP ASVS; four NIST 800-53 controls not enumerated). A new unresolved
    declaration assesses nothing, so it has to be added here on purpose."""
    known_unresolved = {
        "NIST-800-53r5:SC-13", "NIST-800-53r5:SC-23", "NIST-800-53r5:SI-10",
        "NIST-800-53r5:SI-11",
    }
    unresolved = {ref for ref in controls.unresolved_declarations()
                  if not ref.startswith("OWASP-ASVS:")}
    assert unresolved == known_unresolved
