"""The graph viewer template: what it must contain, and that the page built from it is well formed.

Behaviour that needs a browser (layout, pan and zoom, the chain highlight) is checked by hand in headless Chromium; these tests guard
the parts that can be checked without one.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_graph as og  # noqa: E402

TEMPLATE = (ROOT / ".ai" / "graph-viewer" / "viewer.html").read_text(encoding="utf-8")


def build_page(**kwargs) -> str:
    graph = og.Graph()
    for rel in ("src/a.py", "src/b.py"):
        graph.add_node(og.GraphNode(rel, "module", Path(rel).stem, rel, rel, 1, 1, "python"))
    graph.add_edge("src/a.py", "src/b.py", "imports", "EXTRACTED", "syntax")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "graph.json"
        path.write_text(json.dumps(graph.to_json(".", ["python"], {})), encoding="utf-8")
        result = og.build_view_html(path, **kwargs)
    assert result["ok"], result
    return result["html"]


class TestTemplate(unittest.TestCase):
    def test_2d_is_a_real_canvas_view_that_needs_no_webgl(self) -> None:
        self.assertIn('<canvas id="c2d"', TEMPLATE)
        self.assertIn('id="m2d"', TEMPLATE)
        self.assertIn('id="m3d"', TEMPLATE)
        # WebGL is only probed and created by the 3D adapter, never at load in 2D mode
        self.assertEqual(TEMPLATE.count("ForceGraph3D()(host)"), 1)
        self.assertIn("R3.destroy()", TEMPLATE)  # leaving 3D releases the GPU context

    def test_menus_are_collapsible_and_every_control_has_a_tooltip(self) -> None:
        self.assertGreaterEqual(len(re.findall(r'<section class="sec', TEMPLATE)), 7)
        self.assertGreaterEqual(len(re.findall(r"data-tip=", TEMPLATE)), 25)
        self.assertIn("initSections", TEMPLATE)

    def test_views_replace_layer_toggling_and_a_strip_names_the_current_view(self) -> None:
        for view in ("overview", "code", "governance", "history", "assurance", "workspace", "schema"):
            self.assertIn(f"id: '{view}'", TEMPLATE)
        self.assertIn('id="ctxname"', TEMPLATE)
        self.assertIn('id="chips"', TEMPLATE)

    def test_light_theme_is_soft_grey_not_white(self) -> None:
        light = re.search(r":root\[data-theme=light\]\{([^}]*)\}", TEMPLATE)
        self.assertIsNotNone(light)
        for token in re.findall(r"#[0-9a-fA-F]{6}", light.group(1)):
            self.assertNotIn(token.lower(), ("#ffffff", "#fff"), "the light theme must not use pure white")
        self.assertIn("setTheme", TEMPLATE)

    def test_chain_and_trace_are_present(self) -> None:
        for name in ("chainOf", "pathBetween", "runTrace", "clearTrace", "focusOn"):
            self.assertIn(f"function {name}", TEMPLATE)

    def test_lineage_walks_up_and_down_with_one_node(self) -> None:
        for name in ("chainOf", "lineageWalk", "renderLineage", "flowOf"):
            self.assertIn(f"function {name}", TEMPLATE)
        # the flow table must stay in step with the CLI's, or the viewer and `omni graph lineage` will disagree
        import omni_graph as og
        for edge_type, way in {**og.LINEAGE_FLOW, **og.LINEAGE_CODE_FLOW, **og.LINEAGE_SINGLE_HOP}.items():
            self.assertRegex(TEMPLATE, rf"\b{edge_type}: '{way}'", f"viewer flow for {edge_type} differs from omni_graph.py")

    def test_markup_has_no_literal_escapes(self) -> None:
        self.assertIsNone(re.search(r"\\u[0-9a-fA-F]{4}", TEMPLATE.split("<script", 1)[0]))


class TestGeneratedPage(unittest.TestCase):
    def test_mode_is_passed_to_the_page(self) -> None:
        self.assertIn('"mode":"2d"', build_page(mode="2d"))
        self.assertIn('"mode":"3d"', build_page(mode="3d"))
        self.assertIn('"mode":"auto"', build_page())
        self.assertIn('"mode":"auto"', build_page(mode="nonsense"))

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_the_viewer_script_parses(self) -> None:
        page = build_page()
        scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
        main = scripts[-1]
        self.assertIn("omniGraphViewer", main)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "viewer.js"
            path.write_text(main, encoding="utf-8")
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
