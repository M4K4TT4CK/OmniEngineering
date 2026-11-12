"""Tests for the component map (omni_components.py) and its graph layer.

Stdlib only (unittest), no tree-sitter: the project graph is hand-built. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_components as oc  # noqa: E402
import omni_graph as og  # noqa: E402

SHARED_SCORE = "export function score(steps: number[], checked: boolean[]): number {\n" + "\n".join(
    f"  const v{i} = steps[{i}] * (checked[{i}] ? 1 : 0) + {i};" for i in range(30)) + "\n  return v0 + v1;\n}\n"
API_WEB = "export async function getTasks(base: string) {\n" + "\n".join(
    f"  const r{i} = await fetch(base + '/tasks/{i}', {{ headers: {{ 'X-Step': '{i}' }} }});" for i in range(25)) + "\n  return r0;\n}\n"


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def node(nid: str, kind: str = "module", language: str = "typescript", lines: int = 40, **extra) -> dict:
    out = {"id": nid, "kind": kind, "name": Path(nid.split("::")[-1]).stem, "qualified_name": nid,
           "file": nid.split("::")[0], "start_line": 1, "end_line": lines, "language": language, "summary": ""}
    out.update(extra)
    return out


def edge(s: str, t: str, etype: str = "imports", prov: str = "INFERRED") -> dict:
    return {"source": s, "target": t, "type": etype, "provenance": prov, "resolver": "syntax", "detail": ""}


def build_project(root: Path) -> dict:
    """web and mobile apps, a shared/ library copied into both, a backend, tests, history and governance."""
    files = {
        "shared/score.ts": SHARED_SCORE,
        "web/src/shared/score.ts": "// GENERATED from shared/score.ts by scripts/sync.mjs. Do not edit.\n" + SHARED_SCORE,
        "mobile/src/shared/score.ts": "// GENERATED from shared/score.ts by scripts/sync.mjs. Do not edit.\n" + SHARED_SCORE,
        "web/src/pages/EvalPage.tsx": "import { score } from '../shared/score';\nimport { getTasks } from '../services/api';\n",
        "web/src/pages/HomePage.tsx": "import { getTasks } from '../services/api';\nimport { Button } from '../components/Button';\n",
        "web/src/components/Button.tsx": "export function Button() { return null; }\n",
        "web/src/services/api.ts": API_WEB,
        "web/src/services/util.ts": "import { getTasks } from './api';\nexport const x = 1;\n",
        "web/src/test/EvalPage.test.tsx": "import { EvalPage } from '../pages/EvalPage';\n",
        "mobile/src/screens/EvalScreen.tsx": "import { score } from '../shared/score';\nimport { getTasks } from '../services/api';\n",
        "mobile/src/services/api.ts": API_WEB.replace("X-Step", "X-Mobile"),
        "mobile/src/legacy/old.ts": "export function unused() { return 1; }\n",
        "mobile/android/app/.cxx/Debug/x/foo.cpp": "int main() { return 0; }\n",
        "web/src/a/a.ts": "import { b } from '../b/b';\n",
        "web/src/b/b.ts": "import { a } from '../a/a';\n",
    }
    for rel, text in files.items():
        write(root, rel, text)
    nodes = [node(rel, language="cpp" if rel.endswith(".cpp") else "typescript", lines=text.count("\n") + 1) for rel, text in files.items()]
    nodes[[n["id"] for n in nodes].index("web/src/test/EvalPage.test.tsx")].update({"layer": "assurance", "attrs": {"test": True}})
    nodes += [
        node("shared/score.ts::score", "function"), node("web/src/services/api.ts::getTasks", "function"),
        node("mobile/src/services/api.ts::getTasks", "function"), node("web/src/components/Button.tsx::Button", "function"),
        node("mobile/src/legacy/old.ts::unused", "function"),
        {"id": "commit:abc", "kind": "commit", "name": "abc", "qualified_name": "abc", "file": None, "start_line": None,
         "end_line": None, "language": None, "summary": "", "layer": "history", "attrs": {"date": "2026-09-01"}},
        {"id": "req:REQ-001", "kind": "requirement", "name": "REQ-001", "qualified_name": "REQ-001", "file": None, "start_line": None,
         "end_line": None, "language": None, "summary": "", "layer": "governance"},
        {"id": "failure:FAIL-001", "kind": "failure", "name": "FAIL-001", "qualified_name": "FAIL-001", "file": None, "start_line": None,
         "end_line": None, "language": None, "summary": "", "layer": "assurance"},
        {"id": "external:react", "kind": "external", "name": "react", "qualified_name": "react", "file": None, "start_line": None,
         "end_line": None, "language": None, "summary": ""},
    ]
    edges = [
        edge("web/src/pages/EvalPage.tsx", "web/src/shared/score.ts"),
        edge("web/src/pages/EvalPage.tsx", "external:../services/api", prov="EXTRACTED"),  # left unresolved by the graph
        edge("web/src/pages/HomePage.tsx", "web/src/services/api.ts"),
        edge("web/src/pages/HomePage.tsx", "web/src/components/Button.tsx"),
        edge("web/src/pages/HomePage.tsx", "external:react", prov="EXTRACTED"),
        edge("web/src/services/util.ts", "web/src/services/api.ts"),
        edge("mobile/src/screens/EvalScreen.tsx", "mobile/src/shared/score.ts"),
        edge("mobile/src/screens/EvalScreen.tsx", "mobile/src/services/api.ts"),
        # a generated copy's own links are not its source's dependencies
        edge("web/src/shared/score.ts", "web/src/components/Button.tsx", "calls"),
        # a name-only call resolution across applications must not count as a dependency
        edge("mobile/src/screens/EvalScreen.tsx", "web/src/components/Button.tsx::Button", "calls"),
        edge("web/src/test/EvalPage.test.tsx", "web/src/pages/EvalPage.tsx", "verifies"),
        edge("web/src/a/a.ts", "web/src/b/b.ts"), edge("web/src/b/b.ts", "web/src/a/a.ts"),
        edge("shared/score.ts", "shared/score.ts::score", "defines", "EXTRACTED"),
        edge("web/src/services/api.ts", "web/src/services/api.ts::getTasks", "defines", "EXTRACTED"),
        edge("mobile/src/services/api.ts", "mobile/src/services/api.ts::getTasks", "defines", "EXTRACTED"),
        edge("web/src/components/Button.tsx", "web/src/components/Button.tsx::Button", "defines", "EXTRACTED"),
        edge("mobile/src/legacy/old.ts", "mobile/src/legacy/old.ts::unused", "defines", "EXTRACTED"),
        edge("commit:abc", "web/src/services/api.ts", "modifies"),
        edge("req:REQ-001", "web/src/services/api.ts", "touches"),
        edge("failure:FAIL-001", "web/src/services/api.ts", "affects"),
    ]
    return {"generated_at": "2026-09-02T00:00:00+00:00", "nodes": nodes, "edges": edges}


class ComponentMapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.graph = build_project(cls.root)
        cls.cmap = oc.analyze(cls.graph, cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_groups_applications_and_components(self) -> None:
        self.assertEqual(set(self.cmap["applications"]), {"shared", "web", "mobile"})
        comps = self.cmap["components"]
        self.assertIn("web/src/pages", comps)
        self.assertIn("web/src/services", comps)
        self.assertEqual(comps["web/src/pages"]["role"], "ui-screens")
        self.assertEqual(comps["web/src/services"]["role"], "services")
        self.assertEqual(self.cmap["modules"]["web/src/services/api.ts"]["component"], "web/src/services")

    def test_excludes_tests_build_output_and_generated_copies(self) -> None:
        mods = self.cmap["modules"]
        self.assertNotIn("web/src/test/EvalPage.test.tsx", mods)
        self.assertFalse(any(".cxx" in m for m in mods))
        self.assertNotIn("web/src/shared/score.ts", mods)
        self.assertEqual(self.cmap["generated_copies"]["web/src/shared/score.ts"], "shared/score.ts")
        self.assertEqual(sorted(mods["shared/score.ts"]["copies"]), ["mobile/src/shared/score.ts", "web/src/shared/score.ts"])

    def test_generated_copy_consumers_make_the_source_cross_app(self) -> None:
        score = self.cmap["modules"]["shared/score.ts"]
        self.assertEqual(score["reuse"]["category"], "cross-app")
        self.assertEqual(sorted(score["reuse"]["consumer_apps"]), ["mobile", "web"])
        self.assertEqual(score["depends_on"], [])
        self.assertEqual(self.cmap["components"]["shared"]["reuse"]["category"], "cross-app")

    def test_resolves_imports_the_graph_left_external(self) -> None:
        api = self.cmap["modules"]["web/src/services/api.ts"]
        self.assertIn("web/src/pages/EvalPage.tsx", api["used_by"])
        # three consumers, but util.ts is in its own component, so only one other component uses it
        self.assertEqual(api["reuse"]["consumers"], 3)
        self.assertEqual(api["reuse"]["consumer_components"], ["web/src/pages"])
        self.assertEqual(api["reuse"]["category"], "reused")

    def test_cross_app_links_need_a_real_import(self) -> None:
        button = self.cmap["modules"]["web/src/components/Button.tsx"]
        self.assertEqual(button["used_by"], ["web/src/pages/HomePage.tsx"])
        self.assertEqual(button["reuse"]["consumer_apps"], [])

    def test_reuse_categories(self) -> None:
        mods = self.cmap["modules"]
        self.assertEqual(mods["web/src/pages/HomePage.tsx"]["reuse"]["category"], "entry-point")
        self.assertEqual(mods["web/src/components/Button.tsx"]["reuse"]["category"], "single-use")
        self.assertEqual(mods["mobile/src/legacy/old.ts"]["reuse"]["category"], "unreferenced")
        self.assertEqual(self.cmap["components"]["web/src/pages"]["reuse"]["category"], "top-level")

    def test_dependencies_downtrace_and_impact(self) -> None:
        pages = self.cmap["components"]["web/src/pages"]
        self.assertIn("web/src/services", pages["depends_on"])
        self.assertIn("web/src/components", pages["downtrace"])
        services = self.cmap["components"]["web/src/services"]
        self.assertIn("web/src/pages", services["impact"])
        self.assertEqual(services["requirements"], ["REQ-001"])
        self.assertEqual(services["failures"], ["FAIL-001"])
        self.assertEqual(services["external_packages"], {})
        self.assertEqual(self.cmap["components"]["web/src/pages"]["external_packages"], {"react": 1})
        self.assertEqual([s[1] for s in oc.trace(self.cmap, "web/src/services", up=True)], ["web/src/pages"])

    def test_cycles(self) -> None:
        self.assertIn(["web/src/a", "web/src/b"], self.cmap["cycles"])
        self.assertTrue(self.cmap["components"]["web/src/a"]["in_cycle"])

    def test_maintainability_is_explained(self) -> None:
        api = self.cmap["modules"]["web/src/services/api.ts"]["maintainability"]
        self.assertIn("no test exercises it", api["reasons"])
        self.assertIn("1 recorded failure(s)", api["reasons"])
        page = self.cmap["modules"]["web/src/pages/EvalPage.tsx"]["maintainability"]
        self.assertNotIn("no test exercises it", page["reasons"])
        self.assertIn(page["grade"], "AB")

    def test_duplicate_across_apps(self) -> None:
        dupes = [d for d in self.cmap["duplicates"] if d["name"] in ("api.ts", "getTasks")]
        self.assertEqual(len(dupes), 1)
        self.assertTrue(dupes[0]["cross_app"])
        self.assertEqual(dupes[0]["modules"], ["mobile/src/services/api.ts", "web/src/services/api.ts"])
        self.assertIn(dupes[0]["confidence"], ("high", "medium"))
        # generated copies of shared/score.ts are not duplicates of it
        self.assertFalse(any("score" in d["name"] for d in self.cmap["duplicates"]))

    def test_annotations_are_merged(self) -> None:
        ann = {"components": {"web/src/services": {"owner": "web team", "notes": ["talks to the API"]}, "ghost": {"owner": "x"}}, "modules": {}}
        cmap = oc.analyze(self.graph, self.root, annotations=ann)
        self.assertEqual(cmap["components"]["web/src/services"]["annotation"]["owner"], "web team")
        self.assertTrue(any("ghost" in p for p in cmap["problems"]))

    def test_reuse_search_and_find(self) -> None:
        hits = oc.search_reuse(self.cmap, ["score"])
        self.assertEqual(hits[0]["id"], "shared/score.ts")
        self.assertEqual(oc.find(self.cmap, "web/src/services"), ("component", "web/src/services"))
        self.assertEqual(oc.find(self.cmap, "HomePage"), ("module", "web/src/pages/HomePage.tsx"))

    def test_markdown_has_every_section(self) -> None:
        md = oc.render_markdown(self.cmap, "CUI")
        for heading in ("# Component Map", "**Classification:** CUI", "## 1. Applications", "## 2. Reuse catalog",
                        "## 3. Duplication suspects", "## 4. Maintainability watchlist", "## 5. Dependency map", "## 6. Components", "```mermaid"):
            self.assertIn(heading, md)
        self.assertIn("`shared/score.ts`", md)

    def test_explicit_components_and_config(self) -> None:
        cfg = oc.load_config(self.root)
        cfg["components"] = [{"id": "api-layer", "name": "API layer", "paths": ["web/src/services/*"], "role": "services"}]
        cmap = oc.analyze(self.graph, self.root, config=cfg)
        self.assertEqual(cmap["modules"]["web/src/services/api.ts"]["component"], "api-layer")
        self.assertEqual(cmap["components"]["api-layer"]["name"], "API layer")


class TextLinkTest(unittest.TestCase):
    def test_reexports_and_same_package_references_count_as_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {
                "web/src/ui/index.ts": "export { Widget } from './Widget';\n",
                "web/src/ui/Widget.tsx": "export function Widget() { return null; }\n",
                "web/src/pages/Home.tsx": "import { Widget } from '../ui';\n",
                "api/src/main/java/com/acme/svc/Entries.java": "package com.acme.svc;\nfinal class Entries { static final int N = 1; }\n",
                "api/src/main/java/com/acme/svc/NewsService.java": "package com.acme.svc;\nclass NewsService { int n() { return Entries.N; } }\n",
                "api/src/main/java/com/acme/web/Ctl.java": "package com.acme.web;\nclass Ctl {}\n",
                "api/src/main/resources/db/migration/V1__init.sql": "create table t (id int);\n",
            }
            for rel, text in files.items():
                write(root, rel, text)
            lang = {".ts": "typescript", ".tsx": "typescript", ".java": "java", ".sql": "sql"}
            graph = {"nodes": [node(rel, language=lang[Path(rel).suffix]) for rel in files],
                     "edges": [edge("web/src/pages/Home.tsx", "web/src/ui/index.ts")]}
            cmap = oc.analyze(graph, root)
        mods = cmap["modules"]
        self.assertIn("web/src/ui/index.ts", mods["web/src/ui/Widget.tsx"]["used_by"])
        self.assertIn("api/src/main/java/com/acme/svc/NewsService.java", mods["api/src/main/java/com/acme/svc/Entries.java"]["used_by"])
        self.assertEqual(mods["api/src/main/resources/db/migration/V1__init.sql"]["reuse"]["category"], "framework-wired")


class ComponentLayerTest(unittest.TestCase):
    def test_layer_nodes_and_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmap = oc.analyze(build_project(root), root)
        nodes, edges, module_attrs = oc.layer_nodes_and_edges(cmap)
        kinds = {n["kind"] for n in nodes}
        self.assertEqual(kinds, {"application", "component"})
        types = {e["type"] for e in edges}
        self.assertEqual(types, {"groups", "composes", "depends_on"})
        self.assertEqual(module_attrs["shared/score.ts"]["reuse_category"], "cross-app")
        self.assertEqual(module_attrs["web/src/shared/score.ts"]["generated_copy_of"], "shared/score.ts")
        self.assertEqual(og.EDGE_LAYER["depends_on"], og.LAYER_COMPONENTS)
        self.assertIn(og.LAYER_COMPONENTS, og.LAYERS)


class ComponentCliTest(unittest.TestCase):
    def test_build_show_trace_reuse_dupes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph = build_project(root)
            (root / ".ai").mkdir()
            (root / ".ai" / "project-graph.json").write_text(json.dumps(graph), encoding="utf-8")
            omni = [sys.executable, str(ROOT / "omni")]

            # a Windows console's cp1252 cannot encode every character in the map; output must still work there
            env = dict(os.environ, PYTHONIOENCODING="cp1252")

            def run(*args: str) -> subprocess.CompletedProcess:
                return subprocess.run(omni + list(args), cwd=root, capture_output=True, text=True, env=env, encoding="cp1252", errors="replace")

            built = run("components", "build")
            self.assertEqual(built.returncode, 0, built.stderr)
            self.assertTrue((root / ".ai" / "component-map.md").is_file())
            self.assertIn("components", built.stdout)
            shown = run("components", "show", "web/src/services")
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertIn("Change impact", shown.stdout)
            module = run("components", "show", "web/src/services/api.ts", "--json")
            self.assertEqual(json.loads(module.stdout)["component"], "web/src/services")
            traced = run("components", "trace", "web/src/pages")
            self.assertIn("web/src/services", traced.stdout)
            reuse = run("components", "reuse", "score")
            self.assertIn("shared/score.ts", reuse.stdout)
            dupes = run("components", "dupes", "--cross-app", "--json")
            self.assertEqual(len(json.loads(dupes.stdout)), 1)
            missing = run("components", "show", "nothing-like-this")
            self.assertEqual(missing.returncode, 1)


if __name__ == "__main__":
    unittest.main()
