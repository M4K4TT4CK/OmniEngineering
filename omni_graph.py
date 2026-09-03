"""Deterministic, local source-code graph built from tree-sitter ASTs.

This is not a vector index: there are no embeddings and nothing is stored as
a similarity score. Every node is a code entity read directly out of a
syntax tree (a module, class, function, or method) and every edge is tagged
with where it came from:

- EXTRACTED: the fact is explicit in the source text at a single site (an
  import statement names this module; a call site names this function; a
  class statement names this base class). No resolution happened.
- INFERRED: the fact was resolved by traversing the graph itself (following
  imports and scopes across files to find the actual definition a call site
  refers to) or, if a semantic API is configured, by that API. INFERRED
  edges are only ever added on top of an EXTRACTED edge that justifies them
  -- an unresolved reference stays EXTRACTED-only rather than being guessed.

`omni graph build` is the only entry point that needs tree-sitter installed
(the `[graph]` extra). `omni graph trace` / `omni graph show` / `omni graph
render` only read the JSON this module writes, so they work with just the
standard library -- including the SVG renderer's force-directed layout,
which is a small pure-Python spring embedder rather than a numpy/networkx
dependency.
"""

from __future__ import annotations

import fnmatch
import json
import math
import os
import random
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GRAPH_VERSION = "1.0.0"
GRAPH_DEFAULT_OUTPUT = ".ai/project-graph.json"

EXTRACTED = "EXTRACTED"
INFERRED = "INFERRED"

LANGUAGE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "python": (".py",),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "typescript": (".ts", ".tsx"),
}

SEMANTIC_API_URL_ENV = "OMNI_GRAPH_SEMANTIC_API_URL"
SEMANTIC_API_KEY_ENV = "OMNI_GRAPH_SEMANTIC_API_KEY"
SEMANTIC_API_MODEL_ENV = "OMNI_GRAPH_SEMANTIC_MODEL"
SEMANTIC_MAX_NODES_DEFAULT = 40

PROVENANCE_LEGEND = {
    "EXTRACTED": (
        "Read directly from the source AST at a single site -- an explicit "
        "name, import, base class, or call. No resolution happened."
    ),
    "INFERRED": (
        "Resolved by traversing the graph (cross-file/scope name resolution) "
        "or by the configured semantic API pass. Always layered on top of an "
        "EXTRACTED fact that justifies it; never a standalone guess."
    ),
}


class GraphDependencyError(RuntimeError):
    """tree-sitter or a language grammar package is not installed."""


# --------------------------------------------------------------------------
# Graph data model
# --------------------------------------------------------------------------


@dataclass
class GraphNode:
    id: str
    kind: str  # module | class | function | method | external
    name: str
    qualified_name: str
    file: str | None
    start_line: int | None
    end_line: int | None
    language: str | None
    summary: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "language": self.language,
            "summary": self.summary,
        }


@dataclass
class GraphEdge:
    source: str
    target: str
    type: str  # imports | defines | inherits | calls | related_to
    provenance: str  # EXTRACTED | INFERRED
    resolver: str  # syntax | graph-traversal | semantic-api
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.type,
            "provenance": self.provenance,
            "resolver": self.resolver,
            "detail": self.detail,
        }


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self._edge_keys: set[tuple[str, str, str, str]] = set()

    def add_node(self, node: GraphNode) -> GraphNode:
        return self.nodes.setdefault(node.id, node)

    def add_edge(
        self,
        source: str,
        target: str,
        edge_type: str,
        provenance: str,
        resolver: str,
        detail: str = "",
    ) -> None:
        key = (source, target, edge_type, provenance)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self.edges.append(GraphEdge(source, target, edge_type, provenance, resolver, detail))

    def ensure_external(self, name: str) -> GraphNode:
        return self.add_node(GraphNode(f"external:{name}", "external", name, name, None, None, None, None))

    def to_json(self, root: str, languages: list[str], semantic_pass: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": GRAPH_VERSION,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "root": root,
            "languages": languages,
            "semantic_pass": semantic_pass,
            "provenance_legend": PROVENANCE_LEGEND,
            "nodes": [node.to_json() for node in self.nodes.values()],
            "edges": [edge.to_json() for edge in self.edges],
        }


@dataclass
class FileFacts:
    module_id: str
    module_relpath: str
    language: str
    imported_names: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    raw_module_imports: list[str] = field(default_factory=list)
    pending_calls: list[tuple[str, str, str | None, str | None]] = field(default_factory=list)
    pending_inherits: list[tuple[str, str, str | None]] = field(default_factory=list)


# --------------------------------------------------------------------------
# Ignore-pattern filtering
#
# Deliberately duplicated (not imported) from make_ai.py's map filtering:
# make_ai.py imports this module, so importing make_ai back here would be
# circular. The filter is ~20 lines of stdlib fnmatch and cheap to keep in
# sync by hand.
# --------------------------------------------------------------------------

_DEFAULT_EXCLUDED_DIRS = {
    ".codex-local",
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "vendor",
    "venv",
}


def _read_ignore_patterns(root: Path) -> list[str]:
    patterns: list[str] = []
    for name in (".ai/.ignore", ".gitignore"):
        path = root / name
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and not line.startswith("!"):
                patterns.append(line)
    return patterns


def _path_matches_pattern(path: Path, pattern: str, is_dir: bool) -> bool:
    normalized = path.as_posix()
    name = path.name
    if pattern.endswith("/"):
        directory_pattern = pattern.rstrip("/")
        return (
            is_dir and fnmatch.fnmatch(name, directory_pattern)
        ) or normalized == directory_pattern or normalized.startswith(f"{directory_pattern}/")
    if "/" in pattern:
        return fnmatch.fnmatch(normalized, pattern)
    return fnmatch.fnmatch(name, pattern) or any(fnmatch.fnmatch(part, pattern) for part in path.parts)


