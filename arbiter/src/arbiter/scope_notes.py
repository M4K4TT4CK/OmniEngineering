"""Scope notes: read a scanned repo's own docs so a finding can carry a
pointer to a control the repo's authors say already covers it.

Not suppression. Arbiter's whole design (controls.py) refuses to let a claim
outrun what actually ran -- a sentence in a README is not a check, and a
compromised or stale doc is exactly the thing that would lie about coverage.
So a matching note never changes severity, status, or suppression. It only
attaches an annotation, explicitly marked unverified, so whoever reviews the
finding sees the doc's claim and still has to judge it themselves.

## The convention

A heading matching SCOPE_HEADING (case-insensitive: "out of scope", "known
limitations", "external controls", "handled elsewhere", "compensating
controls") opens a scope-note section. Bullets in that section are read
literally; a backtick-quoted path inside a bullet is the one thing treated as
machine-checkable. If a finding's location falls under that path, the
bullet's own text is attached to it.

Free-text bullets with no backtick path match nothing. Guessing at a fuzzy
match between prose and a finding would be a way for this module to be
quietly wrong about which findings a note covers, which is the failure mode
it exists to avoid.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .core import Finding
from .inventory import Inventory
from .probes import _line_of, _read

SCOPE_HEADING = re.compile(
    r"(?im)^#{1,6}[ \t]*(out of scope|known limitations?|external controls?|"
    r"handled elsewhere|compensating controls?)\b"
)
_ANY_HEADING = re.compile(r"(?m)^#{1,6}[ \t]")
_BULLET = re.compile(r"(?m)^[ \t]*[-*][ \t]+(.*)$")
_BACKTICK_PATH = re.compile(r"`([^`\s]+/[^`\s]*|[^`\s]+/)`")


@dataclass(frozen=True)
class ScopeNote:
    path_prefix: str
    text: str
    doc_path: str
    line: int


def _normalize(path: str) -> str:
    return path.strip().strip("/")


def extract_scope_notes(inv: Inventory, repo_id: str) -> list[ScopeNote]:
    """Pull scope notes out of every markdown file in one repo."""
    notes: list[ScopeNote] = []
    for f in inv.with_ext(".md", ".markdown", repo_id=repo_id):
        text = _read(f)
        if not text:
            continue
        for heading in SCOPE_HEADING.finditer(text):
            nxt = _ANY_HEADING.search(text, heading.end())
            section = text[heading.end():nxt.start() if nxt else len(text)]
            for bullet in _BULLET.finditer(section):
                body = bullet.group(1).strip()
                for raw in _BACKTICK_PATH.findall(body):
                    prefix = _normalize(raw)
                    if not prefix:
                        continue
                    notes.append(ScopeNote(
                        path_prefix=prefix,
                        text=body,
                        doc_path=f.path,
                        line=_line_of(text, heading.end() + bullet.start(1)),
                    ))
    return notes


def _under(finding_path: str, prefix: str) -> bool:
    fp = _normalize(finding_path)
    return fp == prefix or fp.startswith(prefix + "/")


def apply_scope_notes(findings: list[Finding], inv: Inventory) -> int:
    """Annotate findings whose location a repo's own docs claim is covered
    elsewhere. Returns the count annotated. Never mutates severity, status,
    or suppression -- see module docstring for why.
    """
    notes_by_repo: dict[str, list[ScopeNote]] = {}
    annotated = 0
    for f in findings:
        if f.suppressed or not f.location.path or f.scope_note:
            continue
        notes = notes_by_repo.setdefault(f.repo_id, extract_scope_notes(inv, f.repo_id))
        for note in notes:
            if _under(f.location.path, note.path_prefix):
                f.scope_note = (
                    f"Possibly mitigated elsewhere -- see {note.doc_path}:{note.line} "
                    f'("{note.text}"), unverified.'
                )
                annotated += 1
                break
    return annotated
