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
import html
import json
import math
import os
import random
import re
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
    attrs: dict[str, Any] = field(default_factory=dict)

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
            **({"attrs": self.attrs} if self.attrs else {}),
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
        self.stats: dict[str, dict[str, Any]] = {}
        self.notes: list[str] = []

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
            "language_stats": self.stats,
            "notes": self.notes,
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
    package: str | None = None
    scope_types: dict[str, dict[str, str]] = field(default_factory=dict)
    class_field_types: dict[str, dict[str, str]] = field(default_factory=dict)
    pending_relations: list[tuple[str, str, str, str]] = field(default_factory=list)


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
        for ext in language_extensions()[language]:
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


# --------------------------------------------------------------------------
# Language registry
#
# python / javascript / typescript keep their dedicated extractors above. Every
# other language is handled by one generic, grammar-driven extractor configured
# by the hints below (tree-sitter node-type names). A language with no hints
# still works through name-based heuristics; a language whose grammar package is
# not installed still shows up as file-level nodes. SQL is handled by a
# dependency-free schema parser (no grammar needed).
# --------------------------------------------------------------------------

_LANGUAGE_SPECS: dict[str, dict[str, Any]] = {
    "java": {
        "extensions": (".java",), "package": "tree-sitter-java", "module": "tree_sitter_java",
        "classes": {"class_declaration", "enum_declaration", "record_declaration"},
        "interfaces": {"interface_declaration", "annotation_type_declaration"},
        "functions": {"method_declaration", "constructor_declaration"},
        "calls": {"method_invocation", "object_creation_expression"},
        "imports": {"import_declaration"},
    },
    "go": {
        "extensions": (".go",), "package": "tree-sitter-go", "module": "tree_sitter_go",
        "classes": {"type_spec"}, "functions": {"function_declaration", "method_declaration"},
        "calls": {"call_expression"}, "imports": {"import_spec"},
    },
    "rust": {
        "extensions": (".rs",), "package": "tree-sitter-rust", "module": "tree_sitter_rust",
        "classes": {"struct_item", "enum_item", "union_item"}, "interfaces": {"trait_item"},
        "impls": {"impl_item"}, "functions": {"function_item", "function_signature_item"},
        "calls": {"call_expression", "macro_invocation"}, "imports": {"use_declaration"},
    },
    "csharp": {
        "extensions": (".cs",), "package": "tree-sitter-c-sharp", "module": "tree_sitter_c_sharp",
        "classes": {"class_declaration", "struct_declaration", "record_declaration", "enum_declaration"},
        "interfaces": {"interface_declaration"},
        "functions": {"method_declaration", "constructor_declaration", "local_function_statement"},
        "calls": {"invocation_expression", "object_creation_expression"}, "imports": {"using_directive"},
    },
    "c": {
        "extensions": (".c", ".h"), "package": "tree-sitter-c", "module": "tree_sitter_c",
        "classes": {"struct_specifier", "union_specifier"}, "functions": {"function_definition"},
        "calls": {"call_expression"}, "imports": {"preproc_include"},
    },
    "cpp": {
        "extensions": (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"), "package": "tree-sitter-cpp", "module": "tree_sitter_cpp",
        "classes": {"class_specifier", "struct_specifier", "union_specifier"}, "functions": {"function_definition"},
        "calls": {"call_expression"}, "imports": {"preproc_include"},
    },
    "ruby": {
        "extensions": (".rb",), "package": "tree-sitter-ruby", "module": "tree_sitter_ruby",
        "classes": {"class", "module"}, "functions": {"method", "singleton_method"},
        "calls": {"call"}, "imports": set(), "import_calls": {"require", "require_relative", "load"},
    },
    "php": {
        "extensions": (".php",), "package": "tree-sitter-php", "module": "tree_sitter_php", "factory": "language_php",
        "classes": {"class_declaration", "trait_declaration", "enum_declaration"}, "interfaces": {"interface_declaration"},
        "functions": {"function_definition", "method_declaration"},
        "calls": {"function_call_expression", "member_call_expression", "scoped_call_expression", "object_creation_expression"},
        "imports": {"namespace_use_declaration"},
    },
    "kotlin": {
        "extensions": (".kt", ".kts"), "package": "tree-sitter-kotlin", "module": "tree_sitter_kotlin",
        "classes": {"class_declaration", "object_declaration"}, "functions": {"function_declaration"},
        "calls": {"call_expression"}, "imports": {"import", "import_header"},
    },
    "swift": {
        "extensions": (".swift",), "package": "tree-sitter-swift", "module": "tree_sitter_swift",
        "classes": {"class_declaration"}, "interfaces": {"protocol_declaration"},
        "functions": {"function_declaration", "init_declaration"}, "calls": {"call_expression"},
        "imports": {"import_declaration"},
    },
    "scala": {
        "extensions": (".scala", ".sc"), "package": "tree-sitter-scala", "module": "tree_sitter_scala",
        "classes": {"class_definition", "object_definition", "enum_definition"}, "interfaces": {"trait_definition"},
        "functions": {"function_definition", "function_declaration"}, "calls": {"call_expression"},
        "imports": {"import_declaration"},
    },
    "bash": {
        "extensions": (".sh", ".bash"), "package": "tree-sitter-bash", "module": "tree_sitter_bash",
        "functions": {"function_definition"}, "calls": {"command"}, "imports": set(), "import_calls": {"source", "."},
    },
    "lua": {
        "extensions": (".lua",), "package": "tree-sitter-lua", "module": "tree_sitter_lua",
        "functions": {"function_declaration"}, "calls": {"function_call"}, "imports": set(), "import_calls": {"require", "dofile"},
    },
    "dart": {
        "extensions": (".dart",), "package": "tree-sitter-dart", "module": "tree_sitter_dart",
        "classes": {"class_definition", "mixin_declaration", "enum_declaration"}, "functions": {"function_signature"},
        "imports": {"import_or_export"},
    },
    "elixir": {
        "extensions": (".ex", ".exs"), "package": "tree-sitter-elixir", "module": "tree_sitter_elixir",
        "classes": set(), "functions": set(), "calls": {"call"}, "imports": set(),  # defmodule/def are calls: see parse_generic_file
    },
    "haskell": {
        "extensions": (".hs",), "package": "tree-sitter-haskell", "module": "tree_sitter_haskell",
        "classes": {"data_type", "newtype", "type_synomym", "class"}, "functions": {"function"},
        "calls": {"apply"}, "imports": {"import"},
    },
    "ocaml": {
        "extensions": (".ml", ".mli"), "package": "tree-sitter-ocaml", "module": "tree_sitter_ocaml", "factory": "language_ocaml",
        "classes": {"module_binding"}, "functions": {"let_binding"}, "calls": {"application_expression"},
        "imports": {"open_statement"},
    },
    "perl": {
        "extensions": (".pl", ".pm"), "package": "tree-sitter-perl", "module": "tree_sitter_perl",
        "classes": {"package_statement"}, "functions": {"subroutine_declaration_statement"},
        "calls": {"function_call_expression", "method_call_expression", "ambiguous_function_call_expression"},
        "imports": {"use_statement", "require_statement"},
    },
    "zig": {"extensions": (".zig",), "package": "tree-sitter-zig", "module": "tree_sitter_zig"},
}

# Node-type heuristics for languages with no (or partial) hints.
_HEUR_CLASS = re.compile(r"^(class|struct|interface|trait|enum|record|object|protocol|union|actor)_(declaration|definition|item|specifier)$")
_HEUR_INTERFACE = re.compile(r"(interface|trait|protocol)")
_HEUR_FUNC = re.compile(r"^(function|method|constructor|destructor|subroutine|singleton_method|func|fn)_(declaration|definition|item|signature|statement)$")
_HEUR_CALL = re.compile(r"^(call|call_expression|method_call|function_call|invocation_expression|method_invocation|call_expr|function_call_expression|member_call_expression|scoped_call_expression)$")
_HEUR_IMPORT = re.compile(r"^(import|use|using|include|require)(_(declaration|statement|directive|header|clause))?$|^preproc_include$")
_NAME_NODE_TYPES = {
    "identifier", "type_identifier", "simple_identifier", "constant", "field_identifier", "name", "property_identifier",
    "variable_name", "word", "value_name", "module_name", "bareword", "package", "variable", "alias",
}
_ELIXIR_MODULE_CALLS = {"defmodule", "defprotocol", "defimpl"}
_ELIXIR_FUNCTION_CALLS = {"def", "defp", "defmacro", "defmacrop"}
_ELIXIR_IMPORT_CALLS = {"import", "alias", "use", "require"}
_ELIXIR_IGNORED_CALLS = _ELIXIR_MODULE_CALLS | _ELIXIR_FUNCTION_CALLS | _ELIXIR_IMPORT_CALLS | {
    "if", "unless", "case", "cond", "with", "for", "fn", "quote", "unquote", "try", "receive", "raise", "@",
}
_DECLARATOR_WRAPPERS = {"function_declarator", "pointer_declarator", "reference_declarator", "parenthesized_declarator", "array_declarator"}
_STEREOTYPES = {
    "RestController": "controller", "Controller": "controller", "RestControllerAdvice": "controller", "ControllerAdvice": "controller",
    "Service": "service", "Repository": "repository", "Component": "component", "Configuration": "configuration",
    "Entity": "entity", "MappedSuperclass": "entity", "Embeddable": "entity", "SpringBootApplication": "configuration",
}
_HTTP_MAPPINGS = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "DeleteMapping": "DELETE", "PatchMapping": "PATCH", "RequestMapping": "ANY"}
_SOURCE_LANGUAGES = ("python", "javascript", "typescript")


