"""A control covered by a language-pack rule is assessed by that pack's probe.

`controls._probe_of("arbiter/python.x")` returned "python". No probe has that
name (the pack probe is `lang_python`), so a control satisfied by a
language-pack rule always read as not assessed, even after the pack ran
clean (ARB-062 acceptance criterion 5).
"""
from __future__ import annotations

import pytest
import yaml

from arbiter import ast as ts
from arbiter import controls
from arbiter.core import Finding, Location, ProbeOutcome
from arbiter.engine import run_scan
from arbiter.lang_rules import load_packs
from arbiter.policy import load_config

# One rule per pack, and one clean file per language for the real scan.
LANGS = {
    "python": ("http-no-timeout", "app.py", "def add(a, b):\n    return a + b\n"),
    "go": ("http-default-client", "main.go", "package main\n\nfunc add(a, b int) int { return a + b }\n"),
    "java": ("http-client-no-timeout", "App.java",
             "class App {\n  int add(int a, int b) { return a + b; }\n}\n"),
    "ruby": ("endless-worker-loop", "app.rb", "def add(a, b)\n  a + b\nend\n"),
    "typescript": ("eval-call", "app.ts", "export function add(a: number, b: number) { return a + b; }\n"),
}
NATIVE = {
    "arbiter/resource.x": "resource_policy", "arbiter/secrets.x": "secrets",
    "arbiter/supply.x": "supply_chain", "arbiter/drift.x": "doc_drift",
    "arbiter/quality.x": "quality", "arbiter/ast.x": "ast_metrics",
    "arbiter/interface.x": "interface", "arbiter/authored.x": "authored",
    "arbiter/assurance.x": "assurance", "arbiter/concurrency.x": "concurrency",
    "checkov/CKV_AWS_1": "checkov", "bandit/B105": "bandit",
}


@pytest.mark.parametrize("lang", sorted(LANGS))
def test_a_language_pack_check_maps_to_its_pack_probe(lang):
    assert controls._probe_of(f"arbiter/{lang}.x") == f"lang_{lang}"


def test_every_shipped_pack_maps_to_a_registered_probe():
    from arbiter.probes import REGISTRY
    names = {p.name for p in REGISTRY}
    for pack, data in load_packs().items():
        for rule in data["rules"]:
            assert controls._probe_of(f"arbiter/{pack}.{rule['id']}") in names


@pytest.mark.parametrize("check,probe", sorted(NATIVE.items()))
def test_native_and_adapter_checks_keep_their_mapping(check, probe):
    assert controls._probe_of(check) == probe


def _fw(tmp_path, rule: str):
    p = tmp_path / "fw.yaml"
    p.write_text(yaml.safe_dump({
        "framework": {"id": "TEST", "title": "Test", "declared_controls": 1},
        "controls": [{"id": "C-1", "automatable": "partial", "satisfied_by": [rule]}],
    }))
    return controls.load_pack(p)


@pytest.mark.parametrize("lang", sorted(LANGS))
def test_a_clean_pack_run_satisfies_its_control_and_a_skipped_one_does_not(tmp_path, lang):
    rule = f"arbiter/{lang}.{LANGS[lang][0]}"
    fw = _fw(tmp_path, rule)
    ran = controls.evaluate(fw, [], [ProbeOutcome(name=f"lang_{lang}")])
    assert ran["controls"][0]["state"] == controls.SATISFIED
    skipped = controls.evaluate(fw, [], [ProbeOutcome(
        name=f"lang_{lang}", status="skipped", reason="grammar not cached")])
    row = skipped["controls"][0]
    assert row["state"] == controls.NOT_ASSESSED and "grammar not cached" in row["reason"]
    hit = Finding(rule_id=rule, title="t", location=Location(path="a"))
    fired = controls.evaluate(fw, [hit], [ProbeOutcome(name=f"lang_{lang}")])
    assert fired["controls"][0]["state"] == controls.VIOLATED


@pytest.mark.parametrize("lang", sorted(LANGS))
def test_a_real_scan_reports_a_language_control_as_assessed(tmp_path, lang):
    """End to end: the controls summary in a scan report, per language."""
    grammars = {"typescript": {"typescript"}}.get(lang, {ts.ts_name(lang)})
    if not ts.available() or not grammars <= ts.cached_grammars():
        pytest.skip(f"{lang} grammar not cached")
    rule_id, name, src = LANGS[lang]
    packs = tmp_path / "packs"
    packs.mkdir()
    (packs / "fw.yaml").write_text(yaml.safe_dump({
        "framework": {"id": "LANG-TEST", "title": "t", "declared_controls": 1},
        "controls": [{"id": "C-1", "automatable": "partial",
                      "satisfied_by": [f"arbiter/{lang}.{rule_id}"]}],
    }))
    code = tmp_path / "code"
    code.mkdir()
    (code / name).write_text(src)
    cfg = dict(load_config(None))
    cfg["controls"] = {"frameworks": ["LANG-TEST"], "packs": [str(packs)]}
    rep = run_scan([str(code)], cfg, only=[f"lang_{lang}"], use_adapters=False, use_cache=False)
    assert [o.status for o in rep.probes if o.name == f"lang_{lang}"] == ["ran"]
    counts = {c["framework"]: c["counts"] for c in rep.controls}["LANG-TEST"]
    assert counts["satisfied"] == 1 and counts["not_assessed"] == 0, counts
