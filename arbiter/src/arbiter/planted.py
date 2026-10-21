"""The planted corpus: known defects, each beside a correct twin.

`fixtures/planted/<lang>/bad/` holds code with defects. Each defect line ends
with a marker comment naming the rule(s) that must report it:

    resp, err := http.Get(url) // expect: arbiter/go.http-default-client

`fixtures/planted/<lang>/good/` holds the same code done correctly. Nothing a
marker names may fire there. A line may carry several rules separated by
commas. A marker with `expect-ext:` names an external analyzer's rule
(semgrep, bandit, ...): it is scored by `tools/planted_score.py` but not
asserted by the fast test suite, which runs without analyzers.

Markers live on the defect line itself, so they cannot drift away from it the
way a separate list of line numbers would.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

MARKER = re.compile(r"(?:#|//|--)\s*expect(?P<ext>-ext)?:\s*(?P<rules>[\w./:,\- ]+?)\s*(?:\*/)?\s*$")
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".go", ".java", ".rb"}


@dataclass
class Expected:
    rule: str
    path: str
    line: int
    external: bool = False


@dataclass
class Score:
    language: str
    expected: list[Expected] = field(default_factory=list)
    caught: list[Expected] = field(default_factory=list)
    missed: list[Expected] = field(default_factory=list)
    unexpected_bad: list[tuple[str, str, int]] = field(default_factory=list)
    false_positives_good: list[tuple[str, str, int]] = field(default_factory=list)


def markers(root: Path) -> list[Expected]:
    out: list[Expected] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = MARKER.search(line)
            if not m:
                continue
            for rule in (r.strip() for r in m.group("rules").split(",")):
                if rule:
                    out.append(Expected(rule=rule, path=rel, line=i, external=bool(m.group("ext"))))
    return out


def languages(corpus: Path) -> list[str]:
    return sorted(p.name for p in corpus.iterdir() if (p / "bad").is_dir()) if corpus.is_dir() else []


def score(corpus: Path, language: str, use_adapters: bool = False) -> Score:
    from .engine import run_scan
    from .policy import load_config

    bad, good = corpus / language / "bad", corpus / language / "good"
    expected = markers(bad)
    if not use_adapters:
        expected = [e for e in expected if not e.external]
    owned = {e.rule for e in expected}

    cfg = load_config(None)
    found_bad = run_scan([str(bad)], cfg, use_adapters=use_adapters, use_cache=False).active()
    at = {(f.rule_id, f.location.path, f.location.start_line) for f in found_bad}
    # A rule that anchors one line off (a decorator, a multi-line call) still
    # counts when it reports within the defect's own statement.
    near = {(f.rule_id, f.location.path, ln) for f in found_bad
            for ln in range(f.location.start_line, max(f.location.end_line, f.location.start_line) + 1)}

    s = Score(language=language, expected=expected)
    for e in expected:
        (s.caught if (e.rule, e.path, e.line) in at | near else s.missed).append(e)
    marked = {(e.rule, e.path, e.line) for e in expected}
    s.unexpected_bad = sorted(k for k in at if k[0] in owned and k not in marked
                              and not any((k[0], k[1], ln) in marked
                                          for ln in range(k[2], k[2] + 1)))
    if good.is_dir():
        found_good = run_scan([str(good)], cfg, use_adapters=use_adapters, use_cache=False).active()
        s.false_positives_good = sorted((f.rule_id, f.location.path, f.location.start_line)
                                        for f in found_good if f.rule_id in owned)
    return s