def language_extensions() -> dict[str, tuple[str, ...]]:
    table = dict(LANGUAGE_EXTENSIONS)
    for name, spec in _LANGUAGE_SPECS.items():
        table[name] = tuple(spec["extensions"])
    table["sql"] = (".sql",)
    return table


def _generic_language(language: str):
    spec = _LANGUAGE_SPECS[language]
    try:
        import tree_sitter as ts
    except ImportError as exc:
        raise GraphDependencyError(
            'tree-sitter is not installed. Install it with: pip install "omniengineering-workspace[graph]"'
        ) from exc
    try:
        import importlib

        grammar = importlib.import_module(spec["module"])
        return ts.Language(getattr(grammar, spec.get("factory", "language"))())
    except (ImportError, AttributeError) as exc:
        raise GraphDependencyError(
            f"tree-sitter grammar for '{language}' is not installed. Install it with: pip install {spec['package']}"
        ) from exc


def _spec_set(spec: dict[str, Any], key: str) -> set[str]:
    return set(spec.get(key) or ())


def _simple_type(node) -> str:
    """Bare type name from a type node: strips generics, arrays, annotations and qualifiers."""
    text = _text(node).strip()
    text = re.sub(r"<.*", "", text)
    text = re.sub(r"[\[\]?*&]", "", text).strip()
    text = re.split(r"\s+", text)[-1] if text else text
    return re.split(r"\.|::", text)[-1]


def _type_arguments(node) -> list[str]:
    names = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in ("type_arguments", "type_argument_list", "type_parameters"):
            for child in current.named_children:
                if child.type not in ("wildcard",):
                    names.append(_simple_type(child))
        else:
            stack.extend(current.children)
    return [name for name in names if name]


def _node_name(node) -> str | None:
    name_node = node.child_by_field_name("name") or node.child_by_field_name("pattern")
    if name_node is None:
        declarator = node.child_by_field_name("declarator")
        while declarator is not None and declarator.type in _DECLARATOR_WRAPPERS:
            declarator = declarator.child_by_field_name("declarator")
        name_node = declarator
    if name_node is None:
        for child in node.named_children:
            if child.type in _NAME_NODE_TYPES:
                name_node = child
                break
    if name_node is None:
        return None
    text = _text(name_node).strip()
    return text or None


def _annotations_of(node) -> list[tuple[str, str]]:
    """(name, raw arguments) for Java-style annotations on a declaration's modifiers."""
    found: list[tuple[str, str]] = []
    for child in node.children:
        if child.type != "modifiers":
            continue
        for modifier in child.children:
            if modifier.type in ("marker_annotation", "annotation"):
                name_node = modifier.child_by_field_name("name")
                args = modifier.child_by_field_name("arguments")
                if name_node is not None:
                    found.append((_text(name_node).split(".")[-1], _text(args) if args is not None else ""))
    return found


def _annotation_string(arguments: str, key: str | None = None) -> str | None:
    if not arguments:
        return None
    if key:
        match = re.search(rf'\b{key}\s*=\s*"([^"]*)"', arguments)
        if match:
            return match.group(1)
    match = re.search(r'(?:^\(\s*|value\s*=\s*|path\s*=\s*)"([^"]*)"', arguments)
    return match.group(1) if match else None


