#!/usr/bin/env python3
"""Mutation variants: does a rule still fire when the defect is written differently?

    python tools/variants.py                         # every language, every transform
    python tools/variants.py python go --kinds rename,hoist
    python tools/variants.py --json out.json --markdown out.md --keep /tmp/variants

The planted corpus (fixtures/planted/<lang>/bad) states each defect once, in
the shape its author thought of. Code a model writes states the same defect
in whatever shape it happens to produce: the query built in a helper, the
variable named `q` rather than `query`, an f-string where the fixture used
`%`. A rule that only matches the fixture's spelling has a recall on real
code well below the 100% the planted score reports.

This rewrites each planted bad file with one semantics-preserving transform
at a time, rescans the tree, and records, for every `expect:` marker the
transform touched, whether its rule still reports it:

  rename     local variables and parameters get neutral names (v1, v2, ...).
             A rule keyed on a name (`password`, `token`) is expected to
             lose findings here; that is a fact about the rule worth knowing,
             not always a defect.
  hoist      the first non-trivial argument of the marked statement's
             outermost call is moved into a temporary on the line before.
  helper     the same argument is passed through an identity function
             defined in the file (`_same(x)`): the value now crosses a call.
  move       the function holding the marker moves to the end of its
             module or class.
  strings    string building changes style on the marked line: f-string /
             %-format / .format / concatenation (Python), template literal /
             concatenation (TypeScript), fmt.Sprintf("%s") / concatenation
             (Go), String.format("%s") / concatenation (Java), interpolation
             / concatenation (Ruby).

Each marker line is tracked through the rewrite (markers are comments on the
line, so they travel with it). A variant file that no longer parses is
discarded, never scored. Robustness per rule is kept / applicable over every
(marker, transform) pair the transform changed; markers the original scan
missed are not scored at all.

Variants are written to a temporary directory and scanned there. Nothing is
built or run, and the fixtures are not modified.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arbiter import planted  # noqa: E402

KINDS = ("rename", "hoist", "helper", "move", "strings")
EXT_LANG = {".py": "python", ".ts": "typescript", ".js": "javascript", ".mjs": "javascript",
            ".cjs": "javascript", ".go": "go", ".java": "java", ".rb": "ruby"}
GRAMMAR = {"python": "python", "typescript": "typescript", "javascript": "javascript",
           "go": "go", "java": "java", "ruby": "ruby"}


@dataclass
class Rewrite:
    """One file after one transform: new text, where each original line went,
    and which original lines the transform touched."""
    text: str
    line_map: dict[int, int]
    affected: set[int] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse(text: str, lang: str):
    from arbiter import ast as ts
    return ts._parser(GRAMMAR[lang]).parse(text.encode("utf-8"))


def parses_clean(text: str, lang: str) -> bool:
    return not parse(text, lang).root_node.has_error


def walk(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def field_of(node) -> str | None:
    p = node.parent
    if p is None:
        return None
    for i, c in enumerate(p.children):
        if c.id == node.id:
            return p.field_name_for_child(i)
    return None


def identity_map(text: str) -> dict[int, int]:
    return {i: i for i in range(1, text.count("\n") + 2)}


def apply_edits(src: bytes, edits: list[tuple[int, int, bytes]]) -> bytes:
    """Non-overlapping (start, end, replacement) byte edits; later edits that
    overlap an earlier one are dropped."""
    out, pos = [], 0
    for s, e, rep in sorted(edits):
        if s < pos:
            continue
        out.append(src[pos:s])
        out.append(rep)
        pos = e
    out.append(src[pos:])
    return b"".join(out)


FUNCTION_NODES = {
    "python": {"function_definition"},
    "typescript": {"function_declaration", "method_definition", "arrow_function", "function_expression"},
    "javascript": {"function_declaration", "method_definition", "arrow_function", "function_expression"},
    "go": {"function_declaration", "method_declaration", "func_literal"},
    "java": {"method_declaration", "constructor_declaration"},
    "ruby": {"method", "singleton_method"},
}


def enclosing(node, types: set[str]):
    n = node
    while n is not None and n.type not in types:
        n = n.parent
    return n


def node_at_line(tree, line: int):
    """The first named node that starts on `line` (1-based) and is a statement-ish child."""
    best = None
    for n in walk(tree.root_node):
        if n.start_point[0] + 1 == line and n.is_named:
            if best is None:
                best = n
    return best


# ---------------------------------------------------------------------------
# rename: local variables and parameters get neutral names
# ---------------------------------------------------------------------------

_KEEP = {"self", "cls", "this", "_", "err", "ctx", "args", "kwargs"}


def _declared_names(fn, lang: str) -> set[str]:
    names: set[str] = set()
    for n in walk(fn):
        if lang == "python":
            if n.type == "identifier" and n.parent is not None and (
                    n.parent.type == "parameters"
                    or (n.parent.type in ("default_parameter", "typed_parameter", "typed_default_parameter")
                        and field_of(n) in ("name", None) and n.parent.children[0].id == n.id)
                    or (n.parent.type == "assignment" and field_of(n) == "left")
                    or (n.parent.type == "for_statement" and field_of(n) == "left")):
                names.add(n.text.decode())
        elif lang in ("typescript", "javascript"):
            if n.type == "identifier" and n.parent is not None and (
                    (n.parent.type == "variable_declarator" and field_of(n) == "name")
                    or (n.parent.type in ("required_parameter", "optional_parameter") and field_of(n) == "pattern")
                    or n.parent.type == "formal_parameters"):
                names.add(n.text.decode())
        elif lang == "go":
            if n.type == "identifier" and n.parent is not None and (
                    (n.parent.type == "parameter_declaration" and field_of(n) == "name")
                    or (n.parent.type == "var_spec" and field_of(n) == "name")
                    or (n.parent.type == "expression_list" and n.parent.parent is not None
                        and n.parent.parent.type in ("short_var_declaration", "range_clause")
                        and field_of(n.parent) == "left")):
                names.add(n.text.decode())
        elif lang == "java":
            if n.type == "identifier" and n.parent is not None and field_of(n) == "name" and \
                    n.parent.type in ("variable_declarator", "formal_parameter") and \
                    enclosing(n.parent, {"field_declaration"}) is None:
                names.add(n.text.decode())
        elif lang == "ruby":
            if n.type == "identifier" and n.parent is not None and (
                    n.parent.type == "method_parameters"
                    or (n.parent.type in ("optional_parameter", "keyword_parameter") and field_of(n) == "name")
                    or (n.parent.type == "assignment" and field_of(n) == "left")):
                if not (n.parent.type == "keyword_parameter"):  # renaming a keyword changes the call contract
                    names.add(n.text.decode())
    return {x for x in names if x not in _KEEP}


def _blocked_names(fn, lang: str) -> set[str]:
    """Names that also appear somewhere a rename would change meaning."""
    out: set[str] = set()
    for n in walk(fn):
        t = n.type
        if t in ("shorthand_property_identifier", "shorthand_property_identifier_pattern"):
            out.add(n.text.decode())
        if lang == "python" and t in ("global_statement", "nonlocal_statement"):
            out |= {c.text.decode() for c in n.children if c.type == "identifier"}
        if lang == "ruby" and t in ("hash_key_symbol",) and n.text.decode().endswith(":"):
            out.add(n.text.decode().rstrip(":"))
    return out


def _is_reference(n, lang: str) -> bool:
    """An identifier occurrence that names the variable (not a field, key or keyword)."""
    if n.type != "identifier":
        return False
    f = field_of(n)
    p = n.parent
    if lang == "python":
        if p.type == "attribute" and f == "attribute":
            return False
        if p.type == "keyword_argument" and f == "name":
            return False
    if lang == "go" and p.type == "literal_element" and p.parent is not None and field_of(p) == "key":
        return False
    if lang == "java":
        if p.type == "method_invocation" and f == "name":
            return False
        if p.type == "field_access" and f == "field":
            return False
    if lang == "ruby" and p.type == "call" and f == "method":
        return False
    if lang in ("typescript", "javascript") and p.type in ("pair", "pair_pattern") and f == "key":
        return False
    return True


def transform_rename(text: str, lang: str) -> Rewrite | None:
    tree = parse(text, lang)
    src = text.encode()
    edits, affected = [], set()
    counter = [0]
    used = {n.text.decode() for n in walk(tree.root_node) if n.type == "identifier"}
    for fn in walk(tree.root_node):
        if fn.type not in FUNCTION_NODES[lang]:
            continue
        if enclosing(fn.parent, FUNCTION_NODES[lang]) is not None if fn.parent else False:
            continue  # nested: handled with its outer function
        names = _declared_names(fn, lang) - _blocked_names(fn, lang)
        mapping = {}
        for name in sorted(names):
            counter[0] += 1
            new = f"v{counter[0]}"
            while new in used:
                counter[0] += 1
                new = f"v{counter[0]}"
            used.add(new)
            mapping[name] = new
        for n in walk(fn):
            if n.type == "identifier" and n.text.decode() in mapping and _is_reference(n, lang):
                edits.append((n.start_byte, n.end_byte, mapping[n.text.decode()].encode()))
        if mapping:
            affected |= set(range(fn.start_point[0] + 1, fn.end_point[0] + 2))
    if not edits:
        return None
    return Rewrite(apply_edits(src, edits).decode(), identity_map(text), affected)


# ---------------------------------------------------------------------------
# hoist / helper: the marked statement's main argument moves
# ---------------------------------------------------------------------------

CALL_NODES = {"python": "call", "typescript": "call_expression", "javascript": "call_expression",
              "go": "call_expression", "java": "method_invocation", "ruby": "call"}
ARGS_FIELD = {"python": "arguments", "typescript": "arguments", "javascript": "arguments",
              "go": "arguments", "java": "arguments", "ruby": "arguments"}
STATEMENTS = {
    "python": {"expression_statement", "return_statement", "call", "assignment"},
    "typescript": {"expression_statement", "return_statement", "lexical_declaration", "variable_declaration"},
    "javascript": {"expression_statement", "return_statement", "lexical_declaration", "variable_declaration"},
    "go": {"expression_statement", "return_statement", "short_var_declaration", "assignment_statement"},
    "java": {"expression_statement", "return_statement", "local_variable_declaration"},
    "ruby": {"assignment", "call", "return"},
}
BLOCKS = {"python": {"block"}, "typescript": {"statement_block"}, "javascript": {"statement_block"},
          "go": {"block", "statement_list"}, "java": {"block"}, "ruby": {"body_statement", "do_block", "block_body"}}
TRIVIAL = {"identifier", "integer", "float", "true", "false", "none", "nil", "null", "number",
           "int_literal", "decimal_integer_literal", "string_literal", "keyword_argument",
           "pair", "spread_element", "list_splat", "dictionary_splat", "block_argument", "this",
           "self", "simple_symbol", "type_identifier", "field_identifier", "constant",
           "scope_resolution", "nil", "string_content"}
# An argument that cannot move without changing what the program says: a
# heredoc's body follows the line that opens it, so hoisting the opener
# would make the original line part of the heredoc.
UNMOVABLE = {"await_expression", "await", "yield", "lambda", "arrow_function", "block", "do_block",
             "func_literal", "lambda_expression", "heredoc_beginning", "heredoc_body"}


def _statement_for_line(tree, line: int, lang: str):
    for n in walk(tree.root_node):
        if n.start_point[0] + 1 != line or n.type not in STATEMENTS[lang]:
            continue
        if n.parent is None or n.parent.type not in BLOCKS[lang]:
            continue
        if n.start_point[0] != n.end_point[0]:
            return None  # single-line statements only: keeps the line bookkeeping exact
        if enclosing(n, FUNCTION_NODES[lang]) is None:
            return None  # module level: a helper defined later is not yet bound
        return n
    return None


def _main_argument(stmt, lang: str):
    """The first non-trivial argument of the outermost call in the statement."""
    for n in walk(stmt):
        if n.type != CALL_NODES[lang]:
            continue
        args = n.child_by_field_name(ARGS_FIELD[lang])
        if args is None:
            continue
        for a in args.named_children:
            if a.type in TRIVIAL or a.type == "comment":
                continue
            if any(c.type in UNMOVABLE for c in walk(a)):
                continue
            if lang == "go" and a.type == "parenthesized_type":
                continue
            return a
    return None


def _indent(line_text: str) -> str:
    return line_text[: len(line_text) - len(line_text.lstrip())]


TEMP_DECL = {"python": "{v} = {e}", "typescript": "const {v} = {e};", "javascript": "const {v} = {e};",
             "go": "{v} := {e}", "java": "var {v} = {e};", "ruby": "{v} = {e}"}


def transform_hoist(text: str, lang: str, lines: set[int]) -> Rewrite | None:
    tree = parse(text, lang)
    src_lines = text.split("\n")
    inserts: dict[int, str] = {}
    edits = []
    n = 0
    for line in sorted(lines):
        stmt = _statement_for_line(tree, line, lang)
        arg = _main_argument(stmt, lang) if stmt is not None else None
        if arg is None or arg.start_point[0] != arg.end_point[0]:
            continue
        n += 1
        var = f"hoisted{n}"
        inserts[line] = _indent(src_lines[line - 1]) + TEMP_DECL[lang].format(v=var, e=arg.text.decode())
        edits.append((arg.start_byte, arg.end_byte, var.encode()))
    if not edits:
        return None
    body = apply_edits(text.encode(), edits).decode().split("\n")
    out, line_map = [], {}
    for i, l in enumerate(body, 1):
        if i in inserts:
            out.append(inserts[i])
        out.append(l)
        line_map[i] = len(out)
    return Rewrite("\n".join(out), line_map, set(inserts))


HELPER_DEF = {
    "python": "\n\ndef _same(value):\n    return value\n",
    "typescript": "\nfunction _same(value: any): any {\n  return value;\n}\n",
    "javascript": "\nfunction _same(value) {\n  return value;\n}\n",
    "go": "\nfunc _same[T any](value T) T {\n\treturn value\n}\n",
    "ruby": "\ndef _same(value)\n  value\nend\n",
}


def transform_helper(text: str, lang: str, lines: set[int]) -> Rewrite | None:
    tree = parse(text, lang)
    edits, affected = [], set()
    for line in sorted(lines):
        stmt = _statement_for_line(tree, line, lang)
        arg = _main_argument(stmt, lang) if stmt is not None else None
        if arg is None or arg.start_point[0] != arg.end_point[0]:
            continue
        edits.append((arg.start_byte, arg.end_byte, f"_same({arg.text.decode()})".encode()))
        affected.add(line)
    if not edits:
        return None
    new = apply_edits(text.encode(), edits).decode()
    if lang == "java":
        cls = next((c for c in tree.root_node.named_children
                    if c.type in ("class_declaration", "enum_declaration")), None)
        if cls is None:
            return None
        body = cls.child_by_field_name("body")
        close_line = body.end_point[0]  # 0-based line of the closing brace
        lines_ = new.split("\n")
        lines_[close_line:close_line] = ["    private static <T> T _same(T value) {", "        return value;", "    }"]
        new = "\n".join(lines_)
        line_map = {i: (i if i - 1 < close_line else i + 3) for i in range(1, text.count("\n") + 2)}
        return Rewrite(new, line_map, affected)
    return Rewrite(new.rstrip("\n") + "\n" + HELPER_DEF[lang], identity_map(text), affected)


# ---------------------------------------------------------------------------
# move: the function holding the marker goes to the end of its container
# ---------------------------------------------------------------------------

CONTAINERS = {
    "python": {"module", "block"},
    "typescript": {"program", "class_body"}, "javascript": {"program", "class_body"},
    "go": {"source_file"}, "java": {"class_body"}, "ruby": {"program", "body_statement"},
}
MOVABLE = {
    "python": {"function_definition", "decorated_definition"},
    "typescript": {"function_declaration", "method_definition", "export_statement"},
    "javascript": {"function_declaration", "method_definition", "export_statement"},
    "go": {"function_declaration", "method_declaration"},
    "java": {"method_declaration"},
    "ruby": {"method", "singleton_method"},
}


def _movable(node, lang: str) -> bool:
    if node.type not in MOVABLE[lang] or node.parent is None or node.parent.type not in CONTAINERS[lang]:
        return False
    if node.type == "export_statement":
        return any(c.type == "function_declaration" for c in node.named_children)
    if lang == "python" and node.parent.type == "block":
        return node.parent.parent is not None and node.parent.parent.type == "class_definition"
    if lang == "python" and node.type == "function_definition" and node.parent.type == "decorated_definition":
        return False
    return True


def transform_move(text: str, lang: str, lines: set[int]) -> Rewrite | None:
    tree = parse(text, lang)
    src = text.split("\n")
    total = len(src)
    for line in sorted(lines):
        n = node_at_line(tree, line)
        if n is None:
            continue
        unit = n
        while unit is not None and not _movable(unit, lang):
            unit = unit.parent
        if unit is None:
            continue
        siblings = [c for c in unit.parent.named_children if c.type != "comment"]
        last = siblings[-1]
        if last.id == unit.id:
            continue  # already last
        s, e = unit.start_point[0], unit.end_point[0]          # 0-based, inclusive
        dest = last.end_point[0]                                # insert after this line
        if e >= dest:
            continue
        order = list(range(0, s)) + list(range(e + 1, dest + 1)) + [None] + list(range(s, e + 1)) \
            + list(range(dest + 1, total))
        out, line_map = [], {}
        for idx in order:
            if idx is None:
                out.append("")  # a blank line between the last sibling and the moved unit
                continue
            out.append(src[idx])
            line_map[idx + 1] = len(out)
        affected = set(range(s + 1, e + 2))
        new = "\n".join(out)
        return Rewrite(new, line_map, affected)
    return None


# ---------------------------------------------------------------------------
# strings: the string building on the marked line changes style
# ---------------------------------------------------------------------------

def _py_literal(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def _python_strings(node, src: bytes) -> bytes | None:
    t = node.text.decode()
    if node.type == "string" and re.match(r"^[fF]['\"](?!['\"])", t):
        parts, ok = [], True
        for c in node.named_children:
            if c.type == "string_content":
                if "\\" in c.text.decode():
                    return None
                parts.append(_py_literal(c.text.decode()))
            elif c.type == "interpolation":
                if any(x.type in ("format_specifier", "type_conversion") for x in c.named_children):
                    ok = False
                expr = c.child_by_field_name("expression") or (c.named_children[0] if c.named_children else None)
                if expr is None:
                    ok = False
                else:
                    parts.append(f"str({expr.text.decode()})")
            elif c.type in ("string_start", "string_end"):
                continue
            else:
                ok = False
        if not ok or not parts or "{{" in t or "}}" in t:
            return None
        return ("(" + " + ".join(parts) + ")").encode()
    if node.type == "binary_operator":
        op = node.child_by_field_name("operator")
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        if op is not None and op.type == "%" and left.type == "string" and right.type != "tuple" \
                and left.text.decode().count("%") == 1 and "%s" in left.text.decode() \
                and not left.text.decode()[:1] in "fFbBrR":
            body = left.text.decode()
            q = body[0]
            inner = body[1:-1].replace("{", "{{").replace("}", "}}").replace("%s", "{" + right.text.decode() + "}")
            if q in inner or "\\" in inner:
                return None
            return f"f{q}{inner}{q}".encode()
    if node.type == "call":
        fn = node.child_by_field_name("function")
        if fn is not None and fn.type == "attribute" and fn.child_by_field_name("attribute").text == b"format":
            obj = fn.child_by_field_name("object")
            args = node.child_by_field_name("arguments").named_children
            if obj.type == "string" and obj.text.decode()[:1] in "'\"" and all(a.type != "keyword_argument" for a in args):
                body = obj.text.decode()
                q, inner = body[0], body[1:-1]
                if inner.count("{}") != len(args) or re.search(r"\{[^}]", inner.replace("{}", "")) \
                        or "\\" in inner or any(q in a.text.decode() for a in args):
                    return None
                for a in args:
                    inner = inner.replace("{}", "{" + a.text.decode() + "}", 1)
                return f"f{q}{inner}{q}".encode()
    return None


def _ts_strings(node) -> bytes | None:
    if node.type == "template_string":
        parts = []
        for c in node.named_children:
            if c.type == "string_fragment":
                parts.append(json.dumps(c.text.decode()))
            elif c.type == "template_substitution":
                parts.append("String(" + c.named_children[0].text.decode() + ")")
            elif c.type == "escape_sequence":
                parts.append('"' + c.text.decode() + '"')
            else:
                return None
        if not any(c.type == "template_substitution" for c in node.named_children):
            return None
        return ("(" + " + ".join(parts or ['""']) + ")").encode()
    if node.type == "binary_expression" and node.child_by_field_name("operator").type == "+":
        flat = _flatten_plus(node, "binary_expression", "string")
        if flat is None or not any(p.type == "string" for p in flat) or all(p.type == "string" for p in flat):
            return None
        out = []
        for p in flat:
            if p.type == "string":
                frag = p.text.decode()[1:-1]
                if "`" in frag or "${" in frag or "\\" in frag:
                    return None
                out.append(frag)
            else:
                out.append("${" + p.text.decode() + "}")
        return ("`" + "".join(out) + "`").encode()
    return None


def _flatten_plus(node, bin_type: str, str_type: str):
    """`a + b + c` as [a, b, c] when every operator is +; None otherwise."""
    if node.type == bin_type:
        op = node.child_by_field_name("operator")
        if op is None or op.type != "+":
            return [node]
        left = _flatten_plus(node.child_by_field_name("left"), bin_type, str_type)
        right = _flatten_plus(node.child_by_field_name("right"), bin_type, str_type)
        return None if left is None or right is None else left + right
    return [node]


def _go_strings(node) -> bytes | None:
    if node.type == "call_expression" and node.child_by_field_name("function").text == b"fmt.Sprintf":
        args = node.child_by_field_name("arguments").named_children
        if not args or args[0].type != "interpreted_string_literal":
            return None
        fmt_ = args[0].text.decode()[1:-1]
        if re.search(r"%[^s]", fmt_.replace("%s", "")) or fmt_.count("%s") != len(args) - 1:
            return None
        pieces = fmt_.split("%s")
        out = []
        for i, piece in enumerate(pieces):
            if piece:
                out.append('"' + piece + '"')
            if i < len(args) - 1:
                out.append(args[i + 1].text.decode())
        return ("(" + " + ".join(out) + ")").encode()
    if node.type == "binary_expression" and node.child_by_field_name("operator").type == "+":
        flat = _flatten_plus(node, "binary_expression", "interpreted_string_literal")
        if flat is None or not any(p.type == "interpreted_string_literal" for p in flat) \
                or all(p.type == "interpreted_string_literal" for p in flat):
            return None
        fmt_, args = "", []
        for p in flat:
            if p.type == "interpreted_string_literal":
                fmt_ += p.text.decode()[1:-1].replace("%", "%%")
            elif p.type == "raw_string_literal":
                return None
            else:
                fmt_ += "%s"
                args.append(p.text.decode())
        return ('fmt.Sprintf("' + fmt_ + '", ' + ", ".join(args) + ")").encode()
    return None


def _java_strings(node) -> bytes | None:
    if node.type == "method_invocation" and node.child_by_field_name("object") is not None \
            and node.child_by_field_name("object").text == b"String" \
            and node.child_by_field_name("name").text == b"format":
        args = node.child_by_field_name("arguments").named_children
        if not args or args[0].type != "string_literal":
            return None
        fmt_ = args[0].text.decode()[1:-1]
        if re.search(r"%[^s]", fmt_.replace("%s", "")) or fmt_.count("%s") != len(args) - 1:
            return None
        out = []
        for i, piece in enumerate(fmt_.split("%s")):
            out.append('"' + piece + '"')
            if i < len(args) - 1:
                out.append("String.valueOf(" + args[i + 1].text.decode() + ")")
        return ("(" + " + ".join(out) + ")").encode()
    if node.type == "binary_expression" and node.child_by_field_name("operator").type == "+":
        flat = _flatten_plus(node, "binary_expression", "string_literal")
        if flat is None or flat[0].type != "string_literal" or all(p.type == "string_literal" for p in flat):
            return None
        fmt_, args = "", []
        for p in flat:
            if p.type == "string_literal":
                fmt_ += p.text.decode()[1:-1].replace("%", "%%")
            else:
                fmt_ += "%s"
                args.append(p.text.decode())
        return ('String.format("' + fmt_ + '", ' + ", ".join(args) + ")").encode()
    return None


def _ruby_strings(node) -> bytes | None:
    if node.type == "string" and node.text.decode().startswith('"') \
            and any(c.type == "interpolation" for c in node.named_children):
        parts = []
        for c in node.named_children:
            if c.type == "string_content":
                parts.append('"' + c.text.decode() + '"')
            elif c.type == "interpolation":
                inner = c.named_children
                if len(inner) != 1:
                    return None
                parts.append("(" + inner[0].text.decode() + ").to_s")
            elif c.type == "escape_sequence":
                parts.append('"' + c.text.decode() + '"')
            else:
                return None
        return ("(" + " + ".join(parts) + ")").encode()
    if node.type == "binary" and node.child_by_field_name("operator").type == "+":
        flat = _flatten_plus(node, "binary", "string")
        if flat is None or flat[0].type != "string" or all(p.type == "string" for p in flat):
            return None
        out = ""
        for p in flat:
            if p.type == "string":
                if not p.text.decode().startswith('"') and not p.text.decode().startswith("'"):
                    return None
                frag = p.text.decode()[1:-1]
                if "#{" in frag or '"' in frag or "\\" in frag:
                    return None
                out += frag
            else:
                out += "#{" + p.text.decode() + "}"
        return ('"' + out + '"').encode()
    return None


STRING_REWRITERS = {
    "python": lambda n, src: _python_strings(n, src),
    "typescript": lambda n, src: _ts_strings(n), "javascript": lambda n, src: _ts_strings(n),
    "go": lambda n, src: _go_strings(n), "java": lambda n, src: _java_strings(n),
    "ruby": lambda n, src: _ruby_strings(n),
}


def transform_strings(text: str, lang: str, lines: set[int]) -> Rewrite | None:
    tree = parse(text, lang)
    src = text.encode()
    edits, affected = [], set()
    taken: list[tuple[int, int]] = []
    for n in walk(tree.root_node):
        line = n.start_point[0] + 1
        if line not in lines or n.start_point[0] != n.end_point[0]:
            continue
        if any(s <= n.start_byte and n.end_byte <= e for s, e in taken):
            continue  # inside an expression already rewritten (outermost wins)
        rep = STRING_REWRITERS[lang](n, src)
        if rep is None or b"\n" in rep:
            continue
        edits.append((n.start_byte, n.end_byte, rep))
        taken.append((n.start_byte, n.end_byte))
        affected.add(line)
    if not edits:
        return None
    return Rewrite(apply_edits(src, edits).decode(), identity_map(text), affected)


def rewrite(kind: str, text: str, lang: str, lines: set[int]) -> Rewrite | None:
    if kind == "rename":
        rw = transform_rename(text, lang)
        if rw is not None:
            rw.affected &= lines
    elif kind == "hoist":
        rw = transform_hoist(text, lang, lines)
    elif kind == "helper":
        rw = transform_helper(text, lang, lines)
    elif kind == "move":
        rw = transform_move(text, lang, lines)
    else:
        rw = transform_strings(text, lang, lines)
    if rw is None or not rw.affected:
        return None
    if not parses_clean(rw.text, lang) and parses_clean(text, lang):
        return None
    return rw


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _scan(tree: Path) -> list[tuple[str, str, int, int]]:
    from arbiter.engine import run_scan
    from arbiter.policy import load_config
    rep = run_scan([str(tree)], load_config(None), use_adapters=False, use_cache=False)
    return [(f.rule_id, f.location.path, f.location.start_line,
             max(f.location.end_line or 0, f.location.start_line)) for f in rep.active()]


def _caught(found, rule: str, path: str, line: int) -> bool:
    return any(r == rule and p == path and s <= line <= e for r, p, s, e in found)


def _shifted(found, rule: str, path: str, line: int, radius: int = 3) -> int | None:
    """The line a finding of this rule moved to, when it is not on the
    defect but within `radius` lines: a location regression rather than a
    lost finding (still a miss for line-matched scoring)."""
    near = [s for r, p, s, _e in found if r == rule and p == path and 0 < abs(s - line) <= radius]
    return min(near, key=lambda s: abs(s - line)) if near else None


def run_language(corpus: Path, lang: str, kinds: list[str], keep: Path | None) -> list[dict]:
    bad = corpus / lang / "bad"
    expected = [e for e in planted.markers(bad) if not e.external]
    original = _scan(bad)
    baseline = [e for e in expected if _caught(original, e.rule, e.path, e.line)]
    by_file: dict[str, list] = defaultdict(list)
    for e in baseline:
        by_file[e.path].append(e)
    rows = []
    for kind in kinds:
        work = Path(tempfile.mkdtemp(prefix=f"variants-{lang}-{kind}-"))
        try:
            tree = work / "bad"
            shutil.copytree(bad, tree)
            maps: dict[str, Rewrite] = {}
            for rel, marks in by_file.items():
                file_lang = EXT_LANG.get(Path(rel).suffix)
                if file_lang is None:
                    continue
                path = tree / rel
                rw = rewrite(kind, path.read_text(encoding="utf-8"), file_lang, {m.line for m in marks})
                if rw is None:
                    continue
                path.write_text(rw.text, encoding="utf-8")
                maps[rel] = rw
            if maps:
                found = _scan(tree)
                for rel, rw in maps.items():
                    for m in by_file[rel]:
                        if m.line not in rw.affected:
                            continue
                        new_line = rw.line_map.get(m.line, m.line)
                        kept = _caught(found, m.rule, rel, new_line) or (
                            # the hoisted temporary holds the defect's expression now
                            kind == "hoist" and _caught(found, m.rule, rel, new_line - 1))
                        rows.append({"language": lang, "kind": kind, "rule": m.rule, "path": rel,
                                     "line": m.line, "variant_line": new_line, "kept": kept,
                                     "shifted_to": None if kept else _shifted(found, m.rule, rel, new_line),
                                     "variant_text": rw.text.split("\n")[new_line - 1].strip()[:160]})
            if keep is not None:
                dest = keep / lang / kind
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(tree, dest)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return rows


def robustness(rows: list[dict]) -> list[dict]:
    per: dict[str, dict] = defaultdict(lambda: {"applicable": 0, "kept": 0, "by_kind": defaultdict(lambda: [0, 0])})
    for r in rows:
        e = per[r["rule"]]
        e["applicable"] += 1
        e["kept"] += r["kept"]
        e["by_kind"][r["kind"]][0] += r["kept"]
        e["by_kind"][r["kind"]][1] += 1
    out = []
    for rule, e in per.items():
        out.append({"rule": rule, "applicable": e["applicable"], "kept": e["kept"],
                    "robustness": round(e["kept"] / e["applicable"], 3),
                    "by_kind": {k: f"{a}/{b}" for k, (a, b) in sorted(e["by_kind"].items())}})
    return sorted(out, key=lambda r: (r["robustness"], -r["applicable"], r["rule"]))


def render_markdown(rows: list[dict], table: list[dict], kinds: list[str]) -> str:
    total = len(rows)
    kept = sum(r["kept"] for r in rows)
    lines = ["# Mutation variants", "",
             f"{kept}/{total} (marker, transform) pairs kept"
             + (f" ({kept / total * 100:.0f}%)" if total else ""), "",
             "| transform | kept / applicable |", "|---|---|"]
    for k in kinds:
        sub = [r for r in rows if r["kind"] == k]
        lines.append(f"| {k} | {sum(r['kept'] for r in sub)}/{len(sub)} |")
    lines += ["", "## Robustness per rule (weakest first)", "",
              "| rule | robustness | kept / applicable | by transform |", "|---|---|---|---|"]
    for t in table:
        lines.append(f"| `{t['rule']}` | {t['robustness'] * 100:.0f}% | {t['kept']}/{t['applicable']} | "
                     + ", ".join(f"{k} {v}" for k, v in t["by_kind"].items()) + " |")
    lost = [r for r in rows if not r["kept"]]
    if lost:
        lines += ["", "## Findings lost", ""]
        for r in lost:
            moved = (f" (reported {r['shifted_to'] - r['variant_line']:+d} lines away: a location "
                     f"regression)" if r.get("shifted_to") else "")
            lines.append(f"- [{r['kind']}] `{r['rule']}` {r['language']}/{r['path']}:{r['line']} -> "
                         f"`{r['variant_text']}`{moved}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("languages", nargs="*")
    ap.add_argument("--kinds", default=",".join(KINDS))
    ap.add_argument("--corpus", type=Path, default=ROOT / "fixtures" / "planted")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--markdown", type=Path)
    ap.add_argument("--keep", type=Path, help="copy every variant tree here for inspection")
    args = ap.parse_args(argv)
    kinds = [k for k in args.kinds.split(",") if k]
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        ap.error(f"unknown transform(s) {bad}; choose from {', '.join(KINDS)}")
    rows = []
    for lang in args.languages or planted.languages(args.corpus):
        rows += run_language(args.corpus, lang, kinds, args.keep)
    table = robustness(rows)
    if args.json:
        args.json.write_text(json.dumps({"rows": rows, "rules": table}, indent=1) + "\n", encoding="utf-8")
    md = render_markdown(rows, table, kinds)
    if args.markdown:
        args.markdown.write_text(md, encoding="utf-8")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
