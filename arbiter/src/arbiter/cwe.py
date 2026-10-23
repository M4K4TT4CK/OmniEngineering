"""CWE classification for every finding, native or external.

Native rules declare their CWE here (or, for a language pack rule, in the pack
itself). External analyzers report it in their own output, read through the
adapter manifest's `cwe` / `cwe_const` map keys and normalised by `normalise`.
A finding that already carries a CWE keeps it: the closer to the rule the
classification is made, the better it is.
"""
from __future__ import annotations

import fnmatch
import re
from typing import Any

# Longest pattern wins; patterns are fnmatch globs over rule_id.
NATIVE: dict[str, list[str]] = {
    "arbiter/secrets.*": ["CWE-798"],
    "arbiter/authored.security-check-disabled": ["CWE-295"],
    "arbiter/authored.undeclared-import": ["CWE-1357"],
    "arbiter/authored.import-of-nonexistent-package": ["CWE-1357", "CWE-494"],
    "arbiter/authored.stub-on-production-path": ["CWE-1164"],
    "arbiter/concurrency.*": ["CWE-362"],
    "arbiter/concurrency.unsynchronised-counter": ["CWE-362", "CWE-366"],
    "arbiter/supply.unpinned-*": ["CWE-1357", "CWE-494"],
    "arbiter/supply.pull-request-target": ["CWE-829"],
    "arbiter/assurance.test-asserts-nothing": ["CWE-1164"],
    "arbiter/ast.high-complexity": ["CWE-1121"],
    "arbiter/ast.deep-nesting": ["CWE-1124"],
    "arbiter/ast.function-too-long": ["CWE-1080"],
}

_ID = re.compile(r"CWE[-_ ]?(\d+)", re.IGNORECASE)


def normalise(value: Any) -> list[str]:
    """"CWE-89: Improper ...", 89, {"id": 89}, ["CWE-79", ...] -> ["CWE-89", ...]."""
    out: list[str] = []
    items = value if isinstance(value, (list, tuple)) else [value]
    for item in items:
        if isinstance(item, dict):
            item = item.get("id", "")
        if isinstance(item, int) or (isinstance(item, str) and item.strip().isdigit()):
            out.append(f"CWE-{int(item)}")
            continue
        out += [f"CWE-{n}" for n in _ID.findall(str(item or ""))]
    seen: set[str] = set()
    return [c for c in out if not (c in seen or seen.add(c))]


def for_rule(rule_id: str) -> list[str]:
    best: tuple[int, list[str]] | None = None
    for pattern, cwes in NATIVE.items():
        if fnmatch.fnmatchcase(rule_id, pattern) and (best is None or len(pattern) > best[0]):
            best = (len(pattern), cwes)
    return list(best[1]) if best else []


def apply(findings: list) -> None:
    for f in findings:
        if not f.cwe:
            f.cwe = for_rule(f.rule_id)