def _java_class_attrs(annotations: list[tuple[str, str]]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if annotations:
        attrs["annotations"] = [name for name, _ in annotations]
    for name, arguments in annotations:
        if name in _STEREOTYPES and "stereotype" not in attrs:
            attrs["stereotype"] = _STEREOTYPES[name]
        if name == "Table":
            table = _annotation_string(arguments, "name")
            if table:
                attrs["table_name"] = table
        if name == "RequestMapping":
            base = _annotation_string(arguments)
            if base:
                attrs["base_path"] = base
    return attrs


def _java_endpoint(annotations: list[tuple[str, str]], base_path: str) -> str | None:
    for name, arguments in annotations:
        if name in _HTTP_MAPPINGS:
            path = _annotation_string(arguments) or ""
            return f"{_HTTP_MAPPINGS[name]} {(base_path.rstrip('/') + '/' + path.lstrip('/')).rstrip('/') or '/'}"
    return None


def _import_raw(spec: dict[str, Any], node) -> list[str]:
    """Raw import targets (a module path, a dotted name) found under an import node."""
    if node.type == "preproc_include":
        path = node.child_by_field_name("path")
        return [_text(path).strip('<>"')] if path is not None else []
    text = _text(node).strip()
    quoted = re.findall(r'["\']([^"\']+)["\']', text)
    if quoted:
        return quoted
    text = re.sub(r"^\s*(import|use|using|from|require|include|extern\s+crate|open)\s+(static\s+)?", "", text)
    text = re.sub(r"\s+as\s+\w+.*$", "", text).rstrip(";").strip()
    if "{" in text:
        base, _, rest = text.partition("{")
        base = base.rstrip(":. ")
        names = [part.strip() for part in rest.rstrip("}").split(",") if part.strip()]
        return [f"{base}::{name.split(' as ')[0]}" for name in names] or [base]
    return [text] if text else []


def _call_parts(node) -> tuple[str | None, str | None]:
    """(qualifier, name) for a call/creation node; both bare identifiers or None."""
    if node.type == "object_creation_expression":
        type_node = node.child_by_field_name("type")
        return None, _simple_type(type_node) if type_node is not None else None
    receiver = None
    for field_name in ("object", "receiver", "operand", "scope"):
        receiver = node.child_by_field_name(field_name)
        if receiver is not None:
            break
    callee = None
    for field_name in ("function", "method", "name", "callee", "constructor", "target"):
        callee = node.child_by_field_name(field_name)
        if callee is not None:
            break
    if callee is None and node.named_children:
        callee = node.named_children[0]
    if callee is None:
        return None, None
    callee_text = re.sub(r"\(.*", "", _text(callee), flags=re.S).strip()
    parts = [part for part in re.split(r"\?\.|\.|::|->|:", re.sub(r"<[^<>]*>", "", callee_text)) if part.strip()]
    if not parts:
        return None, None
    name = re.sub(r"\W+$", "", parts[-1].strip())
    qualifier = None
    if receiver is not None:
        receiver_parts = [p for p in re.split(r"\?\.|\.|::|->", re.sub(r"\(.*", "", _text(receiver), flags=re.S)) if p.strip()]
        qualifier = receiver_parts[-1].strip() if receiver_parts else None
    elif len(parts) > 1:
        qualifier = parts[-2].strip()
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", name or ""):
        return None, None
    if qualifier is not None and not re.fullmatch(r"[A-Za-z_$][\w$]*", qualifier):
        qualifier = None
    return qualifier, name


def _collect_var_types(node, out: dict[str, str]) -> None:
    stack = [node]
    while stack:
        current = stack.pop()
        type_node = current.child_by_field_name("type")
        if type_node is not None:
            names = []
            name_node = current.child_by_field_name("name")
            if name_node is not None:
                names.append(name_node)
            declarators = list(current.children_by_field_name("declarator"))
            if not declarators and name_node is None:
                declarators = [child for child in current.children if child.type.endswith("declarator")]
            for declarator in declarators:
                inner = declarator.child_by_field_name("name")
                if inner is None:
                    inner = next((c for c in declarator.children if c.type in _NAME_NODE_TYPES), declarator)
                names.append(inner)
            for name_node in names:
                if name_node.type in _NAME_NODE_TYPES:
                    simple = _simple_type(type_node)
                    if simple:
                        out[_text(name_node)] = simple
        stack.extend(current.children)


def parse_generic_file(path: Path, root: Path, source: bytes, graph: Graph, language: str) -> FileFacts:
    spec = _LANGUAGE_SPECS[language]
    ts_language = _generic_language(language)
    import tree_sitter as ts

    tree = ts.Parser(ts_language).parse(source)
    relpath = path.relative_to(root).as_posix()
    module_id = relpath
    graph.add_node(GraphNode(module_id, "module", path.stem, module_id, relpath, 1, source.count(b"\n") + 1, language))
    facts = FileFacts(module_id=module_id, module_relpath=relpath, language=language)

    hinted = any(spec.get(key) for key in ("classes", "interfaces", "functions", "calls", "imports"))
    class_types = _spec_set(spec, "classes")
    interface_types = _spec_set(spec, "interfaces")
    impl_types = _spec_set(spec, "impls")
    function_types = _spec_set(spec, "functions")
    call_types = _spec_set(spec, "calls")
    import_types = _spec_set(spec, "imports")
    import_calls = _spec_set(spec, "import_calls")

    def is_class(node_type: str) -> bool:
        return node_type in class_types or node_type in interface_types or (not hinted and bool(_HEUR_CLASS.match(node_type)))

    def is_interface(node_type: str) -> bool:
        return node_type in interface_types or (not hinted and bool(_HEUR_INTERFACE.search(node_type)))

    def is_function(node_type: str) -> bool:
        return node_type in function_types or (not hinted and bool(_HEUR_FUNC.match(node_type)))

    def is_call(node_type: str) -> bool:
        return node_type in call_types or (not hinted and bool(_HEUR_CALL.match(node_type)))

    def is_import(node_type: str) -> bool:
        return node_type in import_types or (not hinted and bool(_HEUR_IMPORT.match(node_type)))

    def relations_of(class_node, class_id: str, class_name: str, is_iface: bool) -> None:
        for index, child in enumerate(class_node.children):
            field_name = class_node.field_name_for_child(index) or ""
            marker = f"{field_name} {child.type}".lower()
            if not re.search(r"super|extends|implements|inherit|base|trait|conform|delegation|interfaces|parent", marker):
                continue
            edge_type = "implements" if re.search(r"implement|interfaces|trait|conform", marker) and not is_iface else "inherits"
            stack = [child]
            seen: set[str] = set()
            while stack:
                current = stack.pop()
                if current.type in ("type_identifier", "identifier", "scoped_type_identifier", "constant", "scope_resolution", "qualified_name", "name"):
                    name = _simple_type(current)
                    if name and name not in seen and name != class_name:
                        seen.add(name)
                        graph.ensure_external(name)
                        graph.add_edge(class_id, f"external:{name}", edge_type, EXTRACTED, "syntax", f"{class_name} {edge_type} {name}")
                        facts.pending_relations.append((class_id, name, edge_type, f"{class_name} {edge_type} {name}"))
                    continue
                if current.type in ("generic_type", "parameterized_type"):
                    base = current.child_by_field_name("type") or (current.named_children[0] if current.named_children else None)
                    if base is not None:
                        name = _simple_type(base)
                        graph.ensure_external(name)
                        graph.add_edge(class_id, f"external:{name}", edge_type, EXTRACTED, "syntax", f"{class_name} {edge_type} {name}")
                        facts.pending_relations.append((class_id, name, edge_type, f"{class_name} {edge_type} {name}"))
                        for argument in _type_arguments(current):
                            facts.pending_relations.append((class_id, argument, "uses", f"{name}<{argument}> type argument of {class_name}"))
                    continue
                stack.extend(reversed(current.children))

    def record_calls(body, caller_id: str, class_id: str | None) -> None:
        stack = [body]
        while stack:
            current = stack.pop()
            if is_call(current.type):
                qualifier, name = _call_parts(current)
                if name and language == "elixir" and name in _ELIXIR_IGNORED_CALLS:
                    pass
                elif name and name not in import_calls:
                    graph.ensure_external(name)
                    graph.add_edge(caller_id, f"external:{name}", "calls", EXTRACTED, "syntax", " ".join(_text(current).split())[:120])
                    facts.pending_calls.append((caller_id, name, qualifier, class_id))
                elif name in import_calls:
                    target = current.child_by_field_name("arguments")
                    for raw in re.findall(r'["\']([^"\']+)["\']', _text(target) if target is not None else _text(current)):
                        graph.ensure_external(raw)
                        graph.add_edge(module_id, f"external:{raw}", "imports", EXTRACTED, "syntax", f"{name} {raw}")
                        facts.raw_module_imports.append(raw)
            stack.extend(current.children)

    def add_class(node, container_id: str, kind: str, name: str) -> str:
        class_id = f"{container_id}::{name}"
        attrs: dict[str, Any] = {}
        annotations: list[tuple[str, str]] = []
        if language == "java":
            annotations = _annotations_of(node)
            attrs = _java_class_attrs(annotations)
        graph.add_node(GraphNode(class_id, kind, name, class_id, relpath, node.start_point[0] + 1, node.end_point[0] + 1, language, attrs=attrs))
        graph.add_edge(container_id, class_id, "defines", EXTRACTED, "syntax")
        return class_id

    def elixir_name(call_node) -> str | None:
        arguments = next((c for c in call_node.children if c.type == "arguments"), None)
        first = arguments.named_children[0] if arguments is not None and arguments.named_children else None
        if first is None:
            return None
        if first.type == "call":
            target = first.child_by_field_name("target")
            return _text(target) if target is not None else None
        return _text(first).split("(")[0].strip() or None

    def walk(node, container_id: str, class_id: str | None) -> None:
        for child in node.children:
            child_type = child.type
            if language == "elixir" and child_type == "call":
                target = child.child_by_field_name("target")
                target_name = _text(target) if target is not None else ""
                if target_name in _ELIXIR_MODULE_CALLS:
                    name = elixir_name(child)
                    if name:
                        new_id = add_class(child, container_id, "class", name)
                        walk(child, new_id, new_id)
                        continue
                elif target_name in _ELIXIR_FUNCTION_CALLS:
                    name = elixir_name(child)
                    if name:
                        func_id = f"{class_id or container_id}::{name}"
                        graph.add_node(GraphNode(func_id, "method" if class_id else "function", name, func_id, relpath, child.start_point[0] + 1, child.end_point[0] + 1, language))
                        graph.add_edge(class_id or container_id, func_id, "defines", EXTRACTED, "syntax")
                        record_calls(child, func_id, class_id)
                        continue
                elif target_name in _ELIXIR_IMPORT_CALLS:
                    raw = elixir_name(child)
                    if raw:
                        graph.ensure_external(raw)
                        graph.add_edge(module_id, f"external:{raw}", "imports", EXTRACTED, "syntax", f"{target_name} {raw}")
                        facts.raw_module_imports.append(raw)
                        facts.imported_names[raw.split(".")[-1]] = (raw, None)
                    continue
            if is_class(child_type):
                name = _node_name(child)
                if not name:
                    walk(child, container_id, class_id)
                    continue
                if language == "go" and child_type == "type_spec":
                    type_node = child.child_by_field_name("type")
                    kind = "interface" if type_node is not None and type_node.type == "interface_type" else "class"
                else:
                    kind = "interface" if is_interface(child_type) else "class"
                new_id = add_class(child, container_id, kind, name)
                relations_of(child, new_id, name, kind == "interface")
                field_types: dict[str, str] = {}
                for member in child.children:
                    if not is_function(member.type):
                        _collect_var_types(member, field_types)
                if field_types:
                    facts.class_field_types[new_id] = field_types
                    for field_name, type_name in field_types.items():
                        facts.pending_relations.append((new_id, type_name, "uses", f"field {field_name}: {type_name}"))
                walk(child, new_id, new_id)
            elif child_type in impl_types:
                type_node = child.child_by_field_name("type")
                trait_node = child.child_by_field_name("trait")
                type_name = _simple_type(type_node) if type_node is not None else None
                if type_name:
                    impl_id = f"{container_id}::{type_name}"
                    graph.add_node(GraphNode(impl_id, "class", type_name, impl_id, relpath, child.start_point[0] + 1, child.end_point[0] + 1, language))
                    graph.add_edge(container_id, impl_id, "defines", EXTRACTED, "syntax")
                    if trait_node is not None:
                        trait_name = _simple_type(trait_node)
                        graph.ensure_external(trait_name)
                        graph.add_edge(impl_id, f"external:{trait_name}", "implements", EXTRACTED, "syntax", f"impl {trait_name} for {type_name}")
                        facts.pending_relations.append((impl_id, trait_name, "implements", f"impl {trait_name} for {type_name}"))
                    walk(child, impl_id, impl_id)
                else:
                    walk(child, container_id, class_id)
            elif is_function(child_type):
                if language == "haskell" and child.parent is not None and child.parent.type == "signature":
                    continue  # a function *type*, not a definition
                name = _node_name(child)
                owner = class_id
                receiver = child.child_by_field_name("receiver")
                if name and "::" in name:
                    owner_name, _, name = name.rpartition("::")
                    owner = f"{container_id}::{owner_name.split('::')[-1]}"
                elif receiver is not None and language == "go":
                    receiver_name = _simple_type(receiver).strip("()") or None
                    for descendant in receiver.children:
                        for grandchild in descendant.children:
                            if grandchild.type in ("type_identifier", "pointer_type", "generic_type"):
                                receiver_name = _simple_type(grandchild)
                    if receiver_name:
                        owner = f"{module_id}::{receiver_name}"
                if not name:
                    continue
                func_id = f"{owner or container_id}::{name}"
                kind = "method" if owner else "function"
                attrs = {}
                if language == "java":
                    annotations = _annotations_of(child)
                    base = graph.nodes[owner].attrs.get("base_path", "") if owner in graph.nodes else ""
                    endpoint = _java_endpoint(annotations, base)
                    if endpoint:
                        attrs["endpoint"] = endpoint
                    if annotations:
                        attrs["annotations"] = [n for n, _ in annotations]
                graph.add_node(GraphNode(func_id, kind, name, func_id, relpath, child.start_point[0] + 1, child.end_point[0] + 1, language, attrs=attrs))
                graph.add_edge(owner or container_id, func_id, "defines", EXTRACTED, "syntax")
                scope_types: dict[str, str] = {}
                _collect_var_types(child, scope_types)
                if scope_types:
                    facts.scope_types[func_id] = scope_types
                record_calls(child, func_id, owner)
            elif is_import(child_type):
                for raw in _import_raw(spec, child):
                    graph.ensure_external(raw)
                    graph.add_edge(module_id, f"external:{raw}", "imports", EXTRACTED, "syntax", f"import {raw}")
                    facts.raw_module_imports.append(raw)
                    simple = re.split(r"\.|::|/|\\\\", raw.rstrip("*.:/"))[-1]
                    if simple:
                        facts.imported_names[simple] = (raw, None)
            else:
                if language == "java" and child_type == "package_declaration":
                    facts.package = _text(child).replace("package", "").strip().rstrip(";").strip()
                elif language == "csharp" and child_type in ("namespace_declaration", "file_scoped_namespace_declaration"):
                    name_node = child.child_by_field_name("name")
                    if name_node is not None:
                        facts.package = _text(name_node)
                elif not function_types and not hinted and is_call(child_type):
                    pass
                walk(child, container_id, class_id)

    walk(tree.root_node, module_id, None)
    if import_calls and not hinted:
        pass
    # Top-level statements can also be calls/imports (scripts): scan the module for require-style imports.
    if import_calls:
        record_calls(tree.root_node, module_id, None)
    return facts


# --------------------------------------------------------------------------
# SQL schema (dependency-free): replays migrations in order to the final state
# --------------------------------------------------------------------------

_SQL_TABLE_NAME = r'(?:"?[\w$]+"?\.)?"?([\w$]+)"?'


def _sql_statements(text: str) -> list[tuple[str, int]]:
    """Split SQL into (statement, start_line), skipping comments and dollar-quoted bodies."""
    statements: list[tuple[str, int]] = []
    buffer: list[str] = []
    line = 1
    start_line = 1
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        two = text[index:index + 2]
        if char == "\n":
            line += 1
        if two == "--":
            end = text.find("\n", index)
            index = length if end == -1 else end
            continue
        if two == "/*":
            end = text.find("*/", index + 2)
            end = length if end == -1 else end + 2
            line += text.count("\n", index, end)
            index = end
            continue
        if char == "'":
            end = index + 1
            while end < length:
                if text[end] == "'" and text[end:end + 2] == "''":
                    end += 2
                    continue
                if text[end] == "'":
                    break
                end += 1
            segment = text[index:end + 1]
            line += segment.count("\n")
            buffer.append(segment)
            index = end + 1
            continue
        if char == "$":
            match = re.match(r"\$[A-Za-z_]*\$", text[index:])
            if match:
                tag = match.group(0)
                end = text.find(tag, index + len(tag))
                end = length if end == -1 else end + len(tag)
                line += text.count("\n", index, end)
                buffer.append("''")  # dollar-quoted bodies (functions, DO blocks) are opaque
                index = end
                continue
        if char == ";":
            statement = " ".join("".join(buffer).split())
            if statement:
                statements.append((statement, start_line))
            buffer = []
            start_line = line
            index += 1
            continue
        if not buffer and char.isspace():
            index += 1
            start_line = line if char != "\n" else line
            continue
        if not buffer:
            start_line = line
        buffer.append(char)
        index += 1
    tail = " ".join("".join(buffer).split())
    if tail:
        statements.append((tail, start_line))
    return statements


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    in_quote = False
    for char in text:
        if char == "'":
            in_quote = not in_quote
        if not in_quote:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                parts.append("".join(current).strip())
                current = []
                continue
        current.append(char)
    if "".join(current).strip():
        parts.append("".join(current).strip())
    return parts


def _sql_ident(raw: str) -> str:
    return raw.strip().strip('"').strip("`").lower()


def _sql_cols(raw: str) -> list[str]:
    return [_sql_ident(part) for part in raw.split(",") if part.strip()]


_SQL_COLUMN_TYPE_STOP = re.compile(
    r"\s+(?:NOT\s+NULL|NULL|DEFAULT|PRIMARY\s+KEY|REFERENCES|UNIQUE|CHECK|CONSTRAINT|GENERATED|COLLATE)\b", re.I
)


def _sql_parse_column(definition: str) -> dict[str, Any] | None:
    match = re.match(r'\s*"?([\w$]+)"?\s+(.*)$', definition, re.S)
    if not match:
        return None
    name, rest = match.group(1).lower(), match.group(2)
    stop = _SQL_COLUMN_TYPE_STOP.search(" " + rest)
    type_text = rest[: stop.start() - 1] if stop and stop.start() > 0 else rest
    column: dict[str, Any] = {"name": name, "type": " ".join(type_text.split()).lower() or "?"}
    upper = " " + rest.upper() + " "
    column["nullable"] = " NOT NULL " not in upper and " PRIMARY KEY " not in upper
    if " PRIMARY KEY " in upper:
        column["pk"] = True
    if " UNIQUE " in upper:
        column["unique"] = True
    reference = re.search(r"\bREFERENCES\s+" + _SQL_TABLE_NAME + r"\s*(?:\(([^)]*)\))?", rest, re.I)
    if reference:
        column["fk"] = {"table": reference.group(1).lower(), "columns": _sql_cols(reference.group(2) or "")}
    return column


def _apply_table_constraint(table: dict[str, Any], item: str) -> bool:
    text = re.sub(r"^CONSTRAINT\s+\S+\s+", "", item, flags=re.I)
    upper = text.upper()
    if upper.startswith("PRIMARY KEY"):
        match = re.search(r"\(([^)]*)\)", text)
        if match:
            table["pk"] = _sql_cols(match.group(1))
        return True
    if upper.startswith("FOREIGN KEY"):
        match = re.search(r"FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+" + _SQL_TABLE_NAME + r"\s*(?:\(([^)]*)\))?", text, re.I)
        if match:
            table["fks"].append({"columns": _sql_cols(match.group(1)), "table": match.group(2).lower(), "ref_columns": _sql_cols(match.group(3) or "")})
        return True
    if upper.startswith(("UNIQUE", "CHECK", "EXCLUDE")):
        return True
    return False


def parse_sql_migrations(files: list[tuple[Path, str]], graph: Graph) -> dict[str, int]:
    def flyway_key(item: tuple[Path, str]):
        match = re.match(r"[VvRr](\d+(?:[._]\d+)*)__", item[0].name)
        version = tuple(int(part) for part in re.split(r"[._]", match.group(1))) if match else (10**9,)
        return (version, item[1])

    tables: dict[str, dict[str, Any]] = {}
    origin: dict[str, tuple[str, int]] = {}
    file_created: dict[str, list[str]] = {}
    understood = ignored = 0

    for path, relpath in sorted(files, key=flyway_key):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        graph.add_node(GraphNode(relpath, "module", path.stem, relpath, relpath, 1, text.count("\n") + 1, "sql"))
        for statement, line in _sql_statements(text):
            upper = statement.upper()
            create = re.match(r"CREATE\s+(?:UNLOGGED\s+|TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?" + _SQL_TABLE_NAME + r"\s*(.*)$", statement, re.I | re.S)
            if create:
                name = create.group(1).lower()
                rest = create.group(2)
                table = {"name": name, "columns": {}, "pk": [], "fks": [], "indexes": [], "migrations": [relpath]}
                if rest.startswith("("):
                    depth = 0
                    for position, char in enumerate(rest):
                        depth += char == "("
                        depth -= char == ")"
                        if depth == 0:
                            body = rest[1:position]
                            break
                    else:
                        body = rest[1:]
                    for item in _split_top_level(body):
                        if re.match(r"(CONSTRAINT|PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|EXCLUDE|LIKE)\b", item, re.I):
                            _apply_table_constraint(table, item)
                            continue
                        column = _sql_parse_column(item)
                        if column:
                            table["columns"][column["name"]] = column
                            if column.get("pk"):
                                table["pk"] = [column["name"]]
                            if column.get("fk"):
                                table["fks"].append({"columns": [column["name"]], "table": column["fk"]["table"], "ref_columns": column["fk"]["columns"]})
                tables[name] = table
                origin[name] = (relpath, line)
                file_created.setdefault(relpath, []).append(name)
                understood += 1
                continue
            alter = re.match(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?" + _SQL_TABLE_NAME + r"\s+(.*)$", statement, re.I | re.S)
            if alter:
                name = alter.group(1).lower()
                table = tables.get(name)
                if table is None:
                    ignored += 1
                    continue
                if relpath not in table["migrations"]:
                    table["migrations"].append(relpath)
                for action in _split_top_level(alter.group(2)):
                    add = re.match(r"ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(.*)$", action, re.I | re.S)
                    if re.match(r"ADD\s+(CONSTRAINT|PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK)\b", action, re.I):
                        _apply_table_constraint(table, re.sub(r"^ADD\s+", "", action, flags=re.I))
                    elif add:
                        column = _sql_parse_column(add.group(1))
                        if column:
                            table["columns"][column["name"]] = column
                            if column.get("fk"):
                                table["fks"].append({"columns": [column["name"]], "table": column["fk"]["table"], "ref_columns": column["fk"]["columns"]})
                    drop = re.match(r"DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?\"?([\w$]+)\"?", action, re.I)
                    if drop:
                        table["columns"].pop(drop.group(1).lower(), None)
                        table["fks"] = [fk for fk in table["fks"] if drop.group(1).lower() not in fk["columns"]]
                    rename_col = re.match(r"RENAME\s+COLUMN\s+\"?([\w$]+)\"?\s+TO\s+\"?([\w$]+)\"?", action, re.I)
                    if rename_col and rename_col.group(1).lower() in table["columns"]:
                        old, new = rename_col.group(1).lower(), rename_col.group(2).lower()
                        column = table["columns"].pop(old)
                        column["name"] = new
                        table["columns"][new] = column
                    alter_col = re.match(r"ALTER\s+COLUMN\s+\"?([\w$]+)\"?\s+(?:SET\s+DATA\s+)?TYPE\s+(.+?)(?:\s+USING\b.*)?$", action, re.I)
                    if alter_col and alter_col.group(1).lower() in table["columns"]:
                        table["columns"][alter_col.group(1).lower()]["type"] = " ".join(alter_col.group(2).split()).lower()
                    not_null = re.match(r"ALTER\s+COLUMN\s+\"?([\w$]+)\"?\s+(SET|DROP)\s+NOT\s+NULL", action, re.I)
                    if not_null and not_null.group(1).lower() in table["columns"]:
                        table["columns"][not_null.group(1).lower()]["nullable"] = not_null.group(2).upper() == "DROP"
                    rename_table = re.match(r"RENAME\s+TO\s+\"?([\w$]+)\"?", action, re.I)
                    if rename_table:
                        new = rename_table.group(1).lower()
                        tables[new] = tables.pop(name)
                        tables[new]["name"] = new
                        origin[new] = origin.pop(name)
                        for other in tables.values():
                            for fk in other["fks"]:
                                if fk["table"] == name:
                                    fk["table"] = new
                understood += 1
                continue
            drop_table = re.match(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(.*?)(?:\s+CASCADE|\s+RESTRICT)?$", statement, re.I)
            if drop_table:
                for raw in drop_table.group(1).split(","):
                    dropped = _sql_ident(raw.split(".")[-1])
                    tables.pop(dropped, None)
                    origin.pop(dropped, None)
                    for other in tables.values():
                        other["fks"] = [fk for fk in other["fks"] if fk["table"] != dropped]
                understood += 1
                continue
            index = re.match(r"CREATE\s+(UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?\"?([\w$]+)\"?\s+ON\s+(?:ONLY\s+)?" + _SQL_TABLE_NAME + r"\s*(?:USING\s+\w+\s*)?\(([^)]*)\)", statement, re.I)
            if index:
                table = tables.get(index.group(3).lower())
                if table is not None:
                    table["indexes"].append({"name": index.group(2).lower(), "unique": bool(index.group(1)), "columns": _sql_cols(index.group(4))})
                understood += 1
                continue
            drop_index = re.match(r"DROP\s+INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+EXISTS\s+)?(?:\"?[\w$]+\"?\.)?\"?([\w$]+)\"?", statement, re.I)
            if drop_index:
                for table in tables.values():
                    table["indexes"] = [ix for ix in table["indexes"] if ix["name"] != drop_index.group(1).lower()]
                understood += 1
                continue
            ignored += 1  # INSERT/UPDATE/functions/views etc. carry no schema structure

    surviving = {relpath: [name for name in names if name in tables and origin.get(name, ("",))[0] == relpath] for relpath, names in file_created.items()}
    for name, table in tables.items():
        relpath, line = origin[name]
        columns = list(table["columns"].values())
        for column in columns:
            if column["name"] in table["pk"]:
                column["pk"] = True
        foreign_keys = [
            {"columns": fk["columns"], "table": fk["table"], "ref_columns": fk["ref_columns"]} for fk in table["fks"]
        ]
        summary = f"{len(columns)} columns; PK ({', '.join(table['pk']) or 'none'})"
        if foreign_keys:
            summary += "; FK -> " + ", ".join(sorted({fk["table"] for fk in foreign_keys}))
        node = GraphNode(
            f"table:{name}", "table", name, name, relpath, line, line, "sql", summary,
            attrs={"columns": columns[:200], "primary_key": table["pk"], "foreign_keys": foreign_keys, "indexes": table["indexes"][:40], "migrations": table["migrations"]},
        )
        graph.add_node(node)
    for relpath, names in surviving.items():
        for name in names:
            graph.add_edge(relpath, f"table:{name}", "defines", EXTRACTED, "syntax", "CREATE TABLE")
    for name, table in tables.items():
        for fk in table["fks"]:
            target = f"table:{fk['table']}"
            detail = f"{name}({', '.join(fk['columns'])}) -> {fk['table']}({', '.join(fk['ref_columns']) or '?'})"
            if target not in graph.nodes:
                graph.ensure_external(fk["table"])
                target = f"external:{fk['table']}"
            graph.add_edge(f"table:{name}", target, "references", EXTRACTED, "syntax", detail)
    return {"tables": len(tables), "statements_understood": understood, "statements_ignored": ignored}


def _snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _link_entities_to_tables(graph: Graph) -> int:
    tables = {node.name for node in graph.nodes.values() if node.kind == "table"}
    linked = 0
    for node in list(graph.nodes.values()):
        if node.kind != "class" or "Entity" not in node.attrs.get("annotations", []):
            continue
        explicit = node.attrs.get("table_name")
        table_name = (explicit or _snake_case(node.name)).lower()
        candidates = [table_name, table_name + "s", table_name + "es"] if not explicit else [table_name]
        for candidate in candidates:
            if candidate in tables:
                graph.add_edge(
                    node.id, f"table:{candidate}", "maps_to", EXTRACTED if explicit else INFERRED,
                    "syntax" if explicit else "graph-traversal",
                    f"@Table(name = \"{explicit}\")" if explicit else f"entity {node.name} -> table {candidate} by naming convention",
                )
                linked += 1
                break
    return linked


# --------------------------------------------------------------------------
# Generic cross-file resolution (types, imports, typed calls)
# --------------------------------------------------------------------------


def _stem(relpath: str) -> str:
    return re.sub(r"\.[A-Za-z0-9]+$", "", relpath)


def _resolve_generic(graph: Graph, generic_facts: list[FileFacts]) -> None:
    modules = {node.id: node for node in graph.nodes.values() if node.kind == "module"}
    suffix_index: dict[str, list[str]] = {}
    for module_id in modules:
        parts = _stem(module_id).split("/")
        for start in range(len(parts)):
            suffix_index.setdefault("/".join(parts[start:]), []).append(module_id)
    class_index: dict[str, list[str]] = {}
    class_methods: dict[str, dict[str, str]] = {}
    for node in graph.nodes.values():
        if node.kind in ("class", "interface"):
            class_index.setdefault(node.name, []).append(node.id)
        if node.kind == "method":
            class_methods.setdefault(node.id.rsplit("::", 1)[0], {})[node.name] = node.id
    function_index: dict[str, list[str]] = {}
    for node in graph.nodes.values():
        if node.kind == "function":
            function_index.setdefault(node.name, []).append(node.id)

    def import_target(raw: str, facts: FileFacts) -> str | None:
        cleaned = raw.strip().rstrip("*").rstrip(".:/")
        if not cleaned:
            return None
        path_like = "/" in cleaned or cleaned.startswith(".")
        if path_like:
            base = Path(facts.module_relpath).parent
            joined = os.path.normpath((base / cleaned).as_posix()).replace(os.sep, "/") if cleaned.startswith(".") else cleaned
            stub = _stem(joined)
            candidates = [stub] if cleaned.startswith(".") else [stub, stub.split("/", 1)[-1]]
        else:
            parts = [p for p in re.split(r"\.|::|\\\\", cleaned) if p and p != "*"]
            candidates = ["/".join(parts[: len(parts) - drop]) for drop in (0, 1, 2) if len(parts) - drop >= 1]
        for stub in candidates:
            hits = suffix_index.get(stub, [])
            hits = [hit for hit in hits if hit != facts.module_id]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                same_language = [hit for hit in hits if modules[hit].language == facts.language]
                if len(same_language) == 1:
                    return same_language[0]
        return None

    def resolve_type(name: str, facts: FileFacts) -> tuple[str, str] | None:
        if name in facts.imported_names:
            target_module = import_target(facts.imported_names[name][0], facts)
            if target_module and f"{target_module}::{name}" in graph.nodes:
                return f"{target_module}::{name}", "resolved via import"
        local = f"{facts.module_id}::{name}"
        if local in graph.nodes:
            return local, "same-file type"
        candidates = class_index.get(name, [])
        if len(candidates) == 1:
            return candidates[0], "unique type name"
        if len(candidates) > 1:
            here = str(Path(facts.module_relpath).parent)
            nearby = [c for c in candidates if str(Path(c.split("::")[0]).parent) == here]
            if len(nearby) == 1:
                return nearby[0], "same package / directory"
            same_language = [c for c in candidates if graph.nodes[c].language == facts.language]
            if len(same_language) == 1:
                return same_language[0], "unique type name in this language"
        return None

    bases: dict[str, list[str]] = {}
    file_of = {facts.module_id: facts for facts in generic_facts}

    for facts in generic_facts:
        for raw in facts.raw_module_imports:
            target = import_target(raw, facts)
            if target:
                graph.add_edge(facts.module_id, target, "imports", INFERRED, "graph-traversal", f"resolved '{raw}' to {target}")
        for source_id, type_name, edge_type, detail in facts.pending_relations:
            resolved = resolve_type(type_name, facts)
            if resolved and resolved[0] != source_id:
                graph.add_edge(source_id, resolved[0], edge_type, INFERRED, "graph-traversal", f"{detail} ({resolved[1]})")
                if edge_type in ("inherits", "implements"):
                    bases.setdefault(source_id, []).append(resolved[0])

    def find_method(class_id: str, name: str, seen: set[str] | None = None) -> str | None:
        seen = seen or set()
        if class_id in seen:
            return None
        seen.add(class_id)
        direct = class_methods.get(class_id, {}).get(name)
        if direct:
            return direct
        for base in bases.get(class_id, []):
            found = find_method(base, name, seen)
            if found:
                return found
        return None

    for facts in generic_facts:
        for caller_id, name, qualifier, class_id in facts.pending_calls:
            target = reason = None
            if qualifier in (None, "this", "self", "super", "base") and class_id:
                found = find_method(class_id, name)
                if found and found != caller_id:
                    target, reason = found, "method on the enclosing class or its supertypes"
            if target is None and qualifier and qualifier not in ("this", "self", "super", "base"):
                type_name = facts.scope_types.get(caller_id, {}).get(qualifier) or (facts.class_field_types.get(class_id, {}).get(qualifier) if class_id else None)
                if type_name:
                    resolved = resolve_type(type_name, facts)
                    if resolved:
                        found = find_method(resolved[0], name)
                        if found:
                            target, reason = found, f"'{qualifier}' is a {type_name} ({resolved[1]})"
                else:
                    resolved = resolve_type(qualifier, facts)  # static-style call on a type name
                    if resolved:
                        found = find_method(resolved[0], name)
                        if found:
                            target, reason = found, f"static call on {qualifier} ({resolved[1]})"
            if target is None and qualifier is None:
                same_module = f"{facts.module_id}::{name}"
                if same_module in graph.nodes and graph.nodes[same_module].kind == "function":
                    target, reason = same_module, "same-module function"
                elif name in facts.imported_names:
                    module = import_target(facts.imported_names[name][0], facts)
                    if module and f"{module}::{name}" in graph.nodes:
                        target, reason = f"{module}::{name}", "resolved via import"
            if target is None and qualifier is None and name not in _BUILTIN_NAMES:
                candidates = function_index.get(name, [])
                if len(candidates) == 1:
                    target, reason = candidates[0], "unique function name across the graph"
            if target is None and name in class_index and qualifier is None:
                resolved = resolve_type(name, facts)
                if resolved:
                    target, reason = resolved[0], f"constructor / type reference ({resolved[1]})"
            if target:
                graph.add_edge(caller_id, target, "calls", INFERRED, "graph-traversal", reason or "")


def build_graph(root: Path, languages: list[str], semantic: bool = False) -> tuple[Graph, dict[str, Any]]:
    graph = Graph()
    file_facts: list[FileFacts] = []
    generic_facts: list[FileFacts] = []
    sql_files: list[tuple[Path, str]] = []
    table = language_extensions()
    auto = not languages or "auto" in languages or "all" in languages
    requested = sorted(table) if auto else list(languages)
    missing_packages: dict[str, str] = {}

    for path, language in discover_source_files(root, requested):
        stats = graph.stats.setdefault(language, {"files": 0, "mode": "ast"})
        stats["files"] += 1
        relpath = path.relative_to(root).as_posix()
        if language == "sql":
            sql_files.append((path, relpath))
            stats["mode"] = "schema"
            continue
        try:
            source = path.read_bytes()
        except OSError:
            continue
        if language == "python":
            file_facts.append(parse_python_file(path, root, source, graph))
        elif language in ("javascript", "typescript"):
            file_facts.append(parse_js_like_file(path, root, source, graph, language))
        else:
            try:
                generic_facts.append(parse_generic_file(path, root, source, graph, language))
            except GraphDependencyError as exc:
                if not auto or str(exc).startswith("tree-sitter is not installed"):
                    raise
                stats["mode"] = "files-only"
                missing_packages[language] = _LANGUAGE_SPECS[language]["package"]
                graph.add_node(GraphNode(relpath, "module", path.stem, relpath, relpath, 1, source.count(b"\n") + 1, language))

    if sql_files:
        sql_stats = parse_sql_migrations(sql_files, graph)
        graph.stats["sql"].update(sql_stats)

    _resolve_graph(graph, file_facts)
    _resolve_generic(graph, generic_facts)
    linked = _link_entities_to_tables(graph)
    if linked:
        graph.stats.setdefault("sql", {})["entities_linked"] = linked

    for language, stats in graph.stats.items():
        stats["symbols"] = sum(1 for node in graph.nodes.values() if node.language == language and node.kind not in ("module", "external"))
    for language, package in sorted(missing_packages.items()):
        graph.notes.append(
            f"{language}: {graph.stats[language]['files']} file(s) added as file nodes only (no grammar installed); "
            f"run `pip install {package}` (in the graph venv) and rebuild for symbols"
        )
    if "sql" in graph.stats:
        graph.notes.append(
            "sql: the schema was rebuilt by replaying migrations in Flyway version order with a lightweight parser; "
            "table/column facts are EXTRACTED from DDL, but unusual DDL (functions, vendor extensions) is skipped"
        )

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


def schema_report(graph_path: Path, table: str | None = None) -> dict[str, Any]:
    graph_data = load_graph(graph_path)
    nodes = {node["id"]: node for node in graph_data["nodes"]}
    tables = {node["id"]: node for node in graph_data["nodes"] if node["kind"] == "table"}
    if not tables:
        return {"ok": False, "error": "no_tables"}
    entities: dict[str, list[str]] = {}
    referenced_by: dict[str, list[dict[str, Any]]] = {}
    for edge in graph_data["edges"]:
        if edge["type"] == "maps_to" and edge["target"] in tables:
            entities.setdefault(edge["target"], []).append(nodes[edge["source"]]["name"])
        if edge["type"] == "references" and edge["source"] in tables and edge["target"] in tables:
            referenced_by.setdefault(edge["target"], []).append({"table": nodes[edge["source"]]["name"], "detail": edge.get("detail", "")})
    selected = list(tables.values())
    if table:
        wanted = table.lower()
        selected = [node for node in selected if node["name"] == wanted]
        if not selected:
            return {"ok": False, "error": "table_not_found", "tables": sorted(node["name"] for node in tables.values())}
    report = []
    for node in sorted(selected, key=lambda item: item["name"]):
        attrs = node.get("attrs", {})
        report.append(
            {
                "name": node["name"],
                "columns": attrs.get("columns", []),
                "primary_key": attrs.get("primary_key", []),
                "foreign_keys": attrs.get("foreign_keys", []),
                "indexes": attrs.get("indexes", []),
                "migrations": attrs.get("migrations", []),
                "entities": sorted(entities.get(node["id"], [])),
                "referenced_by": referenced_by.get(node["id"], []),
            }
        )
    return {"ok": True, "tables": report, "table_count": len(tables)}


def schema_mermaid(report: dict[str, Any]) -> str:
    def ident(text: str) -> str:
        return re.sub(r"\W+", "_", text).strip("_") or "x"

    lines = ["erDiagram"]
    shown = {table["name"] for table in report["tables"]}
    for table in report["tables"]:
        lines.append(f"  {ident(table['name'])} {{")
        foreign = {column for fk in table["foreign_keys"] for column in fk["columns"]}
        for column in table["columns"]:
            key = " PK" if column.get("pk") else (" FK" if column["name"] in foreign else "")
            lines.append(f"    {ident(column['type'])} {ident(column['name'])}{key}")
        lines.append("  }")
    for table in report["tables"]:
        for fk in table["foreign_keys"]:
            if fk["table"] in shown:
                lines.append(f"  {ident(table['name'])} }}o--|| {ident(fk['table'])} : \"{', '.join(fk['columns'])}\"")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Listing everything (`omni graph show --all`)
# --------------------------------------------------------------------------


def _degree_maps(graph_data: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    indegree: dict[str, int] = {}
    outdegree: dict[str, int] = {}
    for edge in graph_data["edges"]:
        outdegree[edge["source"]] = outdegree.get(edge["source"], 0) + 1
        indegree[edge["target"]] = indegree.get(edge["target"], 0) + 1
    return indegree, outdegree


def _file_filter(pattern: str | None):
    if not pattern:
        return lambda file_path: True
    if any(char in pattern for char in "*?["):
        return lambda file_path: fnmatch.fnmatch(file_path or "", pattern)
    return lambda file_path: pattern in (file_path or "")


def list_nodes(
    graph_path: Path,
    kind: str | None = None,
    language: str | None = None,
    file_pattern: str | None = None,
    include_external: bool = False,
    include_edges: bool = False,
    sort: str = "file",
    limit: int = 0,
) -> dict[str, Any]:
    graph_data = load_graph(graph_path)
    indegree, outdegree = _degree_maps(graph_data)
    file_matches = _file_filter(file_pattern)

    rows = []
    for node in graph_data["nodes"]:
        if node["kind"] == "external" and not include_external and kind != "external":
            continue
        if kind and node["kind"] != kind:
            continue
        if language and node.get("language") != language:
            continue
        if not file_matches(node.get("file")):
            continue
        rows.append(
            {
                "id": node["id"],
                "kind": node["kind"],
                "name": node["name"],
                "file": node.get("file"),
                "language": node.get("language"),
                "start_line": node.get("start_line"),
                "end_line": node.get("end_line"),
                "in": indegree.get(node["id"], 0),
                "out": outdegree.get(node["id"], 0),
            }
        )

    if sort == "degree":
        rows.sort(key=lambda row: (-(row["in"] + row["out"]), row["id"]))
    elif sort == "name":
        rows.sort(key=lambda row: (row["name"].lower(), row["id"]))
    else:
        rows.sort(key=lambda row: (row["file"] or "~", row["start_line"] or 0, row["name"]))
    matched = len(rows)
    if limit and limit > 0:
        rows = rows[:limit]

    result: dict[str, Any] = {
        "ok": True,
        "root": graph_data.get("root"),
        "generated_at": graph_data.get("generated_at"),
        "nodes_total": len(graph_data["nodes"]),
        "edges_total": len(graph_data["edges"]),
        "nodes_matched": matched,
        "nodes": rows,
        "node_kinds": _count_by(graph_data["nodes"], "kind"),
        "edge_types": _count_by(graph_data["edges"], "type"),
        "externals_hidden": (
            0
            if include_external or kind == "external"
            else sum(1 for node in graph_data["nodes"] if node["kind"] == "external")
        ),
    }
    if include_edges:
        keep = {row["id"] for row in rows}
        result["edges"] = [
            edge for edge in graph_data["edges"] if edge["source"] in keep and edge["target"] in keep
        ]
    return result


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[str(item.get(key))] = counts.get(str(item.get(key)), 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))


# --------------------------------------------------------------------------
# Interactive 3D view (`omni graph view`): one self-contained HTML file.
# The 3D engine is the unmodified, vendored 3d-force-graph bundle (MIT, plus
# the permissively licensed packages it contains -- see
# .ai/graph-viewer/THIRD_PARTY_NOTICES.md). Nothing is fetched at view time,
# so the page works offline / on an isolated network.
# --------------------------------------------------------------------------

VIEW_DEFAULT_OUTPUT = ".ai/project-graph.html"
VIEW_DEFAULT_MAX_INITIAL = 500
VIEWER_DIR_PARTS = (".ai", "graph-viewer")
VIEWER_ASSETS = ("viewer.html", "3d-force-graph.min.js", "THIRD_PARTY_NOTICES.md")
_VIEW_SUMMARY_LIMIT = 400
_VIEW_DETAIL_LIMIT = 140


def _viewer_asset_path(name: str) -> Path | None:
    for base in (Path(__file__).resolve().parent, Path.cwd()):
        candidate = base.joinpath(*VIEWER_DIR_PARTS, name)
        if candidate.is_file():
            return candidate
    return None


def _trim(text: Any, limit: int) -> str | None:
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_view_html(
    graph_path: Path,
    include_external: bool = False,
    max_initial: int = VIEW_DEFAULT_MAX_INITIAL,
    start_all: bool = False,
    focus: str | None = None,
    depth: int = 2,
) -> dict[str, Any]:
    missing = [name for name in VIEWER_ASSETS if _viewer_asset_path(name) is None]
    if missing:
        return {"ok": False, "error": "missing_assets", "missing": missing}

    template = _viewer_asset_path("viewer.html").read_text(encoding="utf-8")
    library = _viewer_asset_path("3d-force-graph.min.js").read_text(encoding="utf-8")
    notices = _viewer_asset_path("THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    graph_data = load_graph(graph_path)
    all_nodes = {node["id"]: node for node in graph_data["nodes"]}
    edges = graph_data["edges"]
    indegree, outdegree = _degree_maps(graph_data)

    def degree(node_id: str) -> int:
        return indegree.get(node_id, 0) + outdegree.get(node_id, 0)

    def allowed(node_id: str) -> bool:
        return include_external or all_nodes[node_id]["kind"] != "external"

    note = ""
    if focus:
        matches = find_nodes(graph_data, focus)
        if not matches:
            return {"ok": False, "error": "focus_not_found"}
        initial_ids = {node_id for node_id in _ego_network(edges, [n["id"] for n in matches], depth) if allowed(node_id)}
        note = f"focused on {focus} ({depth} hop{'s' if depth != 1 else ''})"
    else:
        candidates = [node_id for node_id in all_nodes if allowed(node_id)]
        if start_all or len(candidates) <= max_initial:
            initial_ids = set(candidates)
        else:
            candidates.sort(key=lambda node_id: (-degree(node_id), node_id))
            initial_ids = set(candidates[:max_initial])
            note = f"top {max_initial} by connections; double-click a node to expand it"

    compact_nodes = []
    for node in graph_data["nodes"]:
        entry = {
            "id": node["id"],
            "name": node["name"],
            "qn": node.get("qualified_name"),
            "kind": node["kind"],
            "file": node.get("file"),
            "start": node.get("start_line"),
            "end": node.get("end_line"),
            "lang": node.get("language"),
            "summary": _trim(node.get("summary"), _VIEW_SUMMARY_LIMIT),
            "attrs": node.get("attrs"),
        }
        compact_nodes.append({key: value for key, value in entry.items() if value not in (None, "")})
    compact_edges = [
        {
            "s": edge["source"],
            "t": edge["target"],
            "type": edge["type"],
            "prov": edge["provenance"],
            "detail": _trim(edge.get("detail"), _VIEW_DETAIL_LIMIT) or "",
        }
        for edge in edges
    ]

    raw_root = str(graph_data.get("root") or "").strip()
    root_label = Path(raw_root).resolve().name if raw_root in ("", ".") else raw_root
    generated = str(graph_data.get("generated_at") or "")[:10]
    payload = {
        "nodes": compact_nodes,
        "edges": compact_edges,
        "initial": sorted(initial_ids),
        "meta": {"root": root_label, "generated_at": generated, "note": note},
    }
    data_json = (
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )

    values = {
        "__OMNI_TITLE__": html.escape(f"{root_label} \u00b7 OmniEngineering CodeGraph"),
        "__OMNI_NOTICES__": html.escape(notices),
        "/*__OMNI_DATA__*/": data_json,
        "/*__OMNI_LIB__*/": library,
    }
    pattern = re.compile("|".join(re.escape(key) for key in values))
    page = pattern.sub(lambda match: values[match.group(0)], template)

    return {
        "ok": True,
        "html": page,
        "nodes_total": len(all_nodes),
        "edges_total": len(edges),
        "nodes_initial": len(initial_ids),
        "note": note,
    }


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
_KIND_RADIUS = {"module": 14, "class": 10, "interface": 10, "table": 12, "function": 6, "method": 6, "external": 3}
_SVG_BG = "#0f0f12"
_SVG_TEXT = "#e8e8e8"
_SVG_DIM = "#707070"
_SVG_FAINT = "#2a2a2e"
_SVG_FONT = "'Share Tech Mono','JetBrains Mono','Courier New',monospace"


def _escape_svg_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _node_color(node: dict[str, Any]) -> str:
    language = node.get("language")
    if language in _LANGUAGE_COLORS or not language:
        return _LANGUAGE_COLORS.get(language, _DEFAULT_NODE_COLOR)
    hue = sum(ord(char) * (index + 1) for index, char in enumerate(language)) % 360
    return f"hsl({hue},55%,62%)"


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
