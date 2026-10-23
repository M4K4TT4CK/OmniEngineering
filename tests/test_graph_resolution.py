"""REQ-055: cross-file resolution that keeps a graph's code connected.

TypeScript ESM `.js` specifiers, Python src-layout and declared import roots, Go same-package and module-path
references, Java same-package types and annotations, external placeholders only for real dependencies, test ->
code `verifies` edges in all five starter languages, and docs `mentions` edges.

The resolvers and the docs pass are exercised on hand-built facts with the standard library only; the end-to-end
builds need the tree-sitter grammars and skip without them (CI's graph-extra job runs the python/typescript ones).
Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_graph as og  # noqa: E402


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def has_grammar(language: str) -> bool:
    try:
        if language in ("python", "javascript", "typescript"):
            og.load_language(language)
        else:
            og._generic_language(language)
        return True
    except og.GraphDependencyError:
        return False


def module(graph: og.Graph, relpath: str, language: str) -> og.GraphNode:
    return graph.add_node(og.GraphNode(relpath, "module", Path(relpath).stem, relpath, relpath, 1, 20, language))


def symbol(graph: og.Graph, owner: str, name: str, kind: str, language: str) -> og.GraphNode:
    relpath = owner.split("::")[0]
    node = graph.add_node(og.GraphNode(f"{owner}::{name}", kind, name, f"{owner}::{name}", relpath, 2, 5, language))
    graph.add_edge(owner, node.id, "defines", og.EXTRACTED, "syntax")
    return node


def edges(graph: og.Graph, edge_type: str) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in graph.edges if e.type == edge_type}


class TempRoot(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()


class TestTypeScriptSpecifiers(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = og._ImportResolver({
            "web/src/app.ts", "web/src/view.tsx", "web/src/worker.mts", "web/src/legacy.cts",
            "web/src/plain.js", "web/src/lib/index.ts", "web/tests/app.test.ts",
        })

    def resolve(self, raw: str, importer: str = "web/src/server.ts") -> str | None:
        return self.resolver.resolve(raw, importer, "typescript")

    def test_esm_js_specifiers_resolve_to_the_typescript_source(self) -> None:
        self.assertEqual(self.resolve("./app.js"), "web/src/app.ts")
        self.assertEqual(self.resolve("../src/app.js", "web/tests/app.test.ts"), "web/src/app.ts")
        self.assertEqual(self.resolve("./view.js"), "web/src/view.tsx")
        self.assertEqual(self.resolve("./view.jsx"), "web/src/view.tsx")
        self.assertEqual(self.resolve("./worker.mjs"), "web/src/worker.mts")
        self.assertEqual(self.resolve("./legacy.cjs"), "web/src/legacy.cts")

    def test_extensionless_index_and_real_js_files(self) -> None:
        self.assertEqual(self.resolve("./app"), "web/src/app.ts")
        self.assertEqual(self.resolve("./lib"), "web/src/lib/index.ts")
        self.assertEqual(self.resolve("./lib/index.js"), "web/src/lib/index.ts")
        self.assertEqual(self.resolve("./plain.js"), "web/src/plain.js")
        self.assertIsNone(self.resolve("./missing.js"))
        self.assertIsNone(self.resolve("express"))

    def test_typescript_module_extensions_are_discovered(self) -> None:
        self.assertIn(".mts", og.LANGUAGE_EXTENSIONS["typescript"])
        self.assertIn(".cts", og.LANGUAGE_EXTENSIONS["typescript"])


class TestPythonImportRoots(TempRoot):
    MODULES = {
        # Two independent src-layout projects that both call their package `service`.
        "templates/a/src/service/__init__.py", "templates/a/src/service/main.py", "templates/a/tests/test_a.py",
        "templates/b/src/service/__init__.py", "templates/b/src/service/main.py", "templates/b/tests/test_b.py",
        # A flat project whose package sits beside its tests.
        "flat/pkg/__init__.py", "flat/pkg/models.py", "flat/pkg/views.py", "flat/pkg/sub/__init__.py", "flat/tests/test_flat.py",
        # Declared roots: setuptools `where` and pytest `pythonpath` naming non-`src` directories.
        "declared/lib/engine/__init__.py", "declared/lib/engine/core.py", "declared/checks/test_core.py",
        "ini/app/helpers.py", "ini/tests/test_helpers.py",
        "top.py",
    }

    def setUp(self) -> None:
        super().setUp()
        write(self.root / "declared/pyproject.toml", '[tool.setuptools.packages.find]\nwhere = ["lib"]\n')
        write(self.root / "ini/pytest.ini", "[pytest]\npythonpath = app\n")
        self.resolver = og._ImportResolver(set(self.MODULES), self.root)

    def resolve(self, raw: str, importer: str) -> str | None:
        return self.resolver.resolve(raw, importer, "python")

    def test_src_layout_resolves_inside_each_project(self) -> None:
        self.assertEqual(self.resolve("service.main", "templates/a/tests/test_a.py"), "templates/a/src/service/main.py")
        self.assertEqual(self.resolve("service.main", "templates/b/tests/test_b.py"), "templates/b/src/service/main.py")
        self.assertEqual(self.resolve("service", "templates/b/tests/test_b.py"), "templates/b/src/service/__init__.py")

    def test_package_beside_the_tests_and_the_repository_root(self) -> None:
        self.assertEqual(self.resolve("pkg.models", "flat/tests/test_flat.py"), "flat/pkg/models.py")
        self.assertEqual(self.resolve("pkg.models", "flat/pkg/views.py"), "flat/pkg/models.py")  # sys.path is above the package
        self.assertEqual(self.resolve("top", "flat/pkg/views.py"), "top.py")
        self.assertIsNone(self.resolve("requests", "flat/pkg/views.py"))

    def test_declared_roots(self) -> None:
        self.assertEqual(self.resolve("engine.core", "declared/checks/test_core.py"), "declared/lib/engine/core.py")
        self.assertEqual(self.resolve("helpers", "ini/tests/test_helpers.py"), "ini/app/helpers.py")

    def test_relative_imports(self) -> None:
        self.assertEqual(self.resolve(".models", "flat/pkg/views.py"), "flat/pkg/models.py")
        self.assertEqual(self.resolve(".", "flat/pkg/views.py"), "flat/pkg/__init__.py")
        self.assertEqual(self.resolve("..models", "flat/pkg/sub/__init__.py"), "flat/pkg/models.py")
        self.assertEqual(og._python_submodule(".", "models"), ".models")
        self.assertEqual(og._python_submodule("pkg", "models"), "pkg.models")

    def test_config_parsing(self) -> None:
        pyproject = (
            '[tool.setuptools]\npackage-dir = {"" = "source"}\n'
            '[tool.pytest.ini_options]\npythonpath = ["src", "."]\n'
            '[tool.poetry]\npackages = [{ include = "pkg", from = "lib" }]\n'
            '[tool.hatch.build.targets.wheel]\npackages = ["code/mypkg"]\n'
        )
        self.assertEqual(set(og._python_config_roots(pyproject, "pyproject.toml")), {"source", "src", ".", "lib", "code"})
        self.assertEqual(og._python_config_roots("[tool:pytest]\npythonpath = src tests\n", "setup.cfg"), ["src", "tests"])
        self.assertEqual(og._python_config_roots('where = ["/etc", "../up"]\n', "pyproject.toml"), [])


class TestPythonAndJsResolution(unittest.TestCase):
    """_resolve_graph on hand-built facts: resolved imports and calls lose their external placeholder."""

    def test_python_test_links_to_its_src_layout_module_and_placeholders_stay_honest(self) -> None:
        graph = og.Graph()
        module(graph, "svc/src/service/__init__.py", "python")
        module(graph, "svc/src/service/main.py", "python")
        module(graph, "svc/tests/test_health.py", "python")
        symbol(graph, "svc/src/service/main.py", "health", "function", "python")
        test = symbol(graph, "svc/tests/test_health.py", "test_health", "function", "python")
        facts = og.FileFacts("svc/tests/test_health.py", "svc/tests/test_health.py", "python")
        for raw in ("service.main", "fastapi.testclient"):
            graph.ensure_external(raw)
            graph.add_edge(facts.module_id, f"external:{raw}", "imports", og.EXTRACTED, "syntax")
            facts.raw_module_imports.append(raw)
        facts.imported_names = {"health": ("service.main", "health"), "TestClient": ("fastapi.testclient", "TestClient")}
        for name, qualifier in (("health", None), ("TestClient", None), ("json", "response"), ("it", None)):
            graph.ensure_external(name)
            graph.add_edge(test.id, f"external:{name}", "calls", og.EXTRACTED, "syntax")
            facts.pending_calls.append((test.id, name, qualifier, None))
        pruner = og._ExternalPruner()
        og._resolve_graph(graph, [facts], None, pruner)
        pruner.apply(graph)

        self.assertIn((facts.module_id, "svc/src/service/main.py"), edges(graph, "imports"))
        self.assertIn((test.id, "svc/src/service/main.py::health"), edges(graph, "calls"))
        externals = {n.id for n in graph.nodes.values() if n.kind == "external"}
        # The unresolved library import and the call into it stay; the resolved import, the resolved call and the
        # bare unresolved calls (`it`, `response.json`) leave no placeholder.
        self.assertEqual(externals, {"external:fastapi.testclient", "external:TestClient"})
        self.assertEqual(graph.nodes[test.id].attrs["unresolved_calls"], ["it", "response.json"])
        self.assertEqual(len(graph._edge_keys), len(graph.edges))

    def test_typescript_server_and_test_reach_app_through_js_specifiers(self) -> None:
        graph = og.Graph()
        for rel in ("web/src/app.ts", "web/src/server.ts", "web/tests/health.test.ts"):
            module(graph, rel, "typescript")
        symbol(graph, "web/src/app.ts", "createApp", "function", "typescript")
        symbol(graph, "web/src/other.ts", "createApp", "function", "typescript")  # not unique: the import must decide
        all_facts = []
        for rel, raw in (("web/src/server.ts", "./app.js"), ("web/tests/health.test.ts", "../src/app.js")):
            facts = og.FileFacts(rel, rel, "typescript", imported_names={"createApp": (raw, "createApp")}, raw_module_imports=[raw])
            facts.pending_calls.append((rel, "createApp", None, None))
            all_facts.append(facts)
        og._resolve_graph(graph, all_facts)
        self.assertTrue({("web/src/server.ts", "web/src/app.ts"), ("web/tests/health.test.ts", "web/src/app.ts")} <= edges(graph, "imports"))
        self.assertIn(("web/tests/health.test.ts", "web/src/app.ts::createApp"), edges(graph, "calls"))


class TestGenericResolution(TempRoot):
    def test_go_same_package_beats_a_same_named_function_elsewhere(self) -> None:
        graph = og.Graph()
        for rel in ("svc/main.go", "svc/router.go", "svc/router_test.go", "other/router.go"):
            module(graph, rel, "go")
        symbol(graph, "svc/router.go", "newRouter", "function", "go")
        symbol(graph, "other/router.go", "newRouter", "function", "go")
        main = symbol(graph, "svc/main.go", "main", "function", "go")
        test = symbol(graph, "svc/router_test.go", "TestHealth", "function", "go")
        facts = [og.FileFacts("svc/main.go", "svc/main.go", "go"), og.FileFacts("svc/router_test.go", "svc/router_test.go", "go")]
        facts[0].pending_calls.append((main.id, "newRouter", None, None))
        facts[1].pending_calls.append((test.id, "newRouter", None, None))
        og._resolve_generic(graph, facts, self.root)
        calls = edges(graph, "calls")
        self.assertIn((main.id, "svc/router.go::newRouter"), calls)
        self.assertIn((test.id, "svc/router.go::newRouter"), calls)

    def test_go_module_path_imports_reach_the_local_package(self) -> None:
        write(self.root / "svc/go.mod", "module example.com/svc\n\ngo 1.22\n")
        graph = og.Graph()
        for rel in ("svc/main.go", "svc/internal/store/store.go", "svc/internal/store/cache.go", "svc/internal/store/store_test.go"):
            module(graph, rel, "go")
        symbol(graph, "svc/internal/store/store.go", "Open", "function", "go")
        symbol(graph, "svc/internal/store/store_test.go", "Open", "function", "go")  # a test helper: same dir, but test file
        main = symbol(graph, "svc/main.go", "main", "function", "go")
        raw = "example.com/svc/internal/store"
        facts = og.FileFacts("svc/main.go", "svc/main.go", "go", raw_module_imports=[raw, "net/http"],
                             imported_names={"store": (raw, None), "http": ("net/http", None)})
        for name in (raw, "net/http"):
            graph.ensure_external(name)
            graph.add_edge(facts.module_id, f"external:{name}", "imports", og.EXTRACTED, "syntax")
        facts.pending_calls.append((main.id, "Open", "store", None))
        pruner = og._ExternalPruner()
        og._resolve_generic(graph, [facts], self.root, pruner)
        pruner.apply(graph)
        imports = edges(graph, "imports")
        self.assertIn(("svc/main.go", "svc/internal/store/store.go"), imports)
        self.assertIn(("svc/main.go", "svc/internal/store/cache.go"), imports)
        self.assertNotIn(("svc/main.go", "svc/internal/store/store_test.go"), imports)
        self.assertNotIn(f"external:{raw}", graph.nodes)
        self.assertIn("external:net/http", graph.nodes)

    def test_go_call_into_a_package_function_with_two_candidates(self) -> None:
        write(self.root / "go.mod", "module example.com/app\n")
        graph = og.Graph()
        for rel in ("cmd/main.go", "store/store.go", "legacy/store.go"):
            module(graph, rel, "go")
        symbol(graph, "store/store.go", "Open", "function", "go")
        symbol(graph, "legacy/store.go", "Open", "function", "go")
        main = symbol(graph, "cmd/main.go", "main", "function", "go")
        facts = og.FileFacts("cmd/main.go", "cmd/main.go", "go", raw_module_imports=["example.com/app/store"],
                             imported_names={"store": ("example.com/app/store", None)})
        facts.pending_calls.append((main.id, "Open", "store", None))
        og._resolve_generic(graph, [facts], self.root)
        self.assertIn((main.id, "store/store.go::Open"), edges(graph, "calls"))

    def java_monorepo(self) -> tuple[og.Graph, og.FileFacts, list[og.FileFacts]]:
        graph = og.Graph()
        all_facts = []
        for project in ("one", "two"):
            for rel, cls in ((f"{project}/src/main/java/service/HealthController.java", "HealthController"),
                             (f"{project}/src/test/java/service/HealthControllerTest.java", "HealthControllerTest")):
                module(graph, rel, "java")
                symbol(graph, rel, cls, "class", "java")
                all_facts.append(og.FileFacts(rel, rel, "java", package="service"))
        return graph, all_facts[3], all_facts

    def test_java_same_package_reference_without_an_import(self) -> None:
        graph, test_facts, all_facts = self.java_monorepo()
        test_class = "two/src/test/java/service/HealthControllerTest.java::HealthControllerTest"
        test_facts.pending_type_refs.append((test_class, "HealthController"))  # @WebMvcTest(HealthController.class)
        og._resolve_generic(graph, all_facts, self.root)
        self.assertIn((test_class, "two/src/main/java/service/HealthController.java::HealthController"), edges(graph, "uses"))
        self.assertNotIn((test_class, "one/src/main/java/service/HealthController.java::HealthController"), edges(graph, "uses"))

    def test_java_fully_qualified_import_of_a_local_class(self) -> None:
        graph, test_facts, all_facts = self.java_monorepo()
        test_facts.package = "other"
        test_facts.raw_module_imports.append("service.HealthController")
        og._resolve_generic(graph, all_facts, self.root)
        self.assertIn((test_facts.module_id, "two/src/main/java/service/HealthController.java"), edges(graph, "imports"))

    def test_ruby_resolved_require_leaves_no_placeholder(self) -> None:
        graph = og.Graph()
        module(graph, "rb/lib/app.rb", "ruby")
        module(graph, "rb/spec/app_spec.rb", "ruby")
        facts = og.FileFacts("rb/spec/app_spec.rb", "rb/spec/app_spec.rb", "ruby", raw_module_imports=["../lib/app", "rack/test"])
        for raw in facts.raw_module_imports:
            graph.ensure_external(raw)
            graph.add_edge(facts.module_id, f"external:{raw}", "imports", og.EXTRACTED, "syntax")
        for name in ("get", "expect"):  # RSpec / Rack::Test DSL: bare, unresolved, not imported
            graph.ensure_external(name)
            graph.add_edge(facts.module_id, f"external:{name}", "calls", og.EXTRACTED, "syntax")
            facts.pending_calls.append((facts.module_id, name, None, None))
        pruner = og._ExternalPruner()
        og._resolve_generic(graph, [facts], self.root, pruner)
        pruner.apply(graph)
        self.assertIn(("rb/spec/app_spec.rb", "rb/lib/app.rb"), edges(graph, "imports"))
        self.assertEqual({n.id for n in graph.nodes.values() if n.kind == "external"}, {"external:rack/test"})


class TestVerifiesFromResolvedEdges(unittest.TestCase):
    def test_uses_and_imports_from_tests_become_verifies(self) -> None:
        graph = og.Graph()
        module(graph, "j/src/main/java/service/HealthController.java", "java")
        controller = symbol(graph, "j/src/main/java/service/HealthController.java", "HealthController", "class", "java")
        module(graph, "j/src/test/java/service/HealthControllerTest.java", "java")
        test_class = symbol(graph, "j/src/test/java/service/HealthControllerTest.java", "HealthControllerTest", "class", "java")
        module(graph, "p/src/service/main.py", "python")
        module(graph, "p/tests/test_health.py", "python")
        graph.add_edge(test_class.id, controller.id, "uses", og.INFERRED, "graph-traversal")
        graph.add_edge("p/tests/test_health.py", "p/src/service/main.py", "imports", og.INFERRED, "graph-traversal")
        test_ids = og.mark_test_nodes(graph)
        og.derive_verifies(graph, test_ids)
        verifies = edges(graph, "verifies")
        self.assertIn(("j/src/test/java/service/HealthControllerTest.java", controller.id), verifies)
        self.assertIn(("p/tests/test_health.py", "p/src/service/main.py"), verifies)


class TestDocMentions(TempRoot):
    def setUp(self) -> None:
        super().setUp()
        write(self.root / "src/app.py", "x = 1\n")
        write(self.root / "scripts/run.sh", "echo\n")
        write(self.root / "README.md", "Start in `src/app.py`; see [the guide](docs/guide.md#setup), `missing/gone.py` and https://example.com/a.py.\n")
        write(self.root / "docs/guide.md", "Run [it](../scripts/run.sh) and read `app.py`. Never `../../outside.txt`.\n")
        write(self.root / ".claude/skills/demo/SKILL.md", "Writes `docs/guide.md`.\n")
        write(self.root / ".ai/playbooks/x.md", "`src/app.py`\n")  # OmniEngineering's own files are tooling
        write(self.root / "node_modules/pkg/README.md", "`src/app.py`\n")
        write(self.root / "CHANGELOG.md", "# Changelog\n\n## 2026-10-22\n\n- touched `src/app.py`\n")
        self.graph = og.Graph()
        module(self.graph, "src/app.py", "python")

    def test_docs_mention_the_files_they_name(self) -> None:
        stats = og.add_governance_layer(self.graph, self.root)
        mentions = {(s, t) for s, t in edges(self.graph, "mentions") if not s.startswith("changelog:")}
        self.assertEqual(mentions, {
            ("file:README.md", "src/app.py"),
            ("file:README.md", "file:docs/guide.md"),
            ("file:docs/guide.md", "file:scripts/run.sh"),
            ("file:docs/guide.md", "src/app.py"),  # `app.py` is the unique suffix of a parsed module
            ("file:.claude/skills/demo/SKILL.md", "file:docs/guide.md"),
        })
        self.assertEqual(stats["doc_mentions"], 5)
        self.assertNotIn("file:missing/gone.py", self.graph.nodes)
        resolvers = {e.resolver for e in self.graph.edges if e.type == "mentions" and e.source == "file:README.md"}
        self.assertEqual(resolvers, {"docs"})

    def test_why_lists_docs_separately_from_the_changelog(self) -> None:
        og.add_governance_layer(self.graph, self.root)
        path = self.root / "graph.json"
        path.write_text(__import__("json").dumps(self.graph.to_json(str(self.root), ["python"], {})), encoding="utf-8")
        result = og.why(path, "src/app.py")
        self.assertIn("docs", result["sections"])
        self.assertIn("changelog", result["sections"])


ALL_STARTERS = all(has_grammar(lang) for lang in ("python", "typescript", "go", "java", "ruby"))


@unittest.skipUnless(ALL_STARTERS, "needs the python, typescript, go, java and ruby tree-sitter grammars")
class TestFiveStarterMonorepo(TempRoot):
    """The interview workspace's five starters, trimmed: every test file verifies its app's code."""

    def setUp(self) -> None:
        super().setUp()
        r = self.root / "templates"
        write(r / "python/pyproject.toml", '[tool.pytest.ini_options]\npythonpath = ["src"]\n')
        write(r / "python/src/service/__init__.py", "")
        write(r / "python/src/service/main.py", "from fastapi import FastAPI\n\napp = FastAPI()\n\n\ndef health():\n    return {}\n")
        write(r / "python/tests/test_health.py", "from fastapi.testclient import TestClient\n\nfrom service.main import app\n\n\ndef test_health():\n    TestClient(app).get('/health')\n")
        write(r / "typescript/src/app.ts", 'import express from "express";\n\nexport const createApp = () => {\n  const app = express();\n  return app;\n};\n')
        write(r / "typescript/src/server.ts", 'import { createApp } from "./app.js";\n\ncreateApp().listen(3000);\n')
        write(r / "typescript/tests/health.test.ts", 'import { it } from "vitest";\nimport { createApp } from "../src/app.js";\n\nit("ok", () => { createApp(); });\n')
        write(r / "go/go.mod", "module service\n\ngo 1.22\n")
        write(r / "go/main.go", "package main\n\nfunc main() {\n\t_ = newRouter()\n}\n")
        write(r / "go/router.go", 'package main\n\nimport "net/http"\n\nfunc newRouter() http.Handler {\n\treturn http.NewServeMux()\n}\n')
        write(r / "go/router_test.go", 'package main\n\nimport "testing"\n\nfunc TestHealth(t *testing.T) {\n\tnewRouter()\n}\n')
        write(r / "java/src/main/java/service/HealthController.java", "package service;\n\nclass HealthController {\n    String health() { return \"ok\"; }\n}\n")
        write(r / "java/src/test/java/service/HealthControllerTest.java", "package service;\n\nimport org.junit.jupiter.api.Test;\n\n@WebMvcTest(HealthController.class)\nclass HealthControllerTest {\n    @Test\n    void ok() {}\n}\n")
        write(r / "ruby/lib/app.rb", "require 'sinatra/base'\n\nclass App < Sinatra::Base\n  get '/health' do\n    'ok'\n  end\nend\n")
        write(r / "ruby/spec/health_spec.rb", "require_relative '../lib/app'\n\nRSpec.describe App do\n  it 'works' do\n    get '/health'\n    expect(1).to eq(1)\n  end\nend\n")
        self.graph, _ = og.build_graph(self.root, ["python", "typescript", "go", "java", "ruby"], layers={og.LAYER_CODE, og.LAYER_ASSURANCE})

    def test_every_starter_test_verifies_code(self) -> None:
        verifies = edges(self.graph, "verifies")
        by_language = {self.graph.nodes[s].language for s, _ in verifies}
        self.assertEqual(by_language, {"python", "typescript", "go", "java", "ruby"})
        self.assertIn(("templates/python/tests/test_health.py", "templates/python/src/service/main.py"), verifies)
        self.assertIn(("templates/typescript/tests/health.test.ts", "templates/typescript/src/app.ts::createApp"), verifies)
        self.assertIn(("templates/go/router_test.go", "templates/go/router.go::newRouter"), verifies)
        self.assertIn(("templates/java/src/test/java/service/HealthControllerTest.java",
                       "templates/java/src/main/java/service/HealthController.java::HealthController"), verifies)
        self.assertIn(("templates/ruby/spec/health_spec.rb", "templates/ruby/lib/app.rb"), verifies)

    def test_code_is_connected_and_placeholders_are_dependencies(self) -> None:
        imports, calls = edges(self.graph, "imports"), edges(self.graph, "calls")
        self.assertIn(("templates/typescript/src/server.ts", "templates/typescript/src/app.ts"), imports)
        self.assertIn(("templates/typescript/src/server.ts", "templates/typescript/src/app.ts::createApp"), calls)  # top-level call
        self.assertIn("templates/typescript/src/app.ts::createApp", self.graph.nodes)  # arrow-function const is a symbol
        self.assertIn(("templates/go/main.go::main", "templates/go/router.go::newRouter"), calls)
        externals = {n.name for n in self.graph.nodes.values() if n.kind == "external"}
        self.assertTrue({"fastapi", "express", "vitest", "net/http", "sinatra/base"} <= externals, externals)
        self.assertFalse({"get", "expect", "eq", "../lib/app", "./app.js", "service.main", "newRouter"} & externals, externals)


if __name__ == "__main__":
    unittest.main()
