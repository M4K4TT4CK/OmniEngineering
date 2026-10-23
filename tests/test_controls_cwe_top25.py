"""The CWE Top 25 control pack agrees with the matrix in docs/cwe-top25.md.

The pack (`packs/controls/cwe-top25-2024.yaml`) gives each weakness one cell
per language: applies, not applicable (with a reason) or out of reach. An
applying cell is assessed by the language pack's rules tagged with the
weakness's CWE. These tests hold every cell to the doc, so a native rule, a
doc edit or a pack edit that leaves the other two behind fails here
(ARB-062 criterion 5).
"""
from __future__ import annotations

import re

import pytest
from helpers import ROOT

from arbiter import ast as ts
from arbiter import controls
from arbiter.core import Finding, Location, ProbeOutcome
from arbiter.engine import run_scan
from arbiter.lang_rules import load_packs
from arbiter.policy import load_config

DOC = ROOT / "docs" / "cwe-top25.md"
PACK = ROOT / "src" / "arbiter" / "packs" / "controls" / "cwe-top25-2024.yaml"
FW_ID = "CWE-Top25-2024"
DOC_LANGS = {"Python": "python", "TypeScript": "typescript", "Go": "go", "Java": "java",
             "Ruby": "ruby"}


def _section(title: str) -> list[str]:
    text = DOC.read_text(encoding="utf-8")
    body = text.split(f"\n{title}\n", 1)[1].splitlines()
    start = next(i for i, ln in enumerate(body) if ln.startswith("|"))
    rows = []
    for ln in body[start:]:
        if not ln.startswith("|"):
            break
        rows.append(ln)
    return rows


def _doc_matrix() -> dict[str, dict[str, str]]:
    """CWE-N -> {language pack: mark}, from the doc's matrix table."""
    rows = _section("## The matrix")
    header = [c.strip() for c in rows[0].strip("|").split("|")]
    langs = [DOC_LANGS[h] for h in header[2:]]
    out: dict[str, dict[str, str]] = {}
    for row in rows[2:]:
        cells = [c.strip() for c in row.strip("|").split("|")]
        if not cells[0].isdigit():
            break
        cwe = "CWE-" + cells[1].split()[0]
        out[cwe] = {lang: cell.replace("**", "").strip() for lang, cell in zip(langs, cells[2:])}
    return out


def _doc_native_counts() -> dict[str, int]:
    """Language -> the 'Native' column of the measured-coverage table."""
    rows = _section("### Measured coverage per language")
    header = [c.strip() for c in rows[0].strip("|").split("|")]
    col = header.index("Native")
    return {DOC_LANGS[c[0]]: int(c[col]) for c in
            ([x.strip() for x in r.strip("|").split("|")] for r in rows[2:])}


def _native(mark: str) -> bool:
    return "N" in re.sub(r"[†*\s]", "", mark).split("+")


def _pack() -> controls.Framework:
    return controls.load_pack(PACK)


def test_the_pack_ships_and_lists_the_doc_weaknesses_in_rank_order():
    fw = _pack()
    assert fw.id == FW_ID and fw.declared_controls == 25
    assert FW_ID in {f.id for f in controls.load_frameworks()}
    assert [c.id for c in fw.controls] == list(_doc_matrix())
    assert [c.family for c in fw.controls] == [f"rank {i}" for i in range(1, 26)]


@pytest.mark.parametrize("cwe", list(_doc_matrix()))
def test_every_cell_agrees_with_the_doc_matrix(cwe):
    doc = _doc_matrix()[cwe]
    control = {c.id: c for c in _pack().controls}[cwe]
    assert set(control.languages) == set(doc) == set(load_packs())
    for lang, mark in doc.items():
        cell = control.languages[lang]
        assert cell["measured"] == mark, f"{cwe} {lang}: pack says {cell['measured']!r}, doc {mark!r}"
        want = {"n/a": controls.NOT_APPLICABLE, "OOR": controls.OUT_OF_REACH}.get(
            mark, controls.APPLIES)
        assert cell["status"] == want, f"{cwe} {lang}"
        if want != controls.APPLIES:
            assert cell["reason"], f"{cwe} {lang}: a {want} cell must say why"
            if want == controls.NOT_APPLICABLE:
                assert not cell["checks"]
            continue
        # The load-bearing check: a native cell (N) has a native rule behind
        # it, and a cell the doc calls a gap or external-only has none. A new
        # rule tagged with this CWE flips the second, and the doc must follow.
        assert bool(cell["checks"]) == _native(mark), (
            f"{cwe} {lang}: doc mark {mark!r}, native checks {cell['checks']}")


