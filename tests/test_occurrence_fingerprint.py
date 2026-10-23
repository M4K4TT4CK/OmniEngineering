"""Same-evidence repeats in one file each count, and old baselines keep matching (ARB-080).

The fingerprint excludes line numbers so a baseline survives reformatting.
That made six identical defects in one file one id, and `_dedupe` kept one.
Each distinct site now gets an occurrence ordinal: occurrence 0 keeps the old
id, occurrence k gets a derived one. A baseline cut before this change (schema
1.0) recorded only the shared id; every repeat it hid still matches it, and
`arbiter baseline --migrate` carries it into the new format.
"""
from __future__ import annotations

import json

import pytest

from arbiter import ast as ts
from arbiter.cli import main
from arbiter.core import (
    FINGERPRINT_SCHEME,
    Finding,
    Location,
    Report,
    assign_occurrences,
    occurrence_id,
)
from arbiter.diff import diff_reports
from arbiter.engine import apply_baseline, run_scan, write_baseline
from arbiter.policy import apply_suppressions, load_config

RULE = "arbiter/ruby.rescue-exception"

_METHOD = """\
  def m{n}
    yield
  rescue Exception
    nil
  end
"""


def _ruby(n: int) -> str:
    return "class A\n" + "".join(_METHOD.format(n=i) for i in range(n)) + "end\n"


def _f(line: int, evidence: str = "e:same", path: str = "a.rb", rule: str = "r/x") -> Finding:
    return Finding(rule_id=rule, title="t", evidence=evidence,
                   location=Location(path=path, start_line=line, end_line=line))


def _legacy_id(line: int = 1, **kw) -> str:
    return _f(line, **kw).id  # an id is computed before any ordinal is assigned


needs_ruby = pytest.mark.skipif(not ts.available() or "ruby" not in ts.cached_grammars(),
                                reason="ruby grammar not cached")


def _scan(tmp_path, n: int, baseline=None, cfg=None) -> Report:
    (tmp_path / "code").mkdir(exist_ok=True)
    (tmp_path / "code" / "a.rb").write_text(_ruby(n))
    return run_scan([str(tmp_path / "code")], cfg or load_config(None), only=["lang_ruby"],
                    use_adapters=False, use_cache=False, baseline=baseline)


def _hits(report: Report) -> list[Finding]:
    return [f for f in report.active() if f.rule_id == RULE]


# --------------------------------------------------------------------------
# the ordinal itself
# --------------------------------------------------------------------------

def test_each_site_of_the_same_evidence_gets_its_own_id_in_line_order():
    found = [_f(line) for line in (30, 10, 20)]
    base = found[0].id
    assign_occurrences(found)
    by_line = {f.location.start_line: f for f in found}
    assert by_line[10].id == base and by_line[10].occurrence == 0
    assert by_line[20].id == occurrence_id(base, 1) and by_line[20].base_id == base
    assert by_line[30].id == occurrence_id(base, 2)
    assert len({f.id for f in found}) == 3
    assert {f.fingerprint_base for f in found} == {base}


def test_a_finding_that_occurs_once_keeps_the_id_it_always_had():
    one, other_file, other_rule = _f(5), _f(5, path="b.rb"), _f(9, rule="r/y")
    before = [one.id, other_file.id, other_rule.id]
    assign_occurrences([one, other_file, other_rule])
    assert [one.id, other_file.id, other_rule.id] == before
    assert one.occurrence == 0 and one.base_id == ""


def test_two_reports_of_one_site_still_collapse():
    a, b = _f(7), _f(7)
    assign_occurrences([a, b])
    assert a.id == b.id, "same rule, same evidence, same place: one defect"


def test_ids_round_trip_through_a_report_and_are_not_renumbered():
    found = [_f(1), _f(2)]
    assign_occurrences(found)
    back = [Finding.from_dict(json.loads(json.dumps(f.to_dict()))) for f in found]
    assign_occurrences(back)
    assert [f.id for f in back] == [f.id for f in found]
    assert back[1].base_id == found[0].id


@needs_ruby
def test_six_repeated_defects_in_one_file_count_as_six(tmp_path):
    hits = _hits(_scan(tmp_path, 6))
    assert len(hits) == 6, [f.location.start_line for f in hits]
    assert len({f.id for f in hits}) == 6
    assert len({f.fingerprint_base for f in hits}) == 1


