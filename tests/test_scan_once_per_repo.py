"""Per-repository facts are built once per scan, not once per file (ARB-076).

`facts.setdefault(key, _ecosystem_facts(...))` evaluates its argument every
time, so the authored probe rebuilt a repository's declared and local names
-- every manifest, every Java file's package -- for each file it looked at:
quadratic, 88 s of keycloak `services/` alone. `apply_scope_notes` had the
same shape per finding. These tests count the calls during a real scan.
"""
from __future__ import annotations

from arbiter import authored, scope_notes
from arbiter.engine import run_scan
from arbiter.policy import load_config

_POM = """<project><groupId>org.example</groupId><artifactId>demo</artifactId>
<dependencies><dependency><groupId>com.google.guava</groupId>
<artifactId>guava</artifactId></dependency></dependencies></project>
"""

_JAVA = """package org.example.p{n};

import com.google.common.collect.ImmutableList;
import org.unknown.lib.Thing;

public class C{n} {{
    public ImmutableList<Thing> all() {{ return ImmutableList.of(); }}
}}
"""


def _tree(tmp_path, files: int):
    (tmp_path / "pom.xml").write_text(_POM)
    src = tmp_path / "src" / "main" / "java"
    for n in range(files):
        d = src / f"p{n}"
        d.mkdir(parents=True)
        (d / f"C{n}.java").write_text(_JAVA.format(n=n))
    (tmp_path / "README.md").write_text(
        "# Demo\n\n## Out of scope\n\n- `src/main/java/p0` is covered by the gateway.\n")


def _count(monkeypatch, module, name) -> list:
    calls: list = []
    real = getattr(module, name)

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, counting)
    return calls


def test_ecosystem_facts_are_built_once_per_repo_and_language(tmp_path, monkeypatch):
    _tree(tmp_path, 12)
    calls = _count(monkeypatch, authored, "_ecosystem_facts")
    report = run_scan([str(tmp_path)], load_config(None), only=["authored"],
                      use_adapters=False, use_cache=False)
    undeclared = [f for f in report.findings if f.rule_id == "arbiter/authored.undeclared-import"]
    # The facts still do their job: guava is declared, org.unknown is not.
    assert {f.location.path for f in undeclared} == {
        f"src/main/java/p{n}/C{n}.java" for n in range(12)}
    assert all("org.unknown" in f.title for f in undeclared)
    assert len(calls) == 1, f"_ecosystem_facts ran {len(calls)} times for 12 Java files"


def test_scope_notes_are_extracted_once_per_repo(tmp_path, monkeypatch):
    _tree(tmp_path, 12)
    calls = _count(monkeypatch, scope_notes, "extract_scope_notes")
    report = run_scan([str(tmp_path)], load_config(None), only=["authored"],
                      use_adapters=False, use_cache=False)
    assert len([f for f in report.findings if not f.suppressed]) >= 12
    assert len(calls) == 1, f"extract_scope_notes ran {len(calls)} times"
    noted = [f for f in report.findings if f.scope_note]
    assert {f.location.path for f in noted} == {"src/main/java/p0/C0.java"}
