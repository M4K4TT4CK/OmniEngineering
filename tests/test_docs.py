"""REQ-057: the documentation names what the CLI and code ship, and its internal links resolve.

These checks are cheap on purpose: they catch a new subcommand, MCP tool or graph-config key that nobody
documented, and a link or anchor that a rename broke. They do not judge prose. Stdlib only.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_ai as ma  # noqa: E402

README = (ROOT / "README.md").read_text(encoding="utf-8")


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:  # noqa: SLF001 -- argparse exposes no public accessor
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return dict(action.choices)
    return {}


def _slug(heading: str) -> str:
    """GitHub's anchor for a Markdown heading: lower-case, punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def _strip_code(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


class TestReadmeCoversTheCli(unittest.TestCase):
    def test_every_subcommand_is_in_the_readme(self) -> None:
        missing = []
        for name, sub in _subparsers(ma.build_parser()).items():
            if f"omni {name}" not in README:
                missing.append(name)
            for leaf in _subparsers(sub):
                # the command reference lists leaves as `omni graph build / show / ...`
                if not re.search(rf"`omni {name} [^`]*\b{re.escape(leaf)}\b", README):
                    missing.append(f"{name} {leaf}")
        self.assertEqual(missing, [], "document these in README.md (the Command Reference table)")

    def test_every_mcp_tool_is_in_the_readme(self) -> None:
        import omni_mcp

        missing = [tool.name for tool in omni_mcp.TOOLS if f"`{tool.name}`" not in README]
        self.assertEqual(missing, [], "list these in the README's MCP server section")

    def test_every_graph_config_key_is_in_the_readme(self) -> None:
        import omni_graph

        missing = [key for key in omni_graph._DEFAULT_GRAPH_CONFIG if f"`{key}`" not in README]  # noqa: SLF001
        self.assertEqual(missing, [], "add these to the .ai/graph-config.json table in README.md")


class TestInternalLinksResolve(unittest.TestCase):
    def test_relative_links_and_anchors(self) -> None:
        listed = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True)
        if listed.returncode != 0:
            self.skipTest("not a git checkout")
        # arbiter/ is a vendored subtree with its own docs and checks
        files = [name for name in listed.stdout.split() if not name.startswith("arbiter/")]
        anchors: dict[Path, set[str]] = {}
        broken = []
        for name in files:
            path = ROOT / name
            if not path.is_file():
                continue
            for target in re.findall(r"\]\(([^)\s]+)\)", _strip_code(path.read_text(encoding="utf-8"))):
                if re.match(r"[a-z]+:", target):
                    continue  # http:, https:, mailto:
                file_part, _, anchor = target.partition("#")
                resolved = (path.parent / file_part).resolve() if file_part else path
                if not resolved.exists():
                    broken.append(f"{name}: {target} (no such file)")
                    continue
                if anchor and resolved.suffix == ".md":
                    if resolved not in anchors:
                        text = _strip_code(resolved.read_text(encoding="utf-8"))
                        anchors[resolved] = {_slug(h) for h in re.findall(r"^#{1,6}\s+(.*)$", text, re.M)}
                    if anchor not in anchors[resolved]:
                        broken.append(f"{name}: {target} (no such heading)")
        self.assertEqual(broken, [])


if __name__ == "__main__":
    unittest.main()