@needs_ruby
def test_a_repeat_keeps_its_ids_across_a_line_shift(tmp_path):
    first = {f.id for f in _hits(_scan(tmp_path, 3))}
    (tmp_path / "code" / "a.rb").write_text("# a comment\n\n" + _ruby(3))
    shifted = run_scan([str(tmp_path / "code")], load_config(None), only=["lang_ruby"],
                       use_adapters=False, use_cache=False)
    assert {f.id for f in _hits(shifted)} == first


# --------------------------------------------------------------------------
# baselines: legacy compatibility, new format, migration
# --------------------------------------------------------------------------

def _legacy_baseline(path, ids, **extra) -> str:
    path.write_text(json.dumps({"schema_version": "1.0", "created": "2026-10-09T20:06:12+00:00",
                                "system": "root", "ids": sorted(ids), **extra}))
    return str(path)


def test_a_legacy_baseline_matches_every_repeat_it_collapsed(tmp_path):
    found = [_f(line) for line in (1, 2, 3)]
    unrelated = _f(4, evidence="e:other")
    base = found[0].id
    bl = _legacy_baseline(tmp_path / "b.json", [base])
    assign_occurrences(found + [unrelated])
    apply_baseline(found + [unrelated], bl)
    assert [f.status for f in found] == ["existing"] * 3
    assert unrelated.status == "new"


def test_a_current_baseline_reports_only_the_added_repeat_as_new(tmp_path):
    found = [_f(line) for line in (1, 2)]
    assign_occurrences(found)
    rep = Report(findings=found, fingerprint=FINGERPRINT_SCHEME)
    write_baseline(rep, str(tmp_path / "b.json"))
    data = json.loads((tmp_path / "b.json").read_text())
    assert data["schema_version"] == "1.1" and data["fingerprint"] == FINGERPRINT_SCHEME
    later = [_f(line) for line in (1, 2, 3)]
    assign_occurrences(later)
    apply_baseline(later, str(tmp_path / "b.json"))
    assert sorted(f.status for f in later) == ["existing", "existing", "new"]


def test_a_baseline_cut_from_a_legacy_report_stays_legacy(tmp_path):
    rep = Report(findings=[_f(1)])  # no fingerprint marker: written before ARB-080
    write_baseline(rep, str(tmp_path / "b.json"))
    data = json.loads((tmp_path / "b.json").read_text())
    assert data["schema_version"] == "1.0" and "fingerprint" not in data


def test_migrating_a_legacy_baseline_keeps_exactly_what_it_accepted(tmp_path):
    accepted = [_f(line) for line in (1, 2, 3)]
    added_later = _f(9, evidence="e:new since the baseline")
    base = accepted[0].id
    gone = "f:000000000000"  # fixed since, or not in this scan: migration never drops it
    old = _legacy_baseline(tmp_path / "old.json", [base, gone],
                           commit="abc1234", config_hash="deadbeef")
    current = accepted + [added_later]
    assign_occurrences(current)
    rep = Report(system="root", findings=current, fingerprint=FINGERPRINT_SCHEME)
    out = tmp_path / "new.json"
    n = write_baseline(rep, str(out), migrate_from=old)
    data = json.loads(out.read_text())
    assert set(data["ids"]) == {f.id for f in accepted} | {gone} and n == 4
    assert added_later.id not in data["ids"], "new against the old baseline stays new"
    assert data["fingerprint"] == FINGERPRINT_SCHEME and data["schema_version"] == "1.1"
    assert data["created"] == "2026-10-09T20:06:12+00:00", "acceptance date is unchanged"
    assert data["commit"] == "abc1234" and data["config_hash"] == "deadbeef"
    assert data["migrated_from"]["schema_version"] == "1.0"
    # Labelling against the migrated baseline equals labelling against the old one.
    fresh = [_f(line) for line in (1, 2, 3)] + [_f(9, evidence="e:new since the baseline")]
    assign_occurrences(fresh)
    apply_baseline(fresh, str(out))
    via_new = [f.status for f in fresh]
    apply_baseline(fresh, old)
    assert via_new == [f.status for f in fresh] == ["existing"] * 3 + ["new"]