def test_the_native_column_of_the_coverage_table_matches_the_pack():
    fw = _pack()
    counted = {lang: sum(1 for c in fw.controls
                         if c.languages[lang]["status"] == controls.APPLIES
                         and c.languages[lang]["checks"])
               for lang in load_packs()}
    assert counted == _doc_native_counts()


def test_every_check_the_pack_resolves_is_a_real_rule():
    known = {f"arbiter/{p}.{r['id']}" for p, d in load_packs().items() for r in d["rules"]}
    src = (ROOT / "src" / "arbiter" / "probes.py").read_text()
    known |= set(re.findall(r'rule_id=f?"(arbiter/secrets\.[a-z-]+)"', src))
    known |= {f"arbiter/secrets.{m}" for m in re.findall(r'^\s+\("([a-z-]+)",', src, re.MULTILINE)}
    for c in _pack().controls:
        for ch in c.satisfied_by:
            assert ch in known, f"{c.id} -> {ch}"


def _outcomes(present: set[str], blocked: dict[str, str] | None = None):
    out = []
    for p in load_packs():
        if p in (blocked or {}):
            out.append(ProbeOutcome(name=f"lang_{p}", status="skipped", reason=blocked[p]))
        elif p in present:
            out.append(ProbeOutcome(name=f"lang_{p}"))
        else:
            out.append(ProbeOutcome(name=f"lang_{p}", status="skipped",
                                    reason=f"no {p} detected", applicable=False))
    return out + [ProbeOutcome(name="secrets")]


def test_a_clean_run_reads_assessed_gap_out_of_reach_and_na_per_language():
    """Per language, the summary is the doc matrix: N cells assessed, other
    applying cells gaps, OOR cells out of reach, n/a cells not applicable."""
    langs = set(load_packs())
    res = controls.evaluate(_pack(), [], _outcomes(langs))
    doc = _doc_matrix()
    for lang in langs:
        marks = [doc[c][lang] for c in doc]
        want = {
            controls.SATISFIED: sum(_native(m) for m in marks),
            controls.NO_COVERAGE: sum(m not in ("n/a", "OOR") and not _native(m) for m in marks),
            controls.OUT_OF_REACH: marks.count("OOR"),
            controls.NOT_APPLICABLE: marks.count("n/a"),
        }
        got = {k: v for k, v in res["languages"][lang].items() if v}
        assert got == want, lang
    # The memory-safety rows apply nowhere, so the control is not applicable;
    # that is a state of its own, not not_automatable.
    states = {r["id"]: r["state"] for r in res["controls"]}
    for cwe in ("CWE-787", "CWE-125", "CWE-416", "CWE-119"):
        assert states[cwe] == controls.NOT_APPLICABLE
    assert states["CWE-89"] == controls.SATISFIED
    assert states["CWE-862"] == controls.NO_COVERAGE
    assert res["counts"][controls.NOT_AUTOMATABLE] == 0


def test_applicability_follows_the_languages_present():
    """CWE-190 is a gap in Go and n/a in Python: in a Python-only codebase it
    is not applicable, in a Go one it is a gap. CWE-502 is n/a in Go."""
    py = {r["id"]: r for r in controls.evaluate(_pack(), [], _outcomes({"python"}))["controls"]}
    go = {r["id"]: r for r in controls.evaluate(_pack(), [], _outcomes({"go"}))["controls"]}
    assert py["CWE-190"]["state"] == controls.NOT_APPLICABLE
    assert "arbitrary-precision" in py["CWE-190"]["reason"]
    assert go["CWE-190"]["state"] == controls.NO_COVERAGE
    assert go["CWE-502"]["state"] == controls.NOT_APPLICABLE
    assert py["CWE-502"]["state"] == controls.SATISFIED
    assert go["CWE-89"]["languages"]["python"]["state"] == controls.ABSENT