def _should_skip(path: Path, is_dir: bool, ignore_patterns: list[str]) -> bool:
    if path == Path("."):
        return False
    if is_dir and path.name in _DEFAULT_EXCLUDED_DIRS:
        return True
    if path == Path(".ai") or ".ai" in path.parts:
        return True
    return any(_path_matches_pattern(path, pattern, is_dir) for pattern in ignore_patterns)


def discover_source_files(root: Path, languages: list[str]) -> list[tuple[Path, str]]:
    extensions: dict[str, str] = {}
    for language in languages:
        for ext in LANGUAGE_EXTENSIONS[language]:
            extensions[ext] = language

    ignore_patterns = _read_ignore_patterns(root)
    found: list[tuple[Path, str]] = []

    def walk(current: Path) -> None:
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            return
        for child in children:
            relative = child.relative_to(root)
            is_dir = child.is_dir()
            if _should_skip(relative, is_dir, ignore_patterns):
                continue
            if is_dir:
                walk(child)
            elif child.suffix in extensions:
                found.append((child, extensions[child.suffix]))

    walk(root)
    return found


# --------------------------------------------------------------------------
# tree-sitter loading
# --------------------------------------------------------------------------


def load_language(language: str):
    try:
        import tree_sitter as ts
    except ImportError as exc:
        raise GraphDependencyError(
            'tree-sitter is not installed. Install it with: pip install "omniengineering-workspace[graph]"'
        ) from exc

    try:
        if language == "python":
            import tree_sitter_python as grammar

            return ts.Language(grammar.language())
        if language == "javascript":
            import tree_sitter_javascript as grammar

            return ts.Language(grammar.language())
        if language == "typescript":
            import tree_sitter_typescript as grammar

            return ts.Language(grammar.language_typescript())
    except ImportError as exc:
        raise GraphDependencyError(
            f"tree-sitter grammar for '{language}' is not installed. Install it with: "
            'pip install "omniengineering-workspace[graph]"'
        ) from exc

    raise GraphDependencyError(f"Unsupported language: {language}")


def _text(node) -> str:
    return node.text.decode("utf-8", "replace")


def _split_qualifier(name: str) -> tuple[str | None, str]:
    if "." in name:
        qualifier, simple = name.rsplit(".", 1)
        return qualifier, simple
    return None, name


# --------------------------------------------------------------------------
# Python parsing
# --------------------------------------------------------------------------


def _iter_calls_py(node, call_type: str = "call"):
    if node.type == call_type:
        yield node
    for child in node.children:
        yield from _iter_calls_py(child, call_type)


def _import_name_and_alias(node) -> tuple[str, str]:
    if node.type == "aliased_import":
        name_node = node.child_by_field_name("name")
        alias_node = node.child_by_field_name("alias")
        return _text(name_node), _text(alias_node)
    text = _text(node)
    return text, text.split(".")[-1]


def parse_python_file(path: Path, root: Path, source: bytes, graph: Graph) -> FileFacts:
    language = load_language("python")  # raises GraphDependencyError before the raw import below can
    import tree_sitter as ts
    parser = ts.Parser(language)
    tree = parser.parse(source)

    relpath = path.relative_to(root).as_posix()
    module_id = relpath
    graph.add_node(
        GraphNode(module_id, "module", path.stem, module_id, relpath, 1, source.count(b"\n") + 1, "python")
    )

    facts = FileFacts(module_id=module_id, module_relpath=relpath, language="python")

    def record_calls(node, caller_id: str, current_class_id: str | None) -> None:
        for call in _iter_calls_py(node):
            func = call.child_by_field_name("function")
            if func is None:
                continue
            if func.type == "identifier":
                name = _text(func)
                qualifier = None
            elif func.type == "attribute":
                object_node = func.child_by_field_name("object")
                attribute_node = func.child_by_field_name("attribute")
                if attribute_node is None:
                    continue
                name = _text(attribute_node)
                qualifier = _text(object_node) if object_node is not None else None
            else:
                continue
            graph.ensure_external(name)
            detail = _text(call)
            graph.add_edge(caller_id, f"external:{name}", "calls", EXTRACTED, "syntax", detail[:120])
            facts.pending_calls.append((caller_id, name, qualifier, current_class_id))

    def walk(node, container_id: str, current_class_id: str | None) -> None:
        for child in node.children:
            ctype = child.type
            if ctype == "decorated_definition":
                inner = child.child_by_field_name("definition")
                if inner is not None:
                    walk_single(inner, container_id, current_class_id)
                continue
            walk_single(child, container_id, current_class_id)

    def walk_single(child, container_id: str, current_class_id: str | None) -> None:
        ctype = child.type
        if ctype == "import_statement":
            for name_node in child.children_by_field_name("name"):
                name, alias = _import_name_and_alias(name_node)
                graph.ensure_external(name)
                graph.add_edge(module_id, f"external:{name}", "imports", EXTRACTED, "syntax", f"import {name}")
                facts.raw_module_imports.append(name)
                facts.imported_names[alias] = (name, None)
        elif ctype == "import_from_statement":
            module_field = child.child_by_field_name("module_name")
            base_module = _text(module_field) if module_field is not None else ""
            graph.ensure_external(base_module)
            graph.add_edge(
                module_id, f"external:{base_module}", "imports", EXTRACTED, "syntax", f"from {base_module} import ..."
            )
            facts.raw_module_imports.append(base_module)
            for name_node in child.children_by_field_name("name"):
                if name_node.type == "wildcard_import":
                    continue
                symbol, alias = _import_name_and_alias(name_node)
                facts.imported_names[alias] = (base_module, symbol)
        elif ctype == "class_definition":
            name_node = child.child_by_field_name("name")
            if name_node is None:
                return
            class_name = _text(name_node)
            class_id = f"{container_id}::{class_name}"
            graph.add_node(
                GraphNode(
                    class_id, "class", class_name, class_id, relpath,
                    child.start_point[0] + 1, child.end_point[0] + 1, "python",
                )
            )
            graph.add_edge(container_id, class_id, "defines", EXTRACTED, "syntax")

            superclasses = child.child_by_field_name("superclasses")
            if superclasses is not None:
                for base_child in superclasses.children:
                    if base_child.type in ("identifier", "attribute"):
                        base_name = _text(base_child)
                        graph.ensure_external(base_name)
                        graph.add_edge(
                            class_id, f"external:{base_name}", "inherits", EXTRACTED, "syntax",
                            f"class {class_name}({base_name})",
                        )
                        facts.pending_inherits.append((class_id, base_name, None))

            body = child.child_by_field_name("body")
            if body is not None:
                walk(body, class_id, class_id)
        elif ctype == "function_definition":
            name_node = child.child_by_field_name("name")
            if name_node is None:
                return
            func_name = _text(name_node)
            func_id = f"{container_id}::{func_name}"
            kind = "method" if current_class_id else "function"
            graph.add_node(
                GraphNode(
                    func_id, kind, func_name, func_id, relpath,
                    child.start_point[0] + 1, child.end_point[0] + 1, "python",
                )
            )
            graph.add_edge(container_id, func_id, "defines", EXTRACTED, "syntax")
            body = child.child_by_field_name("body")
            if body is not None:
                record_calls(body, func_id, current_class_id)

    walk(tree.root_node, module_id, None)
    return facts


