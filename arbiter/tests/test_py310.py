"""Arbiter runs on Python 3.10 (REQ-040).

The floor used to be 3.11 for exactly one reason: `tomllib`. Everything else
in the tree is 3.10-clean, so the whole question of the floor reduces to one
import site, and these tests pin that site down from both sides: the fallback
is taken when the standard-library parser is absent, and the tools that CI
runs before anything is installed stay on the standard library.
"""
from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_MANIFEST = 'name = "x"\n[requires]\nbinaries = ["x"]\n'


def test_the_toml_reader_falls_back_to_tomli_below_311(monkeypatch):
    """`sys.modules["tomllib"] = None` is what 3.10 looks like to an import
    statement: the name resolves to nothing and `import tomllib` raises
    ModuleNotFoundError. The stand-in records whether it was asked, so the
    test can tell the fallback path from a parse that happened to succeed."""
    tomli = pytest.importorskip("tomli")
    from arbiter.adapters import _toml_loads

    calls: list[str] = []
    stand_in = types.ModuleType("tomli")

    def loads(text: str) -> dict:
        calls.append(text)
        return tomli.loads(text)

    stand_in.loads = loads  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tomli", stand_in)

    # Control: with the standard-library parser importable, tomli is not asked.
    if sys.version_info >= (3, 11):
        assert _toml_loads(_MANIFEST)["name"] == "x"
        assert calls == [], "tomli was used although tomllib is available"

    monkeypatch.setitem(sys.modules, "tomllib", None)
    data = _toml_loads(_MANIFEST)
    assert data == {"name": "x", "requires": {"binaries": ["x"]}}
    assert calls == [_MANIFEST], "the fallback did not go through tomli"


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("tool", ["tools/integrity.py", "tools/mutate_tests.py"])
def test_integrity_and_mutation_tools_import_only_stdlib(tool):
    """pr-check runs both tools on every cell of the matrix, including the
    3.10 one, and they run before any optional extra is guaranteed. A TOML
    parser or any third-party module there would make the check itself the
    first thing to break on the floor version."""
    names = _top_level_imports(ROOT / tool)
    assert names, f"{tool} imports nothing, which cannot be right"
    stdlib = set(sys.stdlib_module_names) - {"tomllib"}
    allowed = stdlib | {"arbiter"}
    offenders = sorted(n for n in names if n not in allowed)
    assert offenders == [], f"{tool} imports outside the standard library: {offenders}"