def test_a_finding_violates_only_its_own_language_cell():
    langs = {"python", "go"}
    hit = Finding(rule_id="arbiter/go.sql-injection", title="t",
                  location=Location(path="db.go", start_line=1))
    # The secrets probe is language-independent: it counts against the
    # language of the file it is in.
    cred = Finding(rule_id="arbiter/secrets.assigned-credential", title="t",
                   location=Location(path="settings.py", start_line=1))
    rows = {r["id"]: r for r in controls.evaluate(_pack(), [hit, cred], _outcomes(langs))["controls"]}
    assert rows["CWE-89"]["state"] == controls.VIOLATED
    assert rows["CWE-89"]["languages"]["go"]["state"] == controls.VIOLATED
    assert rows["CWE-89"]["languages"]["python"]["state"] == controls.SATISFIED
    assert rows["CWE-798"]["languages"]["python"]["state"] == controls.VIOLATED
    assert rows["CWE-798"]["languages"]["go"]["state"] == controls.SATISFIED


def test_a_partial_signal_in_an_out_of_reach_cell_can_violate_but_never_satisfy():
    langs = {"go"}
    clean = {r["id"]: r for r in controls.evaluate(_pack(), [], _outcomes(langs))["controls"]}
    assert clean["CWE-863"]["languages"]["go"]["state"] == controls.OUT_OF_REACH
    stub = Finding(rule_id="arbiter/go.stub-on-security-path", title="t",
                   location=Location(path="auth.go", start_line=1))
    hit = {r["id"]: r for r in controls.evaluate(_pack(), [stub], _outcomes(langs))["controls"]}
    assert hit["CWE-863"]["state"] == controls.VIOLATED


def test_a_language_whose_grammar_is_missing_reads_not_assessed():
    res = controls.evaluate(_pack(), [], _outcomes(
        {"python"}, blocked={"java": "missing tree-sitter grammar: java"}))
    rows = {r["id"]: r for r in res["controls"]}
    assert rows["CWE-89"]["state"] == controls.NOT_ASSESSED
    assert "java" in rows["CWE-89"]["reason"]
    assert res["languages"]["java"][controls.NOT_ASSESSED] > 0
    assert res["languages"]["java"][controls.SATISFIED] == 0


@pytest.mark.parametrize("lang", sorted(DOC_LANGS.values()))
def test_a_real_scan_of_the_planted_corpus_reports_per_language(lang):
    """End to end on each language's planted bad tree: the summary is in the
    scan report, and the language's native cells are assessed, not skipped."""
    grammars = {"typescript": {"typescript"}}.get(lang, {ts.ts_name(lang)})
    if not ts.available() or not grammars <= ts.cached_grammars():
        pytest.skip(f"{lang} grammar not cached")
    cfg = dict(load_config(None))
    cfg["controls"] = {"frameworks": [FW_ID]}
    rep = run_scan([str(ROOT / "fixtures" / "planted" / lang / "bad")], cfg,
                   only=[f"lang_{lang}", "secrets"], use_adapters=False, use_cache=False)
    summary = {c["framework"]: c for c in rep.controls}[FW_ID]
    cells = summary["languages"][lang]
    doc = _doc_matrix()
    native = sum(_native(doc[c][lang]) for c in doc)
    assert cells[controls.NOT_ASSESSED] == 0, cells
    # The bad tree plants a defect for every native cell, so each one fires
    # (and Go's out-of-reach CWE-863 cell fires its partial signal too).
    assert cells[controls.SATISFIED] == 0 and cells[controls.VIOLATED] >= native, cells
    assert cells[controls.NOT_APPLICABLE] == sum(doc[c][lang] == "n/a" for c in doc)