def test_migration_refuses_a_report_without_occurrence_ids(tmp_path):
    old = _legacy_baseline(tmp_path / "old.json", [_legacy_id()])
    with pytest.raises(ValueError, match="re-run the scan"):
        write_baseline(Report(findings=[_f(1)]), str(tmp_path / "n.json"), migrate_from=old)


def test_a_suppression_by_the_old_id_covers_every_repeat():
    found = [_f(line) for line in (1, 2, 3)]
    base = found[0].id
    assign_occurrences(found)
    n = apply_suppressions(found, {"suppress": [{"id": base, "reason": "accepted",
                                                 "expires": "2999-01-01"}]})
    assert n == 3 and all(f.suppressed for f in found)


def test_a_diff_from_a_legacy_report_does_not_call_the_repeats_new():
    before = Report(findings=[_f(1)])  # legacy: one collapsed id
    after_found = [_f(line) for line in (1, 2, 3)]
    assign_occurrences(after_found)
    d = diff_reports(before, Report(findings=after_found, fingerprint=FINGERPRINT_SCHEME))
    assert not d.new and not d.fixed and len(d.persisting) == 3
    # Between two current reports an added repeat is new.
    more = [_f(line) for line in (1, 2, 3, 4)]
    assign_occurrences(more)
    d2 = diff_reports(Report(findings=after_found, fingerprint=FINGERPRINT_SCHEME),
                      Report(findings=more, fingerprint=FINGERPRINT_SCHEME))
    assert len(d2.new) == 1 and not d2.fixed


# --------------------------------------------------------------------------
# end to end: the gate, the CLI, and the omni gate's baseline path
# --------------------------------------------------------------------------

def _new_fails(tmp_path) -> str:
    cfg = tmp_path / "arbiter.yaml"
    cfg.write_text("gate:\n  fail_on:\n    severity: null\n    new: low\n")
    return str(cfg)


@needs_ruby
def test_the_gate_stays_green_on_a_legacy_baseline_and_after_migrating_it(tmp_path, capsys):
    """The omni gate runs `arbiter gate . --changed <base> --profile offline
    --baseline .arbiter/baseline.json`; `omni arbiter baseline` adds commit
    and config_hash to that file. Same shape here, on unchanged code."""
    code = tmp_path / "code"
    code.mkdir()
    (code / "a.rb").write_text(_ruby(6))
    cfg = _new_fails(tmp_path)
    first = _scan(tmp_path, 6)
    legacy = [f.id for f in _hits(first)]
    assert len(legacy) == 6 and len({f.fingerprint_base for f in _hits(first)}) == 1
    # What a pre-ARB-080 baseline held: one id per same-evidence group.
    base = {f.fingerprint_base for f in first.active()}
    assert len(base) < len(first.active())
    bl_dir = code / ".arbiter"
    bl_dir.mkdir()
    bl = _legacy_baseline(bl_dir / "baseline.json", base, commit="abc1234", config_hash="x")
    out = tmp_path / "out"
    argv = ["gate", str(code), "--config", cfg, "--profile", "offline", "--only", "lang_ruby",
            "--no-adapters", "--no-cache", "--no-history", "--baseline", bl, "--out", str(out),
            "--format", "json"]
    assert main(argv) == 0, capsys.readouterr().out
    report = json.loads((out / "report.json").read_text())
    assert report["fingerprint"] == FINGERPRINT_SCHEME
    hits = [f for f in report["findings"] if f["rule_id"] == RULE]
    assert len(hits) == 6 and {f["status"] for f in hits} == {"existing"}

    assert main(["baseline", str(out / "report.json"), "--out", bl, "--migrate", bl]) == 0
    migrated = json.loads((bl_dir / "baseline.json").read_text())
    assert set(legacy) <= set(migrated["ids"]) and migrated["commit"] == "abc1234"
    assert main(argv) == 0, "still green after migrating"

    (code / "a.rb").write_text(_ruby(7))  # one more repeat: one new finding
    assert main(argv) == 1
    report = json.loads((out / "report.json").read_text())
    statuses = sorted(f["status"] for f in report["findings"] if f["rule_id"] == RULE)
    assert statuses == ["existing"] * 6 + ["new"]