# --------------------------------------------------------------------------
# JavaScript / TypeScript parsing
# --------------------------------------------------------------------------


def _iter_calls_js(node):
    if node.type == "call_expression":
        yield node
    for child in node.children:
        yield from _iter_calls_js(child)


def _js_import_clause_names(clause) -> list[tuple[str, str | None]]:
    """Return (local_name, exported_name_or_None) pairs for one import_clause."""
    names: list[tuple[str, str | None]] = []
    for node in clause.children:
        if node.type == "identifier":
            names.append((_text(node), None))
        elif node.type == "namespace_import":
            ident = next((c for c in node.children if c.type == "identifier"), None)
            if ident is not None:
                names.append((_text(ident), None))
        elif node.type == "named_imports":
            for specifier in node.children:
                if specifier.type != "import_specifier":
                    continue
                name_node = specifier.child_by_field_name("name")
                alias_node = specifier.child_by_field_name("alias")
                if name_node is None:
                    continue
                if alias_node is not None:
                    names.append((_text(alias_node), _text(name_node)))
                else:
                    names.append((_text(name_node), _text(name_node)))
    return names


def parse_js_like_file(path: Path, root: Path, source: bytes, graph: Graph, language: str) -> FileFacts:
    ts_language = load_language(language)  # raises GraphDependencyError before the raw import below can
    import tree_sitter as ts

    parser = ts.Parser(ts_language)
    tree = parser.parse(source)

    relpath = path.relative_to(root).as_posix()
    module_id = relpath
    graph.add_node(
        GraphNode(module_id, "module", path.stem, module_id, relpath, 1, source.count(b"\n") + 1, language)
    )

    facts = FileFacts(module_id=module_id, module_relpath=relpath, language=language)

    def record_calls(node, caller_id: str, current_class_id: str | None) -> None:
        for call in _iter_calls_js(node):
            func = call.child_by_field_name("function")
            if func is None:
                continue
            if func.type in ("identifier", "type_identifier"):
                name = _text(func)
                qualifier = None
            elif func.type == "member_expression":
                object_node = func.child_by_field_name("object")
                property_node = func.child_by_field_name("property")
                if property_node is None:
                    continue
                name = _text(property_node)
                qualifier = _text(object_node) if object_node is not None else None
            else:
                continue
            graph.ensure_external(name)
            graph.add_edge(caller_id, f"external:{name}", "calls", EXTRACTED, "syntax", _text(call)[:120])
            facts.pending_calls.append((caller_id, name, qualifier, current_class_id))

    def walk(node, container_id: str, current_class_id: str | None) -> None:
        for child in node.children:
            ctype = child.type
            if ctype == "export_statement":
                inner = child.child_by_field_name("declaration")
                if inner is not None:
                    walk_single(inner, container_id, current_class_id)
                continue
            walk_single(child, container_id, current_class_id)

    def walk_single(child, container_id: str, current_class_id: str | None) -> None:
        ctype = child.type
        if ctype == "import_statement":
            source_node = child.child_by_field_name("source")
            source_text = _text(source_node).strip("'\"") if source_node is not None else ""
            graph.ensure_external(source_text)
            graph.add_edge(
                module_id, f"external:{source_text}", "imports", EXTRACTED, "syntax", f"import ... from '{source_text}'"
            )
            facts.raw_module_imports.append(source_text)
            clause = next((c for c in child.children if c.type == "import_clause"), None)
            if clause is not None:
                for local_name, exported_name in _js_import_clause_names(clause):
                    facts.imported_names[local_name] = (source_text, exported_name)
        elif ctype in ("class_declaration", "abstract_class_declaration"):
            name_node = child.child_by_field_name("name")
            if name_node is None:
                return
            class_name = _text(name_node)
            class_id = f"{container_id}::{class_name}"
            graph.add_node(
                GraphNode(
                    class_id, "class", class_name, class_id, relpath,
                    child.start_point[0] + 1, child.end_point[0] + 1, language,
                )
            )
            graph.add_edge(container_id, class_id, "defines", EXTRACTED, "syntax")

            heritage = next((c for c in child.children if c.type == "class_heritage"), None)
            if heritage is not None:
                for clause in heritage.children:
                    if clause.type == "extends_clause":
                        value = clause.child_by_field_name("value")
                        if value is not None:
                            base_name = _text(value)
                            graph.ensure_external(base_name)
                            graph.add_edge(
                                class_id, f"external:{base_name}", "inherits", EXTRACTED, "syntax",
                                f"extends {base_name}",
                            )
                            facts.pending_inherits.append((class_id, base_name, None))

            body = child.child_by_field_name("body")
            if body is not None:
                walk(body, class_id, class_id)
        elif ctype in ("function_declaration", "generator_function_declaration"):
            name_node = child.child_by_field_name("name")
            if name_node is None:
                return
            func_name = _text(name_node)
            func_id = f"{container_id}::{func_name}"
            graph.add_node(
                GraphNode(
                    func_id, "function", func_name, func_id, relpath,
                    child.start_point[0] + 1, child.end_point[0] + 1, language,
                )
            )
            graph.add_edge(container_id, func_id, "defines", EXTRACTED, "syntax")
            body = child.child_by_field_name("body")
            if body is not None:
                record_calls(body, func_id, current_class_id)
        elif ctype == "method_definition":
            name_node = child.child_by_field_name("name")
            if name_node is None:
                return
            method_name = _text(name_node)
            method_id = f"{container_id}::{method_name}"
            graph.add_node(
                GraphNode(
                    method_id, "method", method_name, method_id, relpath,
                    child.start_point[0] + 1, child.end_point[0] + 1, language,
                )
            )
            graph.add_edge(container_id, method_id, "defines", EXTRACTED, "syntax")
            body = child.child_by_field_name("body")
            if body is not None:
                record_calls(body, method_id, current_class_id)

    walk(tree.root_node, module_id, None)
    return facts


