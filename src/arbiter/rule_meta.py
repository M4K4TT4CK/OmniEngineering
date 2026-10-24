"""Rule metadata that is not detection logic: requirements, tier, precision ref.

Every shipped rule has an entry, wherever its logic lives:

* language-pack rules (`packs/rules/ast/*.yaml`) and resource rules
  (`packs/rules/resource.yaml`) carry the keys on the rule itself;
* rules coded in Python (secrets, supply chain, drift, interface, authored,
  assurance, ...) are listed in `packs/rules/native.yaml`, one entry per rule
  id the source can emit.

Keys, all optional on the rule:

    requirements: [ARB-064]   # the requirement(s) the rule was built or fixed under
    tier: advisory            # verified | advisory; default advisory (on probation)
    precision_ref: <rule id>  # the ledger key, when it is not the rule id

`tier: verified` is a declaration, not a fact: a finding of that rule is
verified only when the precision ledger (precision.py) also admits the rule,
and the CI precision guard fails when a declared rule is not admitted. Rule
ids outside Arbiter's own (`house/...`, `semgrep/...`) have no entry and are
always advisory.
"""
from __future__ import annotations

import re
from functools import cache
from pathlib import Path

PACKS = Path(__file__).parent / "packs" / "rules"
NATIVE = PACKS / "native.yaml"
REQUIREMENT_ID = re.compile(r"^ARB-\d{3}$")
TIERS = ("verified", "advisory", "inferred")
RULE_TIERS = ("verified", "advisory")
META_KEYS = {"requirements", "tier", "precision_ref"}


class RuleMetaError(ValueError):
    """Malformed rule metadata. Raised at load so a typo fails the suite."""


def validate_meta(rule: dict, where: str) -> None:
    reqs = rule.get("requirements")
    if reqs is not None:
        if not isinstance(reqs, list) or not reqs:
            raise RuleMetaError(f"{where}: `requirements` is a non-empty list of ARB ids")
        for r in reqs:
            if not isinstance(r, str) or not REQUIREMENT_ID.match(r):
                raise RuleMetaError(f"{where}: requirement ids look like ARB-064, got {r!r}")
        if len(set(reqs)) != len(reqs):
            raise RuleMetaError(f"{where}: duplicate requirement id")
    tier = rule.get("tier")
    if tier is not None and tier not in RULE_TIERS:
        raise RuleMetaError(f"{where}: `tier` is one of {RULE_TIERS}, got {tier!r}")
    ref = rule.get("precision_ref")
    if ref is not None and (not isinstance(ref, str) or not ref):
        raise RuleMetaError(f"{where}: `precision_ref` is a rule id")


def _yaml(path: Path) -> dict:
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise RuleMetaError(f"{path.name}: expected a mapping with a `rules` list")
    return data


def _entry(rule: dict, source: str, extra: dict | None = None) -> dict:
    return {"requirements": list(rule.get("requirements") or []),
            "tier": rule.get("tier") or "advisory",
            "precision_ref": rule.get("precision_ref") or "",
            "dimension": rule.get("dimension", ""),
            "cwe": list(rule.get("cwe") or []),
            "_source": source, **(extra or {})}


@cache
def shipped_rules() -> dict[str, dict]:
    """rule id -> metadata for every rule Arbiter ships."""
    from .lang_rules import load_packs
    out: dict[str, dict] = {}
    for pack, data in load_packs().items():
        for rule in data["rules"]:
            out[f"arbiter/{pack}.{rule['id']}"] = _entry(rule, "pack")
    res = _yaml(PACKS / "resource.yaml")
    for i, rule in enumerate(res["rules"]):
        validate_meta(rule, f"resource.yaml rule {i} ({rule.get('id', '?')})")
        out[f"arbiter/resource.{rule['id']}"] = _entry(rule, "resource")
    if NATIVE.is_file():
        nat = _yaml(NATIVE)
        for i, rule in enumerate(nat["rules"]):
            rid = rule.get("id")
            where = f"native.yaml rule {i} ({rid or '?'})"
            if not isinstance(rid, str) or not rid.startswith("arbiter/"):
                raise RuleMetaError(f"{where}: `id` is a full rule id, arbiter/<area>.<name>")
            unknown = set(rule) - META_KEYS - {"id", "dimension", "cwe", "note"}
            if unknown:
                raise RuleMetaError(f"{where}: unknown keys {sorted(unknown)}")
            if rid in out:
                raise RuleMetaError(f"{where}: already defined by a pack")
            validate_meta(rule, where)
            out[rid] = _entry(rule, "native")
    return out


def meta(rule_id: str) -> dict | None:
    return shipped_rules().get(rule_id)


NOT_ASSESSED = ".not-assessed"


def requirements_of(rule_id: str) -> list[str]:
    """The rule's requirements. `<rule>.not-assessed` (a check that could not
    be evaluated, e.g. a resource value known only at apply) traces to the
    rule it stands for; its tier stays its own (advisory)."""
    m = meta(rule_id)
    if m is None and rule_id.endswith(NOT_ASSESSED):
        m = meta(rule_id[: -len(NOT_ASSESSED)])
    return list(m["requirements"]) if m else []


def declared_tier(rule_id: str) -> str:
    """The tier the rule's metadata declares; advisory for anything unknown."""
    m = meta(rule_id)
    return m["tier"] if m else "advisory"


def ledger_key(rule_id: str) -> str:
    m = meta(rule_id)
    return (m and m["precision_ref"]) or rule_id


# ---------------------------------------------------------------------------
# What the source can emit (used by the tests and tools/rule_requirements.py)
# ---------------------------------------------------------------------------

_LITERAL = re.compile(r'"(arbiter/[a-z_]{2,}\.[a-z0-9][a-z0-9-]*)"')


def native_rule_ids_from_source() -> set[str]:
    """Every rule id a Python probe can emit, read from the source.

    Literal rule-id strings (arbiter/<area>.<name> in quotes), one per secret pattern, and no pack rule
    (those are enumerated from the packs). Read rather than listed so a new
    rule in Python shows up the day it lands."""
    src = Path(__file__).parent
    out: set[str] = set()
    for path in src.glob("*.py"):
        out |= set(_LITERAL.findall(path.read_text(encoding="utf-8")))
    from .probes import SECRET_PATTERNS
    out |= {f"arbiter/secrets.{s[0]}" for s in SECRET_PATTERNS}
    packed = {r for r, m in shipped_rules().items() if m["_source"] != "native"}
    return out - packed


def _pack_names() -> list[str]:
    from .lang_rules import load_packs
    return list(load_packs())
