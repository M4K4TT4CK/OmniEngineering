"""The documentation a user reads agrees with the tree and the CLI.

Two cheap checks that catch the commonest drift: a relative link or
`#anchor` in README.md, the root guides or docs/ that no longer resolves,
and a CLI subcommand that docs/cli.md never mentions.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from arbiter.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted(
    [p for p in ROOT.glob("*.md") if p.name != "CHANGELOG.md"]
    + list((ROOT / "docs").glob("*.md"))
)

LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^(```|~~~)")


def _prose(text: str) -> str:
    """The text outside fenced code blocks and inline code spans."""
    out, fenced = [], False
    for line in text.splitlines():
        if FENCE.match(line.strip()):
            fenced = not fenced
            continue
        if not fenced:
            out.append(re.sub(r"`[^`]*`", "", line))
    return "\n".join(out)


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped,
    spaces to hyphens."""
    h = re.sub(r"`", "", heading.strip().lower())
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    out = set()
    fenced = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line.strip()):
            fenced = not fenced
            continue
        m = re.match(r"^#{1,6}\s+(.*)$", line)
        if fenced or not m:
            continue
        slug = _slug(m.group(1))
        n = seen.get(slug, 0)
        out.add(slug if n == 0 else f"{slug}-{n}")
        seen[slug] = n + 1
    return out


def _links():
    for doc in DOCS:
        for target in LINK.findall(_prose(doc.read_text(encoding="utf-8"))):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target):
                continue  # http:, https:, mailto: ...
            yield doc, target


@pytest.mark.parametrize("doc,target", list(_links()),
                         ids=lambda v: v.name if isinstance(v, Path) else v)
def test_every_relative_link_and_anchor_resolves(doc, target):
    path, _, anchor = target.partition("#")
    dest = (doc.parent / path).resolve() if path else doc
    assert dest.exists(), f"{doc.relative_to(ROOT)} links to missing {target}"
    if anchor and dest.suffix == ".md":
        assert anchor in _anchors(dest), (
            f"{doc.relative_to(ROOT)} links to #{anchor}, which {dest.name} has no heading for")


def _subcommands(parser: argparse.ArgumentParser, prefix: str = "") -> list[str]:
    out = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                out.append(f"{prefix}{name}")
                out.extend(_subcommands(sub, f"{prefix}{name} "))
    return out


def test_every_cli_subcommand_is_in_the_command_reference():
    text = (ROOT / "docs" / "cli.md").read_text(encoding="utf-8")
    missing = [c for c in _subcommands(build_parser()) if f"arbiter {c}" not in text]
    assert not missing, f"docs/cli.md never shows: {missing}"
