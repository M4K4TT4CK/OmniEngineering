"""Draft OmniEngineering failure-ledger entries from true-positive verdicts.

Arbiter's adjudication ledger (`.arbiter/knowledge.json`) and the OmniEngineering
failure ledger (`.ai/failures/failure-ledger.json`) both record what was wrong
and why, and until now in parallel: a reviewer marked a finding real, fixed it,
and then described the same defect a second time for the workspace. This draws
the first draft from the verdict. The entry is `open`, carries the finding's
title, location, severity and rule, and nothing it cannot know -- root cause,
fix and regression test are the person's to fill in with `omni failure update`.

Nothing here reads the OmniEngineering tooling; the file shape is the one its
schema (`.ai/schemas/failure-ledger.schema.json`) requires, written the way
`make_ai.write_json` writes it so a later CLI edit produces a clean diff.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Iterable

from .core import Finding

_SEVERITIES = {"critical", "high", "medium", "low"}
_FINDING_REF = re.compile(r"finding (f:[0-9a-f]+)")


def _empty_ledger() -> dict:
    return {"version": "1.0.0", "failure_id_prefix": "FAIL", "failures": []}


def _load(path: Path) -> dict:
    if not path.is_file():
        return _empty_ledger()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("failures"), list):
        raise ValueError(f"{path} is not a failure ledger: expected an object with a failures array")
    data.setdefault("version", "1.0.0")
    data.setdefault("failure_id_prefix", "FAIL")
    return data


def _next_id(ledger: dict) -> str:
    prefix = str(ledger.get("failure_id_prefix", "FAIL"))
    highest = 0
    for item in ledger["failures"]:
        m = re.fullmatch(rf"{re.escape(prefix)}-(\d+)", str(item.get("id", "")))
        if m:
            highest = max(highest, int(m.group(1)))
    return f"{prefix}-{highest + 1:03d}"


def _referenced(ledger: dict) -> set[str]:
    out: set[str] = set()
    for item in ledger["failures"]:
        for key in ("how_detected", "symptom"):
            out.update(_FINDING_REF.findall(str(item.get(key, ""))))
    return out


def _entry(fid: str, finding: Finding, today: str) -> dict:
    loc = finding.location
    where = loc.path + (f":{loc.start_line}" if loc.start_line else "")
    if loc.repo_id and loc.repo_id != "root":
        where = f"{loc.repo_id}:{where}"
    severity = finding.severity if finding.severity in _SEVERITIES else "low"
    return {
        "id": fid,
        "date": today,
        "title": finding.title,
        "status": "open",
        "severity": severity,
        "symptom": f"{finding.description.strip()} At {where}." if finding.description else f"At {where}.",
        "how_detected": f"arbiter {finding.rule_id} finding {finding.id}, adjudicated true positive",
        "root_cause": "",
        "requirement": "",
        "affected": [loc.path] if loc.path else [],
        "fix_summary": "",
        "fix_commits": [],
        "regression_tests": [],
        "no_test_reason": "",
        "prevention_rules": [],
        "prevention_notes": "",
        "recurrence_of": "",
    }


def _draft(ledger: dict, findings: Iterable[Finding], wanted: set[str], today: str) -> list[dict]:
    """Add one open entry per wanted finding not already referenced, to the
    loaded copy of the ledger only. Returns the entries added, numbered after
    whatever the copy already held."""
    seen = _referenced(ledger)
    entries: list[dict] = []
    for f in findings:
        if f.id not in wanted or f.id in seen:
            continue
        entry = _entry(_next_id(ledger), f, today)
        ledger["failures"].append(entry)
        seen.add(f.id)
        entries.append(entry)
    return entries


def preview_entries(
    findings: Iterable[Finding],
    true_positive_ids: Iterable[str],
    ledger_path: Path | None = None,
    today: str | None = None,
) -> list[dict]:
    """The entries `draft_entries` would add, computed without writing anything.

    This is what an assistant's review draft shows a person before any verdict
    exists (ARB-050). Without a `ledger_path` the numbering starts from an
    empty ledger, so the ids are placeholders: the real ones are assigned when
    a person applies the marks with `--ledger`, against the real file."""
    wanted = set(true_positive_ids)
    if not wanted:
        return []
    ledger = _load(ledger_path) if ledger_path else _empty_ledger()
    return _draft(ledger, findings, wanted, today or date.today().isoformat())


def draft_entries(
    findings: Iterable[Finding],
    true_positive_ids: Iterable[str],
    ledger_path: Path,
    today: str | None = None,
) -> list[str]:
    """Append one open entry per true-positive finding not already in the
    ledger. Returns the ids written. Findings are matched by their report id,
    recorded in `how_detected`, so applying the same marks twice adds nothing."""
    wanted = set(true_positive_ids)
    if not wanted:
        return []
    ledger = _load(ledger_path)
    entries = _draft(ledger, findings, wanted, today or date.today().isoformat())
    if entries:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return [e["id"] for e in entries]