# --------------------------------------------------------------------------
# Cross-file resolution (INFERRED edges)
# --------------------------------------------------------------------------

# Common built-ins that would otherwise collide with a same-named project
# symbol under the "unique name across the graph" fallback (e.g. a bare
# print(...) call resolving to a project method named print()). Skipping
# these keeps that fallback deterministic instead of coincidence-prone.
_BUILTIN_NAMES = {
    "print", "len", "str", "int", "float", "bool", "list", "dict", "set",
    "tuple", "open", "range", "enumerate", "zip", "map", "filter", "sorted",
    "reversed", "isinstance", "issubclass", "getattr", "setattr", "hasattr",
    "super", "type", "repr", "format", "min", "max", "sum", "abs", "all",
    "any", "next", "iter", "input", "vars", "dir", "id", "hash", "frozenset",
    "bytes", "bytearray", "object", "property", "staticmethod", "classmethod",
    "console", "require", "Object", "Array", "Promise", "JSON", "Map", "Set",
    "parseInt", "parseFloat", "fetch", "setTimeout", "setInterval",
}


def _resolve_import_target(
    raw: str, importing_relpath: str, module_ids: set[str], language: str
) -> str | None:
    if language == "python":
        stub = raw.replace(".", "/")
        for candidate in (f"{stub}.py", f"{stub}/__init__.py"):
            if candidate in module_ids:
                return candidate
        return None

    if not raw.startswith("."):
        return None
    base_dir = Path(importing_relpath).parent
    joined = os.path.normpath((base_dir / raw).as_posix()).replace(os.sep, "/")
    for suffix in ("", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js", "/index.jsx"):
        candidate = f"{joined}{suffix}"
        if candidate in module_ids:
            return candidate
    return None


def _resolve_reference(
    name: str,
    qualifier: str | None,
    current_class_id: str | None,
    module_relpath: str,
    language: str,
    imported_names: dict[str, tuple[str, str | None]],
    class_methods: dict[str, dict[str, str]],
    graph_nodes: dict[str, GraphNode],
    module_ids: set[str],
    simple_name_index: dict[str, list[str]],
    allow_self: bool,
) -> tuple[str, str] | None:
    if allow_self and qualifier in ("self", "cls", "this") and current_class_id in class_methods:
        target = class_methods[current_class_id].get(name)
        if target:
            return target, "resolved on the enclosing class"

    same_module_id = f"{module_relpath}::{name}" if qualifier is None else None
    if same_module_id and same_module_id in graph_nodes:
        return same_module_id, "same-module definition"

    if qualifier is not None and qualifier in imported_names:
        base_module, _symbol = imported_names[qualifier]
        target_module = _resolve_import_target(base_module, module_relpath, module_ids, language)
        if target_module:
            candidate = f"{target_module}::{name}"
            if candidate in graph_nodes:
                return candidate, f"resolved via imported module '{base_module}'"

    if qualifier is None and name in imported_names:
        base_module, symbol = imported_names[name]
        if symbol is not None:
            target_module = _resolve_import_target(base_module, module_relpath, module_ids, language)
            if target_module:
                candidate = f"{target_module}::{symbol}"
                if candidate in graph_nodes:
                    return candidate, f"resolved via import from '{base_module}'"

    if name not in _BUILTIN_NAMES:
        candidates = simple_name_index.get(name, [])
        if len(candidates) == 1:
            return candidates[0], "unique symbol name across the graph"

    return None


def _resolve_graph(graph: Graph, file_facts: list[FileFacts]) -> None:
    module_ids = {node.id for node in graph.nodes.values() if node.kind == "module"}
    simple_name_index: dict[str, list[str]] = {}
    class_methods: dict[str, dict[str, str]] = {}
    for node in graph.nodes.values():
        if node.kind in ("class", "function", "method"):
            simple_name_index.setdefault(node.name, []).append(node.id)
        if node.kind == "method":
            class_id = node.id.rsplit("::", 1)[0]
            class_methods.setdefault(class_id, {})[node.name] = node.id

    for facts in file_facts:
        for raw in facts.raw_module_imports:
            target = _resolve_import_target(raw, facts.module_relpath, module_ids, facts.language)
            if target and target != facts.module_id:
                graph.add_edge(
                    facts.module_id, target, "imports", INFERRED, "graph-traversal",
                    f"resolved '{raw}' to {target}",
                )

        for caller_id, raw_name, qualifier, current_class_id in facts.pending_calls:
            split_qualifier, name = _split_qualifier(raw_name) if qualifier is None else (qualifier, raw_name)
            resolution = _resolve_reference(
                name, split_qualifier, current_class_id, facts.module_relpath, facts.language,
                facts.imported_names, class_methods, graph.nodes, module_ids, simple_name_index,
                allow_self=True,
            )
            if resolution:
                target_id, reason = resolution
                graph.add_edge(caller_id, target_id, "calls", INFERRED, "graph-traversal", reason)

        for class_id, raw_base, _unused in facts.pending_inherits:
            split_qualifier, name = _split_qualifier(raw_base)
            resolution = _resolve_reference(
                name, split_qualifier, None, facts.module_relpath, facts.language,
                facts.imported_names, class_methods, graph.nodes, module_ids, simple_name_index,
                allow_self=False,
            )
            if resolution:
                target_id, reason = resolution
                graph.add_edge(class_id, target_id, "inherits", INFERRED, "graph-traversal", reason)


# --------------------------------------------------------------------------
# Semantic pass (opt-in, network-touching)
# --------------------------------------------------------------------------


def _call_semantic_api(api_url: str, api_key: str | None, payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(api_url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - explicit opt-in, user-configured URL
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def run_semantic_pass(graph: Graph, max_nodes: int = SEMANTIC_MAX_NODES_DEFAULT) -> dict[str, Any]:
    api_url = os.environ.get(SEMANTIC_API_URL_ENV)
    if not api_url:
        return {"enabled": False, "reason": f"{SEMANTIC_API_URL_ENV} is not set", "api_url": None}

    api_key = os.environ.get(SEMANTIC_API_KEY_ENV)
    model = os.environ.get(SEMANTIC_API_MODEL_ENV)
    known_names = {node.qualified_name: node.id for node in graph.nodes.values() if node.kind in ("class", "function", "method")}
    candidates = [node for node in graph.nodes.values() if node.kind in ("class", "function", "method")][:max_nodes]

    tagged = 0
    edges_added = 0
    errors: list[str] = []
    for node in candidates:
        payload = {
            "model": model,
            "node": {"name": node.name, "qualified_name": node.qualified_name, "kind": node.kind, "file": node.file},
            "known_symbols": sorted(known_names.keys()),
            "instructions": (
                "Given this single code symbol and known_symbols (other symbol "
                'qualified_names in the same repository), respond with strict JSON: '
                '{"summary": string, "related": [qualified_name, ...]}. Only include a '
                "qualified_name from known_symbols when this symbol is meaningfully "
                "related to it. Never invent a qualified_name not in known_symbols."
            ),
        }
        try:
            response = _call_semantic_api(api_url, api_key, payload)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            errors.append(f"{node.qualified_name}: {exc}")
            continue

        summary = response.get("summary") if isinstance(response, dict) else None
        related = response.get("related") if isinstance(response, dict) else None
        if isinstance(summary, str) and summary.strip():
            node.summary = summary.strip()
            tagged += 1
        if isinstance(related, list):
            for target_qn in related:
                target_id = known_names.get(target_qn)
                if target_id and target_id != node.id:
                    graph.add_edge(
                        node.id, target_id, "related_to", INFERRED, "semantic-api",
                        f"suggested by configured semantic API ({model or 'unspecified model'})",
                    )
                    edges_added += 1

    return {
        "enabled": True,
        "api_url": api_url,
        "model": model,
        "nodes_considered": len(candidates),
        "nodes_tagged": tagged,
        "edges_added": edges_added,
        "errors": errors,
    }


# --------------------------------------------------------------------------
# Build orchestration
# --------------------------------------------------------------------------


def build_graph(root: Path, languages: list[str], semantic: bool = False) -> tuple[Graph, dict[str, Any]]:
    graph = Graph()
    file_facts: list[FileFacts] = []

    for path, language in discover_source_files(root, languages):
        try:
            source = path.read_bytes()
        except OSError:
            continue
        if language == "python":
            facts = parse_python_file(path, root, source, graph)
        else:
            facts = parse_js_like_file(path, root, source, graph, language)
        file_facts.append(facts)

    _resolve_graph(graph, file_facts)

    semantic_info: dict[str, Any] = {"enabled": False, "reason": "not requested", "api_url": None}
    if semantic:
        semantic_info = run_semantic_pass(graph)

    return graph, semantic_info


# --------------------------------------------------------------------------
# Query surface (trace / show) -- stdlib only, no tree-sitter needed
# --------------------------------------------------------------------------


def load_graph(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_nodes(graph_data: dict[str, Any], query: str) -> list[dict[str, Any]]:
    """Resolve a name/qualified-name query to graph nodes.

    Real symbols (module/class/function/method) always take priority over
    'external' placeholder nodes -- an external node's qualified_name is
    just its bare name, so an unqualified query like 'run_doctor' would
    otherwise tie-match the placeholder for its own unresolved call sites
    instead of the actual function definition.
    """
    id_exact = [node for node in graph_data["nodes"] if node["id"] == query]
    if id_exact:
        return id_exact

    real_nodes = [node for node in graph_data["nodes"] if node["kind"] != "external"]
    qn_exact = [node for node in real_nodes if node["qualified_name"] == query]
    if qn_exact:
        return qn_exact

    query_lower = query.lower()
    real_matches = [
        node
        for node in real_nodes
        if query_lower in node["name"].lower() or query_lower in node["qualified_name"].lower()
    ]
    if real_matches:
        return real_matches

    return [node for node in graph_data["nodes"] if node["kind"] == "external" and query_lower in node["name"].lower()]


def _adjacency(graph_data: dict[str, Any]) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for edge in graph_data["edges"]:
        adjacency.setdefault(edge["source"], []).append((edge["target"], edge))
        adjacency.setdefault(edge["target"], []).append((edge["source"], edge))
    return adjacency


def shortest_path(graph_data: dict[str, Any], start_id: str, end_id: str) -> list[dict[str, Any]] | None:
    if start_id == end_id:
        return []
    adjacency = _adjacency(graph_data)
    visited = {start_id}
    queue: deque[tuple[str, list[dict[str, Any]]]] = deque([(start_id, [])])
    while queue:
        node_id, path = queue.popleft()
        for neighbor_id, edge in adjacency.get(node_id, []):
            if neighbor_id in visited:
                continue
            new_path = path + [edge]
            if neighbor_id == end_id:
                return new_path
            visited.add(neighbor_id)
            queue.append((neighbor_id, new_path))
    return None


def trace(graph_path: Path, source_query: str, target_query: str) -> dict[str, Any]:
    graph_data = load_graph(graph_path)
    sources = find_nodes(graph_data, source_query)
    targets = find_nodes(graph_data, target_query)
    if len(sources) != 1 or len(targets) != 1:
        return {
            "ok": False,
            "error": "ambiguous_or_not_found",
            "source_matches": [node["id"] for node in sources],
            "target_matches": [node["id"] for node in targets],
        }
    path_edges = shortest_path(graph_data, sources[0]["id"], targets[0]["id"])
    if path_edges is None:
        return {"ok": False, "error": "no_path", "source": sources[0]["id"], "target": targets[0]["id"]}
    return {"ok": True, "source": sources[0]["id"], "target": targets[0]["id"], "hops": path_edges}


def show(graph_path: Path, query: str) -> dict[str, Any]:
    graph_data = load_graph(graph_path)
    matches = find_nodes(graph_data, query)
    if len(matches) != 1:
        return {"ok": False, "error": "ambiguous_or_not_found", "matches": [node["id"] for node in matches]}
    node = matches[0]
    node_id = node["id"]
    outgoing = [edge for edge in graph_data["edges"] if edge["source"] == node_id]
    incoming = [edge for edge in graph_data["edges"] if edge["target"] == node_id]
    return {"ok": True, "node": node, "outgoing": outgoing, "incoming": incoming}


# --------------------------------------------------------------------------
# SVG rendering -- a real node-link graph, not a chart. Pure stdlib (a small
# Fruchterman-Reingold spring embedder) so `omni graph render`, like trace
# and show, never needs tree-sitter or any third-party layout library.
# --------------------------------------------------------------------------

RENDER_DEFAULT_OUTPUT = ".ai/project-graph.svg"
RENDER_DEFAULT_MAX_NODES = 300
RENDER_DEFAULT_ITERATIONS = 150
RENDER_DEFAULT_DEPTH = 2

_LANGUAGE_COLORS = {
    "python": "#e0475c",
    "javascript": "#4fb3bf",
    "typescript": "#c9a869",
}
_DEFAULT_NODE_COLOR = "#9a9a9a"
_KIND_RADIUS = {"module": 14, "class": 10, "function": 6, "method": 6, "external": 3}
_SVG_BG = "#0f0f12"
_SVG_TEXT = "#e8e8e8"
_SVG_DIM = "#707070"
_SVG_FAINT = "#2a2a2e"
_SVG_FONT = "'Share Tech Mono','JetBrains Mono','Courier New',monospace"


def _escape_svg_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _node_color(node: dict[str, Any]) -> str:
    return _LANGUAGE_COLORS.get(node.get("language"), _DEFAULT_NODE_COLOR)


def _ego_network(edges: list[dict[str, Any]], focus_ids: list[str], depth: int) -> set[str]:
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge["source"], set()).add(edge["target"])
        adjacency.setdefault(edge["target"], set()).add(edge["source"])
    visited = set(focus_ids)
    frontier = set(focus_ids)
    for _ in range(max(depth, 0)):
        next_frontier: set[str] = set()
        for node_id in frontier:
            next_frontier |= adjacency.get(node_id, set())
        next_frontier -= visited
        if not next_frontier:
            break
        visited |= next_frontier
        frontier = next_frontier
    return visited


def _spring_layout(
    node_ids: list[str], pair_edges: list[tuple[str, str]], iterations: int, seed: int
) -> dict[str, tuple[float, float]]:
    """A small Fruchterman-Reingold force-directed layout. O(n^2) per iteration,
    which is fine for the few hundred nodes this renders (see max_nodes)."""
    n = len(node_ids)
    if n == 0:
        return {}
    if n == 1:
        return {node_ids[0]: (0.0, 0.0)}

    rng = random.Random(seed)
    pos = {node_id: [rng.uniform(-1.0, 1.0), rng.uniform(-1.0, 1.0)] for node_id in node_ids}
    k = math.sqrt(1.0 / n)
    temperature = 0.1
    cooling = temperature / (iterations + 1)

    for _ in range(iterations):
        disp = {node_id: [0.0, 0.0] for node_id in node_ids}

        for i in range(n):
            vi = node_ids[i]
            xi, yi = pos[vi]
            for j in range(i + 1, n):
                vj = node_ids[j]
                xj, yj = pos[vj]
                dx, dy = xi - xj, yi - yj
                dist = math.hypot(dx, dy) or 1e-6
                force = (k * k) / dist
                ux, uy = dx / dist, dy / dist
                disp[vi][0] += ux * force
                disp[vi][1] += uy * force
                disp[vj][0] -= ux * force
                disp[vj][1] -= uy * force

        for source, target in pair_edges:
            xi, yi = pos[source]
            xj, yj = pos[target]
            dx, dy = xi - xj, yi - yj
            dist = math.hypot(dx, dy) or 1e-6
            force = (dist * dist) / k
            ux, uy = dx / dist, dy / dist
            disp[source][0] -= ux * force
            disp[source][1] -= uy * force
            disp[target][0] += ux * force
            disp[target][1] += uy * force

        for node_id in node_ids:
            dx, dy = disp[node_id]
            dist = math.hypot(dx, dy) or 1e-6
            capped = min(dist, temperature)
            pos[node_id][0] += dx / dist * capped
            pos[node_id][1] += dy / dist * capped

        temperature -= cooling

    return {node_id: (pos[node_id][0], pos[node_id][1]) for node_id in node_ids}


def _render_svg(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    root_label: str,
    truncated: bool,
    iterations: int,
    seed: int,
) -> str:
    node_ids = [node["id"] for node in nodes]
    by_id = {node["id"]: node for node in nodes}
    pair_edges = [(edge["source"], edge["target"]) for edge in edges]
    positions = _spring_layout(node_ids, pair_edges, iterations=iterations, seed=seed)

    width, height, margin = 1600, 1100, 70
    if positions:
        xs = [point[0] for point in positions.values()]
        ys = [point[1] for point in positions.values()]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        span_x = (maxx - minx) or 1.0
        span_y = (maxy - miny) or 1.0
    else:
        minx = miny = 0.0
        span_x = span_y = 1.0

    def sx(x: float) -> float:
        return margin + (x - minx) / span_x * (width - 2 * margin)

    def sy(y: float) -> float:
        return margin + 50 + (y - miny) / span_y * (height - 2 * margin - 50)

    languages_present = sorted({node.get("language") for node in nodes if node.get("language")})

    parts: list[str] = [
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" fill="none" '
        'xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="title desc">',
        f'<title id="title">{_escape_svg_text(root_label)} code graph</title>',
        '<desc id="desc">Force-directed node-link graph generated by omni graph render from '
        'omni graph build output -- not hand-drawn.</desc>',
        '<defs><pattern id="og-grid" width="26" height="26" patternUnits="userSpaceOnUse">'
        f'<path d="M 26 0 L 0 0 0 26" fill="none" stroke="{_LANGUAGE_COLORS["python"]}" '
        'stroke-width="0.2" opacity="0.06"/></pattern></defs>',
        f'<rect width="{width}" height="{height}" fill="{_SVG_BG}"/>',
        f'<rect width="{width}" height="{height}" fill="url(#og-grid)"/>',
        f'<text x="24" y="30" font-family="{_SVG_FONT}" font-size="15" font-weight="700" '
        f'fill="{_SVG_TEXT}" letter-spacing="0.5">{_escape_svg_text(root_label)} -- code graph</text>',
    ]

    subtitle = f"{len(nodes)} SYMBOLS · {len(edges)} RESOLVED EDGES · GENERATED BY OMNI GRAPH RENDER"
    if truncated:
        subtitle += " · TRUNCATED TO HIGHEST-DEGREE NODES"
    parts.append(
        f'<text x="24" y="48" font-family="{_SVG_FONT}" font-size="9.5" fill="{_SVG_DIM}" '
        f'letter-spacing="1.1">{_escape_svg_text(subtitle)}</text>'
    )

    edge_style = {
        "calls": (0.35, False),
        "imports": (0.6, False),
        "inherits": (0.6, True),
        "related_to": (0.55, True),
    }
    for edge in edges:
        source = by_id.get(edge["source"])
        target = by_id.get(edge["target"])
        if source is None or target is None:
            continue
        x1, y1 = sx(positions[edge["source"]][0]), sy(positions[edge["source"]][1])
        x2, y2 = sx(positions[edge["target"]][0]), sy(positions[edge["target"]][1])
        if edge["type"] == "defines":
            parts.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="{_SVG_FAINT}" stroke-width="0.6" opacity="0.5"/>'
            )
            continue
        opacity, dashed = edge_style.get(edge["type"], (0.3, False))
        color = _node_color(source)
        dash = ' stroke-dasharray="3,3"' if dashed else ""
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="0.8" opacity="{opacity}"{dash}/>'
        )

    degree: dict[str, int] = {}
    for edge in edges:
        degree[edge["source"]] = degree.get(edge["source"], 0) + 1
        degree[edge["target"]] = degree.get(edge["target"], 0) + 1

    for node in nodes:
        node_id = node["id"]
        x, y = sx(positions[node_id][0]), sy(positions[node_id][1])
        radius = _KIND_RADIUS.get(node["kind"], 6)
        color = _node_color(node)
        fill_opacity = "0.9" if node["kind"] in ("module", "class") else "0.75"
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius}" fill="{color}" '
            f'fill-opacity="{fill_opacity}" stroke="{_SVG_BG}" stroke-width="1"/>'
        )
        deg = degree.get(node_id, 0)
        if node["kind"] in ("module", "class") or deg >= 4:
            size = 9
        elif deg >= 1:
            size = 7
        else:
            continue
        tx, ty = x + radius + 4, y + 3
        text = _escape_svg_text(node["name"])
        parts.append(
            f'<text x="{tx:.1f}" y="{ty:.1f}" font-family="{_SVG_FONT}" font-size="{size}" '
            f'font-weight="700" stroke="{_SVG_BG}" stroke-width="3" fill="none">{text}</text>'
        )
        parts.append(
            f'<text x="{tx:.1f}" y="{ty:.1f}" font-family="{_SVG_FONT}" font-size="{size}" '
            f'font-weight="700" fill="{_SVG_TEXT}">{text}</text>'
        )

    legend_rows = len(languages_present) + 2
    legend_x, legend_y = width - 260, height - (30 + 22 * legend_rows)
    legend_h = 20 + 22 * legend_rows
    parts.append(
        f'<rect x="{legend_x}" y="{legend_y}" width="240" height="{legend_h}" rx="8" '
        f'fill="rgba(15,15,18,0.85)" stroke="{_LANGUAGE_COLORS["python"]}" stroke-width="0.75" opacity="0.9"/>'
    )
    row = legend_y + 22
    for language in languages_present:
        color = _LANGUAGE_COLORS.get(language, _DEFAULT_NODE_COLOR)
        parts.append(f'<circle cx="{legend_x+18}" cy="{row}" r="6" fill="{color}"/>')
        parts.append(
            f'<text x="{legend_x+32}" y="{row+4}" font-family="{_SVG_FONT}" font-size="9.5" '
            f'fill="{_SVG_TEXT}">{_escape_svg_text(language)}</text>'
        )
        row += 22
    parts.append(f'<line x1="{legend_x+12}" y1="{row}" x2="{legend_x+28}" y2="{row}" stroke="{_SVG_DIM}" stroke-width="1" opacity="0.7"/>')
    parts.append(
        f'<text x="{legend_x+34}" y="{row+4}" font-family="{_SVG_FONT}" font-size="8.5" '
        f'fill="{_SVG_DIM}">calls / imports / inherits</text>'
    )
    row += 20
    parts.append(f'<line x1="{legend_x+12}" y1="{row}" x2="{legend_x+28}" y2="{row}" stroke="{_SVG_FAINT}" stroke-width="1"/>')
    parts.append(
        f'<text x="{legend_x+34}" y="{row+4}" font-family="{_SVG_FONT}" font-size="8.5" '
        f'fill="{_SVG_DIM}">defines (module/class contents)</text>'
    )

    parts.append(
        f'<text x="{width-16}" y="{height-12}" text-anchor="end" font-family="{_SVG_FONT}" '
        f'font-size="8" fill="{_SVG_FAINT}" letter-spacing="1">OMNI-GRAPH-RENDER</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def render(
    graph_path: Path,
    include_external: bool = False,
    max_nodes: int = RENDER_DEFAULT_MAX_NODES,
    focus: str | None = None,
    depth: int = RENDER_DEFAULT_DEPTH,
    iterations: int = RENDER_DEFAULT_ITERATIONS,
    seed: int = 7,
) -> dict[str, Any]:
    graph_data = load_graph(graph_path)
    all_nodes = {node["id"]: node for node in graph_data["nodes"]}
    all_edges = graph_data["edges"]

    if focus:
        matches = find_nodes(graph_data, focus)
        if not matches:
            return {"ok": False, "error": "focus_not_found"}
        focus_ids = [node["id"] for node in matches]
        keep_ids = _ego_network(all_edges, focus_ids, depth)
    else:
        keep_ids = set(all_nodes)

    if not include_external:
        keep_ids = {node_id for node_id in keep_ids if all_nodes[node_id]["kind"] != "external"}

    truncated = False
    if len(keep_ids) > max_nodes:
        degree: dict[str, int] = {}
        for edge in all_edges:
            if edge["source"] in keep_ids:
                degree[edge["source"]] = degree.get(edge["source"], 0) + 1
            if edge["target"] in keep_ids:
                degree[edge["target"]] = degree.get(edge["target"], 0) + 1
        ranked = sorted(keep_ids, key=lambda node_id: degree.get(node_id, 0), reverse=True)
        keep_ids = set(ranked[:max_nodes])
        truncated = True

    render_nodes = [all_nodes[node_id] for node_id in keep_ids]
    render_edges = [edge for edge in all_edges if edge["source"] in keep_ids and edge["target"] in keep_ids]

    raw_root = str(graph_data.get("root") or "").strip()
    root_label = Path(raw_root).resolve().name if raw_root in ("", ".") else raw_root
    svg_text = _render_svg(render_nodes, render_edges, root_label, truncated, iterations, seed)

    return {
        "ok": True,
        "svg": svg_text,
        "nodes_rendered": len(render_nodes),
        "edges_rendered": len(render_edges),
        "nodes_total": len(all_nodes),
        "edges_total": len(all_edges),
        "truncated": truncated,
    }
