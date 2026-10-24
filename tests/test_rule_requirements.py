"""Every shipped rule traces to the requirement(s) it was built or fixed
under (ARB-064). No exceptions: the rules that shipped before the registry
existed trace to the group requirements ARB-084..ARB-090 written for them.

The mapping is rule metadata: `requirements:` on each pack rule and in
`packs/rules/native.yaml` for rules coded in Python, proposed from the
CHANGELOG, pack comments, the registry and the introducing commit by
tools/rule_requirements.py and reviewed by hand. It reaches every finding
(`Finding.requirements`) and the report's top-level `rules` block.
"""
from __future__ import annotations

import json

import pytest
from helpers import ROOT

from arbiter import rule_meta
from arbiter.lang_rules import PackError, validate_pack

def _registry() -> set[str]:
    ids = set()
    for name in ("requirements.json", "requirements-archive.json"):
        d = json.loads((ROOT / ".ai" / "requirements" / name).read_text())
        ids |= {r["id"] for r in d["requirements"]}
    return ids


def test_every_shipped_rule_has_a_requirement_in_the_registry():
    """Strict: no exceptions. A rule shipped before the registry traces to the
    group requirement written for it (ARB-084..ARB-090)."""
    known = _registry()
    rules = rule_meta.shipped_rules()
    missing = sorted(r for r, m in rules.items() if not m["requirements"])
    assert not missing, f"rules with no requirement: {missing}"
    dangling = {r: sorted(set(m["requirements"]) - known) for r, m in rules.items()
                if set(m["requirements"]) - known}
    assert not dangling, f"rules citing ids not in the registry or its archive: {dangling}"


def test_most_rules_are_traced():
    rules = rule_meta.shipped_rules()
    traced = sum(1 for m in rules.values() if m["requirements"])
    assert traced == len(rules) and len(rules) >= 263


def test_every_cited_requirement_exists():
    known = _registry()
    for rule, m in rule_meta.shipped_rules().items():
        unknown = set(m["requirements"]) - known
        assert not unknown, f"{rule} cites {sorted(unknown)}, not in the registry or its archive"


def test_native_yaml_lists_exactly_the_rules_the_source_can_emit():
    native = {r for r, m in rule_meta.shipped_rules().items() if m["_source"] == "native"}
    emitted = rule_meta.native_rule_ids_from_source()
    assert native == emitted, (f"missing from native.yaml: {sorted(emitted - native)}; "
                               f"listed but never emitted: {sorted(native - emitted)}")


def test_a_not_assessed_finding_traces_to_the_rule_it_stands_for():
    assert rule_meta.requirements_of("arbiter/resource.unencrypted-volume.not-assessed") == \
        rule_meta.requirements_of("arbiter/resource.unencrypted-volume") == ["ARB-085"]
    assert rule_meta.requirements_of("arbiter/nope.not-assessed") == []


def test_every_native_yaml_entry_lists_requirements():
    import yaml
    for rule in yaml.safe_load(rule_meta.NATIVE.read_text())["rules"]:
        assert rule.get("requirements"), rule["id"]


def test_every_arbiter_finding_in_a_scan_carries_requirements(legacy_report, system_report,
                                                               plan_report):
    """End to end: every arbiter/* finding of a real scan names a requirement,
    in the Finding and in the JSON report (quality, drift, secrets, supply,
    interface, contract and resource rules among them)."""
    known = _registry()
    seen = set()
    for report in (legacy_report, system_report, plan_report):
        data = json.loads(json.dumps(report.to_dict(), default=str))
        for f in data["findings"]:
            if not f["rule_id"].startswith("arbiter/"):
                continue
            seen.add(f["rule_id"])
            assert f["requirements"], f["rule_id"]
            assert set(f["requirements"]) <= known, f["rule_id"]
    assert {r.split(".")[0] for r in seen} >= {
        "arbiter/quality", "arbiter/resource", "arbiter/interface", "arbiter/secrets"}, sorted(seen)


@pytest.mark.parametrize("bad,msg", [
    ({"requirements": ["REQ-064"]}, "look like ARB-064"),
    ({"requirements": []}, "non-empty list"),
    ({"requirements": ["ARB-061", "ARB-061"]}, "duplicate"),
    ({"tier": "gold"}, "tier"),
])
def test_bad_metadata_fails_at_pack_load(bad, msg):
    rule = {"id": "x", "title": "t", "query": "(identifier) @hit", "remediation": "r", **bad}
    with pytest.raises(PackError, match=msg):
        validate_pack({"languages": ["python"], "rules": [rule]}, "test.yaml")


def test_metadata_keys_are_accepted_by_the_pack_schema():
    rule = {"id": "x", "title": "t", "query": "(identifier) @hit", "remediation": "r",
            "requirements": ["ARB-064"], "tier": "advisory", "precision_ref": "arbiter/python.x"}
    validate_pack({"languages": ["python"], "rules": [rule]}, "test.yaml")


def test_the_proposer_finds_changelog_and_pack_evidence():
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("rule_requirements", ROOT / "tools" / "rule_requirements.py")
    rr = importlib.util.module_from_spec(spec)
    sys.modules["rule_requirements"] = rr
    spec.loader.exec_module(rr)
    known, aliases = rr.registry()
    rules = rr.all_rules()
    cl = rr.from_changelog(rules, known, aliases)
    assert "ARB-062" in {i for e in cl["arbiter/go.ssrf-request-url"] for i in e["ids"]}
    packs = rr.from_packs(rules, known, aliases)
    assert any(e["source"] == "pack-header" and e["ids"] == ["ARB-061"]
               for e in packs["arbiter/typescript.eval-call"])
    assert rr.canonical("REQ-038", known, aliases) == "ARB-038"
    bullets = rr.changelog_bullets("### d\n- ARB-001, ARB-002 | `a`\n  more\n- plain\n")
    assert bullets == ["ARB-001, ARB-002 | `a`\n  more", "plain"]
