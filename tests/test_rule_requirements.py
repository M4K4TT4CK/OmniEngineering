"""Every shipped rule traces to the requirement(s) it was built or fixed
under (ARB-064), or is listed here as an exception with its reason.

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

# Rules with no requirement, by reason. Every one was introduced on
# 2026-09-12 or 2026-09-13 (commits 8158e1a "Arbiter 0.1.0", bcb5fc6,
# 489a085, 64d028d, efdeb51), before the requirement registry existed, and no
# requirement has changed it since: there is nothing true to cite. A rule
# that a requirement later changes gets that id and leaves this list.
PRE_REGISTRY = {
    # resource.yaml: the provider-neutral IaC policy shipped with 0.1.0
    *(f"arbiter/resource.{r}" for r in (
        "database-allows-plaintext-connections", "database-publicly-accessible", "host-network",
        "k8s-capabilities-not-dropped", "k8s-dangerous-capabilities", "k8s-host-namespace",
        "k8s-host-path-volume", "k8s-mutable-image-tag", "k8s-no-resource-limits",
        "k8s-no-security-context", "k8s-not-run-as-non-root", "k8s-privilege-escalation",
        "k8s-privilege-escalation-not-disabled", "k8s-runs-as-root", "k8s-writable-root-filesystem",
        "no-deletion-protection", "no-https-redirect", "no-log-retention", "no-object-store-logging",
        "plaintext-listener", "privileged-container", "public-object-store", "source-is-literal-hcl",
        "unencrypted-database", "unencrypted-object-store", "unencrypted-queue", "unencrypted-volume",
        "unknown-until-apply", "unrestricted-ingress", "weak-tls-version")),
    # secrets: fixed-prefix token patterns (0.1.0 and efdeb51)
    *(f"arbiter/secrets.{r}" for r in (
        "anthropic-key", "aws-access-key", "gh-token", "gitlab-token", "google-api-key", "jwt",
        "npm-token", "openai-key", "pg-url", "pypi-token", "sendgrid-key", "slack-token",
        "slack-webhook", "stripe-key")),
    *(f"arbiter/supply.{r}" for r in (
        "pull-request-target", "unpinned-action", "unpinned-npm-dep", "unpinned-python-dep")),
    *(f"arbiter/interface.{r}" for r in (
        "constant-disagreement", "env-var-never-provided", "env-var-provided-but-unused",
        "permission-not-granted", "permission-unused", "port-not-exposed")),
    *(f"arbiter/contract.{r}" for r in (
        "documented-route-not-registered", "dropped-column-still-referenced", "spec-not-compared")),
    "arbiter/ast.deep-nesting", "arbiter/ast.function-too-long", "arbiter/ast.high-complexity",
    "arbiter/quality.file-too-long", "arbiter/quality.no-tests", "arbiter/quality.todo-density",
    "arbiter/drift.broken-doc-link", "arbiter/drift.documented-env-var-absent",
    "arbiter/assurance.analysis-excluded", "arbiter/assurance.suppression-census",
    "arbiter/authored.docstring-promises-absent-behaviour",
    "arbiter/authored.import-of-nonexistent-package", "arbiter/authored.stub-on-production-path",
    "arbiter/judgement.claim-contradicts-code",
}


def _registry() -> set[str]:
    ids = set()
    for name in ("requirements.json", "requirements-archive.json"):
        d = json.loads((ROOT / ".ai" / "requirements" / name).read_text())
        ids |= {r["id"] for r in d["requirements"]}
    return ids


def test_every_shipped_rule_has_a_requirement_or_is_a_listed_exception():
    rules = rule_meta.shipped_rules()
    missing = sorted(r for r, m in rules.items() if not m["requirements"] and r not in PRE_REGISTRY)
    assert not missing, f"rules with no requirement and no listed reason: {missing}"
    stale = sorted(r for r in PRE_REGISTRY if r in rules and rules[r]["requirements"])
    assert not stale, f"now traced, remove from PRE_REGISTRY: {stale}"
    assert PRE_REGISTRY <= set(rules), sorted(PRE_REGISTRY - set(rules))


def test_most_rules_are_traced():
    rules = rule_meta.shipped_rules()
    traced = sum(1 for m in rules.values() if m["requirements"])
    assert traced >= 157 and len(rules) >= 228


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


def test_a_pre_registry_native_rule_says_why_it_has_no_requirement():
    import yaml
    for rule in yaml.safe_load(rule_meta.NATIVE.read_text())["rules"]:
        assert rule.get("requirements") or rule.get("note"), rule["id"]


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
