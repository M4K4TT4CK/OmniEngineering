"""Tree-sitter layer.

This is what makes "polyglot" real rather than aspirational: function
boundaries, nesting depth and branch complexity are read from a parse tree
instead of guessed from indentation, and the same code works for every
grammar in the pack.

The whole module degrades cleanly. If tree-sitter is not installed,
`available()` is False and the probes that depend on it are skipped with that
reason recorded — never counted as a pass.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

# Arbiter language name -> tree-sitter-language-pack name
LANG_MAP = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "go": "go",
    "java": "java",
    "kotlin": "kotlin",
    "c": "c",
    "cpp": "cpp",
    "csharp": "csharp",
    "ruby": "ruby",
    "rust": "rust",
    "php": "php",
    "r": "r",
    "shell": "bash",
    "terraform": "hcl",
    "hcl": "hcl",
    "yaml": "yaml",
    "json": "json",
    "sql": "sql",
}

# Node types that represent a branch. Deliberately broad: grammars disagree on
# naming, and over-counting a branch is a far cheaper error than silently
# reporting complexity 1 for a language whose node names we did not anticipate.
BRANCH_TYPES = {
    "if_statement", "elif_clause", "else_if_clause", "if_expression",
    "for_statement", "for_in_statement", "for_range_loop", "foreach_statement",
    "while_statement", "do_statement", "repeat_statement",
    "switch_statement", "switch_expression", "case_statement", "case_clause",
    "switch_case", "when_clause", "match_statement", "match_arm", "case",
    "catch_clause", "except_clause", "rescue", "try_statement",
    "conditional_expression", "ternary_expression",
    "and", "or", "boolean_operator", "logical_expression",
}

# Types that open a nesting level. Control flow only: counting every `block`
# node makes trivial Python look nine levels deep, which is noise, not signal.
NEST_TYPES = {
    "if_statement", "if_expression", "for_statement", "for_in_statement",
    "for_range_loop", "foreach_statement", "while_statement", "do_statement",
    "switch_statement", "switch_expression", "try_statement", "with_statement",
    "match_statement", "case_statement",
}

FUNCTION_QUERIES: dict[str, str] = {
    "python": "(function_definition name: (identifier) @name) @fn",
    "javascript": """
        [(function_declaration name: (identifier) @name)
         (method_definition name: (property_identifier) @name)
         (function_expression name: (identifier) @name)] @fn
    """,
    "typescript": """
        [(function_declaration name: (identifier) @name)
         (method_definition name: (property_identifier) @name)] @fn
    """,
    "go": """
        [(function_declaration name: (identifier) @name)
         (method_declaration name: (field_identifier) @name)] @fn
    """,
    "java": "(method_declaration name: (identifier) @name) @fn",
    "ruby": "(method name: (identifier) @name) @fn",
    "rust": "(function_item name: (identifier) @name) @fn",
    "php": "(function_definition name: (name) @name) @fn",
    "c": "(function_definition declarator: (function_declarator declarator: (identifier) @name)) @fn",
    "cpp": "(function_definition declarator: (function_declarator declarator: (identifier) @name)) @fn",
    "bash": "(function_definition name: (word) @name) @fn",
}


@dataclass
class FunctionInfo:
    name: str
    start_line: int
    end_line: int
    lines: int
    complexity: int
    max_depth: int
    params: int = 0


@functools.lru_cache(maxsize=1)
def available() -> bool:
    try:
        import tree_sitter_language_pack  # noqa: F401 - imported only to prove it is installed
        return True
    except ImportError:
        return False


def unavailable_reason() -> str:
    return "tree-sitter not installed (pip install 'arbiter-eval[ast]')"


@functools.lru_cache(maxsize=64)
def _parser(ts_lang: str):
    from tree_sitter_language_pack import get_parser
    return get_parser(ts_lang)


@functools.lru_cache(maxsize=64)
def _language(ts_lang: str):
    from tree_sitter_language_pack import get_language
    return get_language(ts_lang)


def ts_name(language: str) -> str | None:
    return LANG_MAP.get(language)


def parse_file(path: str, language: str):
    """Return a tree-sitter tree, or None when the language is unsupported."""
    ts = ts_name(language)
    if not ts or not available():
        return None
    try:
        data = Path(path).read_bytes()
        return _parser(ts).parse(data), data
    except Exception:  # noqa: BLE001 - an unreadable or unparseable file is "no tree", not a crash mid-scan
        return None


def run_query(language: str, query_src: str, tree_and_src) -> list[dict]:
    """Run a tree-sitter query. Returns one dict per capture."""
    ts = ts_name(language)
    if not ts or tree_and_src is None:
        return []
    tree, src = tree_and_src
    lang = _language(ts)
    # tree-sitter moved queries around between 0.21 and 0.26. Support both:
    # new  -> Query(language, source) + QueryCursor(query).captures(node)
    # old  -> language.query(source).captures(node)
    try:
        import tree_sitter as _ts
        if hasattr(_ts, "Query") and not hasattr(lang, "query"):
            query = _ts.Query(lang, query_src)
            runner = _ts.QueryCursor(query)
        else:
            query = lang.query(query_src)
            runner = query
    except Exception as exc:
        raise ValueError(f"invalid tree-sitter query for {language}: {exc}") from exc

    out: list[dict] = []
    try:
        captures = runner.captures(tree.root_node)
    except Exception:  # noqa: BLE001 - a query the grammar rejects at match time yields no captures
        return []
    # tree-sitter's Python binding returns {name: [nodes]} on modern versions
    # and [(node, name)] on older ones. Support both.
    if isinstance(captures, dict):
        pairs = [(node, name) for name, nodes in captures.items() for node in nodes]
    else:
        pairs = list(captures)
    for node, name in pairs:
        out.append({
            "capture": name,
            "type": node.type,
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "text": src[node.start_byte:node.end_byte].decode("utf-8", "replace"),
            "node": node,
        })
    return out


def _count_branches(node) -> int:
    total = 0
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in BRANCH_TYPES:
            total += 1
        stack.extend(n.children)
    return total


def _max_depth(node, _depth: int = 0) -> int:
    best = _depth
    for child in node.children:
        d = _max_depth(child, _depth + (1 if child.type in NEST_TYPES else 0))
        if d > best:
            best = d
    return best


def functions(path: str, language: str) -> list[FunctionInfo]:
    """Every function in the file, with real boundaries and metrics."""
    ts = ts_name(language)
    if not ts or ts not in FUNCTION_QUERIES or not available():
        return []
    parsed = parse_file(path, language)
    if parsed is None:
        return []
    caps = run_query(language, FUNCTION_QUERIES[ts], parsed)

    fns: dict[int, dict] = {}
    for c in caps:
        node = c["node"]
        if c["capture"] == "fn":
            fns.setdefault(node.start_byte, {})["node"] = node
        elif c["capture"] == "name":
            # attach to the nearest enclosing captured function
            parent = node
            while parent is not None and parent.start_byte not in fns:
                parent = parent.parent
            if parent is not None:
                fns[parent.start_byte]["name"] = c["text"]

    out: list[FunctionInfo] = []
    for entry in fns.values():
        node = entry.get("node")
        if node is None:
            continue
        start = node.start_point[0] + 1
        end = node.end_point[0] + 1
        out.append(FunctionInfo(
            name=entry.get("name", "<anonymous>"),
            start_line=start,
            end_line=end,
            lines=end - start + 1,
            complexity=_count_branches(node) + 1,
            max_depth=_max_depth(node),
        ))
    return sorted(out, key=lambda f: f.start_line)


def supported_languages() -> list[str]:
    if not available():
        return []
    return sorted(k for k, v in LANG_MAP.items() if v in FUNCTION_QUERIES)
