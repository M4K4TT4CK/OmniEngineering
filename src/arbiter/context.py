"""What a model is shown about one finding: a bounded, deterministic excerpt (ARB-067).

Used by the model labellers (`tools/label_models.py`, through
`tools/ground_truth/gt_common.excerpt`) and by the verification pass
(`verify.py`), so the two see the same code for the same finding.

Calibration round 2 found that every false `tp` call on blind ground truth
had its deciding fact outside a single-function excerpt: a "safe source"
method defined in another file, a Flask route decorator above the `def`, a
Juliet case split across files. So the excerpt is, in order of priority:

1. the enclosing function, **with its decorators and annotations** (Python
   `@app.route`, TypeScript `@Get`, Java `@Override` are part of the span);
2. the enclosing class header (its annotations, name, `extends`);
3. the file's imports;
4. same-file route registrations of the handler (`app.get("/x", handler)`,
   `add_url_rule(..., view_func=handler)`);
5. same-file functions called on the **taint path** (a backward, textual
   slice from the flagged statement through the assignments that feed it);
6. the **repository definition** of functions and methods called on that
   path when they live in another file of the same repository, resolved by
   name plus receiver type or import (never by name alone across the repo);
7. when the value at the flagged line arrives as a **parameter** of the
   enclosing function, up to two **same-file callers** of that function
   (each caller's function, or its head and a window around the call), so
   the argument's origin is visible (Juliet `badSink(data)`, a handler's
   `doPost(request, response)` from `doGet`);
8. other same-file functions the enclosing one calls.

Everything is bounded (`Limits`), the whole text is passed through
`judgement._mask_secrets`, and the output depends only on the files' bytes,
so a cache key over it is stable. Nothing here runs the code it reads.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

LANG_BY_EXT = {".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
               ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".go": "go",
               ".java": "java", ".rb": "ruby"}
_EXTS = {"python": (".py",), "java": (".java",), "go": (".go",), "ruby": (".rb",),
         "typescript": (".ts", ".tsx", ".js", ".mjs", ".cjs", ".jsx"),
         "javascript": (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")}


@dataclass(frozen=True)
class Limits:
    function_lines: int = 80   # the enclosing function (a window around the line beyond this)
    import_lines: int = 25
    header_lines: int = 4      # enclosing class annotations + declaration
    route_lines: int = 3
    helpers: int = 3           # same-file callees
    helper_lines: int = 40     # each
    defs: int = 3              # definitions from other files
    def_lines: int = 30        # each
    total: int = 220           # every line shown, all sections together
    window: int = 30           # each side of the line when no function encloses it
    callers: int = 2           # same-file callers, when the tainted value is a parameter
    caller_lines: int = 25     # each


LIMITS = Limits()

_IMPORT = {
    "python": re.compile(r"^\s*(import\s|from\s+\S+\s+import\s)"),
    "typescript": re.compile(r"^\s*(import\s|export\s.*\sfrom\s|(const|let|var)\s.*=\s*require\()"),
    "javascript": re.compile(r"^\s*(import\s|export\s.*\sfrom\s|(const|let|var)\s.*=\s*require\()"),
    "go": re.compile(r'^\s*(import\b|"[\w./-]+"\s*$|\w+\s+"[\w./-]+"\s*$)'),
    "java": re.compile(r"^\s*import\s"),
    "ruby": re.compile(r"^\s*(require|require_relative|load)\b"),
}
_CLASS_TYPES = {"class_declaration", "class_definition", "class", "abstract_class_declaration",
                "interface_declaration", "enum_declaration", "module"}
_ANON = {"lambda", "lambda_expression", "arrow_function", "func_literal"}
_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "new", "function", "def", "class", "super",
    "this", "self", "elif", "else", "try", "except", "with", "not", "and", "or", "in", "is", "await",
    "async", "yield", "throw", "throws", "raise", "typeof", "instanceof", "sizeof", "print", "len",
    "str", "int", "float", "bool", "list", "dict", "set", "tuple", "range", "isinstance", "func",
    "go", "defer", "make", "append", "string", "var", "let", "const", "final", "static", "public",
    "private", "protected", "void", "null", "None", "nil", "true", "false", "True", "False",
    "lambda", "do", "end", "unless", "puts", "require", "import", "from", "as", "case", "when",
    "then", "begin", "rescue", "ensure", "assert", "synchronized", "fn", "of", "delete",
}
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_CALL = re.compile(r"((?:[A-Za-z_$][\w$]*(?:\(\))?\s*\.\s*)*)([A-Za-z_$][\w$]*)\s*\(")
_ASSIGN = re.compile(r"(?<![=!<>:+\-*/%&|^])(:=|[-+*/%&|^]?=)(?![=>~])")
_ROUTE = re.compile(r"\.(get|post|put|patch|delete|all|use|route|add_url_rule|register|handle|"
                    r"HandleFunc|Handle|GET|POST|PUT|PATCH|DELETE|Any|Group|on)\s*\(|"
                    r"\b(path|re_path|url|view_func|Route|router)\b")


def language_of(path: str) -> str:
    return LANG_BY_EXT.get(Path(path).suffix, "")


# ---------------------------------------------------------------------------
# Parsing helpers (tree-sitter when available; no tree means a plain window)
# ---------------------------------------------------------------------------

def parse(text: str, path: str, language: str):
    try:
        from . import ast as ts
        grammar = ts.grammar_for(path, language)
        if not grammar or not ts.available():
            return None
        return ts._parser(grammar).parse(text.encode("utf-8"))
    except Exception:  # noqa: BLE001 - no tree means "fall back to a window", never a crash
        return None


def _function_types(language: str) -> set[str]:
    from .lang_rules import FUNCTION_TYPES
    return FUNCTION_TYPES.get("typescript" if language == "javascript" else language, set())


def _decorated_start(node) -> int:
    """0-based first row of `node` including decorators/annotations written
    above it (Java annotations are already inside the method node)."""
    start = node.start_point[0]
    p = node.parent
    if p is not None and p.type == "decorated_definition":        # Python
        return p.start_point[0]
    sib = node.prev_named_sibling                                  # TypeScript/JS class members
    while sib is not None and sib.type == "decorator":
        start = sib.start_point[0]
        sib = sib.prev_named_sibling
    if p is not None and p.type in ("export_statement",):
        start = min(start, p.start_point[0])
    return start


def enclosing_function(tree, text: str, language: str, line: int):
    """The innermost named function node around `line` (1-based), else the
    innermost anonymous one, else None."""
    if tree is None:
        return None
    types = _function_types(language)
    named = types - _ANON
    lines = text.splitlines()
    row = max(0, line - 1)
    col = len(lines[row]) - len(lines[row].lstrip()) if row < len(lines) else 0
    node = tree.root_node.descendant_for_point_range((row, col), (row, col))
    best = None
    while node is not None:
        if node.type in named:
            return node
        if node.type in types and best is None:
            best = node
        node = node.parent
    return best


def function_span(text: str, path: str, language: str, line: int,
                  decorators: bool = True) -> tuple[int, int] | None:
    """1-based inclusive span of the innermost named function around `line`,
    decorators included unless `decorators=False`."""
    node = enclosing_function(parse(text, path, language), text, language, line)
    if node is None:
        return None
    start = _decorated_start(node) if decorators else node.start_point[0]
    return start + 1, node.end_point[0] + 1


def named_functions(text: str, path: str, language: str, tree=None) -> dict[str, tuple[int, int]]:
    """Name -> 1-based span (decorators included) of every named function in
    the file; the first definition of a name wins."""
    tree = tree if tree is not None else parse(text, path, language)
    if tree is None:
        return {}
    types = _function_types(language)
    data = text.encode("utf-8")
    out: dict[str, tuple[int, int]] = {}
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type in types:
            name = n.child_by_field_name("name")
            if name is not None:
                key = data[name.start_byte:name.end_byte].decode("utf-8", "replace")
                out.setdefault(key, (_decorated_start(n) + 1, n.end_point[0] + 1))
        stack.extend(reversed(n.children))
    return out


def _class_header(node) -> tuple[int, int] | None:
    """1-based span of the enclosing class's annotations and declaration line(s)."""
    p = node.parent if node is not None else None
    while p is not None and p.type not in _CLASS_TYPES:
        p = p.parent
    if p is None or p.parent is None:        # Python's root node is also called `module`
        return None
    body = p.child_by_field_name("body")
    start = _decorated_start(p) + 1
    if body is None:
        end = p.start_point[0] + 1
    elif body.type == "block":               # Python: the body starts on the next line
        end = body.start_point[0]
    else:                                    # a brace body: its `{` line is the header's end
        end = body.start_point[0] + 1
    return start, max(start, end)


# ---------------------------------------------------------------------------
# The repository around the file
# ---------------------------------------------------------------------------

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache",
              "dist", "build", "target", ".arbiter", "arbiter-out"}
_WALK_CACHE: dict[str, list[str]] = {}
MAX_REPO_FILES = 200_000


@dataclass
class RepoView:
    """Read-only access to the other source files of one repository.

    `paths` are posix paths relative to the repository root. `read` returns
    a file's text ('' when unreadable). `transform(text, rel)` is applied to
    every file read (the labellers' blinding); `shown(rel)` is the path a
    model is told (blinded paths for ground truth)."""

    paths: list[str]
    read_raw: Callable[[str], str]
    transform: Callable[[str, str], str] | None = None
    shown: Callable[[str], str] | None = None
    _by_name: dict = field(default_factory=dict, repr=False)
    _set: set = field(default_factory=set, repr=False)
    _texts: dict = field(default_factory=dict, repr=False)
    _by_dir: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self.paths = sorted(set(self.paths))
        self._set = set(self.paths)
        for p in self.paths:
            self._by_name.setdefault(PurePosixPath(p).name, []).append(p)

    @classmethod
    def from_root(cls, root: Path | str, **kw) -> "RepoView":
        root = Path(root).expanduser()
        key = str(root.resolve())
        if key not in _WALK_CACHE:
            out: list[str] = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
                rel = Path(dirpath).relative_to(root)
                for fn in filenames:
                    if Path(fn).suffix in LANG_BY_EXT:
                        out.append((rel / fn).as_posix())
                if len(out) > MAX_REPO_FILES:
                    break
            _WALK_CACHE[key] = out

        def read(rel: str) -> str:
            try:
                return (root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""
        return cls(paths=_WALK_CACHE[key], read_raw=read, **kw)

    def exists(self, rel: str) -> bool:
        return rel in self._set

    def go_dirs(self) -> set[str]:
        if "__go_dirs__" not in self._texts:
            self._texts["__go_dirs__"] = {_dir(p) for p in self.paths if p.endswith(".go")}
        return self._texts["__go_dirs__"]

    def by_name(self, name: str) -> list[str]:
        return self._by_name.get(name, [])

    def in_dir(self, d: str) -> list[str]:
        if not self._by_dir:
            for p in self.paths:
                self._by_dir.setdefault(_dir(p), []).append(p)
        return self._by_dir.get(d.strip("/"), [])

    def read(self, rel: str) -> str:
        if rel not in self._texts:
            text = self.read_raw(rel) or ""
            if text and self.transform is not None:
                text = self.transform(text, rel)
            self._texts[rel] = text
        return self._texts[rel]

    def show(self, rel: str) -> str:
        return self.shown(rel) if self.shown else rel


# ---------------------------------------------------------------------------
# Taint path: a backward textual slice inside the function
# ---------------------------------------------------------------------------

def _statements(rows: list[str], lo: int, hi: int, language: str) -> list[tuple[int, int, str]]:
    """(first line, last line, joined text) of each logical statement in
    lines lo..hi (1-based): a line continues while brackets are open or it
    ends with an operator."""
    out, buf, first, depth = [], [], None, 0
    cont = ("=", "+", ",", "(", ".", "\\", "&", "|", "?", "[") if language != "python" \
        else ("=", "+", ",", "(", ".", "\\", "&", "|", "[")
    for i in range(lo, hi + 1):
        s = rows[i - 1].strip()
        if first is None:
            first = i
        buf.append(s)
        depth += s.count("(") + s.count("[") - s.count(")") - s.count("]")
        if len(buf) < 12 and (depth > 0 or s.endswith(cont)):
            continue
        out.append((first, i, " ".join(buf)))
        buf, first, depth = [], None, 0
    if buf:
        out.append((first, hi, " ".join(buf)))
    return out


def _idents(s: str) -> set[str]:
    s = re.sub(r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')", " ", s)
    return {m for m in _IDENT.findall(s) if m not in _KEYWORDS}


def _split_assign(s: str) -> tuple[str, str] | None:
    body = re.sub(r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')", lambda m: "_" * len(m.group(0)), s)
    m = _ASSIGN.search(body)
    if not m or body[:m.start()].count("(") != body[:m.start()].count(")"):
        return None
    return s[:m.start()], s[m.end():]


def _sink_receiver(stmt: str) -> set[str]:
    """The object the flagged statement's outer call is made on
    (`statement` in `statement.addBatch(sql)`): where the data goes, not
    where it comes from, so its own history is not followed. Kept when the
    name occurs again in the statement (`s.add(s.size())`)."""
    rest = stmt
    sa = _split_assign(stmt)
    if sa is not None:
        rest = sa[1]
    rest = re.sub(r"^\s*(return|await|yield)\s+", "", rest.strip())
    m = re.match(r"([A-Za-z_$][\w$]*)\s*\.", rest)
    if not m:
        return set()
    name = m.group(1)
    return {name} if len(re.findall(rf"(?<![\w$]){re.escape(name)}(?![\w$])", stmt)) == 1 else set()


def _slice(rows: list[str], span: tuple[int, int], line: int, language: str):
    """(statements, indexes on the taint path -- the flagged statement first --,
    identifiers tracked) for the backward textual slice to `line`."""
    lo, hi = span
    stmts = _statements(rows, lo, hi, language)
    idx = next((k for k, (a, b, _t) in enumerate(stmts) if a <= line <= b), None)
    if idx is None:
        return stmts, [], set()
    tracked = _idents(stmts[idx][2]) - _sink_receiver(stmts[idx][2])
    order = [idx]
    for k in range(idx - 1, -1, -1):
        sa = _split_assign(stmts[k][2])
        if sa is None:
            continue
        lhs, rhs = sa
        if _idents(lhs) & tracked:
            tracked |= _idents(rhs)
            order.append(k)
    return stmts, order, tracked


def path_calls(rows: list[str], span: tuple[int, int], line: int, language: str) -> list[tuple[str, str, int]]:
    """(receiver, name, line) for calls on the taint path to `line`, the
    flagged statement first, then each feeding assignment going upward."""
    stmts, order, _tracked = _slice(rows, span, line, language)
    out, seen = [], set()
    for k in order:
        a, _b, t = stmts[k]
        flat = re.sub(r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')", "''", t)
        for m in _CALL.finditer(flat):
            recv = re.sub(r"\s+", "", m.group(1)).rstrip(".")
            name = m.group(2)
            if name in _KEYWORDS or (recv, name) in seen:
                continue
            if re.search(r"\bnew\s+$", flat[:m.start()]):
                continue                     # a constructor: rarely decides taint, costs lines
            seen.add((recv, name))
            out.append((recv, name, a))
    return out


_PARAM_SKIP = {"type_identifier", "primitive_type", "generic_type", "scoped_type_identifier",
               "array_type", "type_annotation", "pointer_type", "qualified_type", "slice_type",
               "map_type", "modifiers", "annotation", "marker_annotation", "dimensions"}


def parameter_names(fn, text: str) -> list[str]:
    """Names of the parameters a function node declares, in order (Python
    `self`/`cls` and Go receivers left out)."""
    if fn is None:
        return []
    params = fn.child_by_field_name("parameters")
    if params is None:
        return []
    data = text.encode("utf-8")
    out: list[str] = []

    def first_ident(n):
        stack = [n]
        while stack:
            c = stack.pop(0)
            if c.type in ("identifier", "shorthand_property_identifier_pattern") and c.parent is not None \
                    and c.parent.type not in _PARAM_SKIP:
                return c
            if c.type in _PARAM_SKIP or c.type in ("default_value",):
                continue
            stack[0:0] = [x for x in c.named_children if x.type not in _PARAM_SKIP]
        return None

    for p in params.named_children:
        if p.type in ("comment", "line_comment", "block_comment"):
            continue
        names = list(p.children_by_field_name("name")) if p.type == "parameter_declaration" else []
        if not names:
            n = p if p.type == "identifier" else (p.child_by_field_name("name")
                                                  or p.child_by_field_name("pattern") or first_ident(p))
            names = [n] if n is not None else []
        flat = []
        for n in names:
            if n is not None and n.type in ("object_pattern", "array_pattern"):
                stack = [n]                      # destructured: every name it binds
                while stack:
                    c = stack.pop(0)
                    if c.type in ("identifier", "shorthand_property_identifier_pattern"):
                        flat.append(c)
                    elif c.type != "property_identifier":
                        stack[0:0] = list(c.named_children)
            else:
                flat.append(n)
        for n in flat:
            if n is not None and n.type in ("identifier", "shorthand_property_identifier_pattern"):
                key = data[n.start_byte:n.end_byte].decode("utf-8", "replace")
                if key not in ("self", "cls") and key not in out:
                    out.append(key)
    return out


def tainted_parameters(rows: list[str], span: tuple[int, int], line: int, language: str,
                       params: list[str]) -> list[str]:
    """The enclosing function's parameters that feed the flagged statement
    through the taint slice."""
    if not params:
        return []
    _stmts, _order, tracked = _slice(rows, span, line, language)
    return [p for p in params if p in tracked]


def _function_name_rows(tree, text: str, language: str, name: str) -> set[int]:
    """1-based rows where a function named `name` is declared."""
    types = _function_types(language)
    data = text.encode("utf-8")
    out: set[int] = set()
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type in types:
            nm = n.child_by_field_name("name")
            if nm is not None and data[nm.start_byte:nm.end_byte].decode("utf-8", "replace") == name:
                out.add(nm.start_point[0] + 1)
        stack.extend(n.children)
    return out


def callers(tree, text: str, rows: list[str], language: str, name: str, span: tuple[int, int],
            limits: Limits = LIMITS) -> list[tuple[int, list[int]]]:
    """Up to `limits.callers` same-file call sites of function `name` outside
    its own body, as (call line, lines to show): the calling function when
    it fits in `limits.caller_lines`, else its first lines and a window that
    ends just after the call (the argument's history is above it). One per
    calling function, in file order."""
    if not name or tree is None or limits.callers <= 0:
        return []
    call = re.compile(rf"(?<![\w$]){re.escape(name)}\s*\(")
    decl_rows = _function_name_rows(tree, text, language, name)
    out: list[tuple[int, list[int]]] = []
    seen_spans: set = set()
    cap = max(6, limits.caller_lines)
    for i, ln in enumerate(rows, 1):
        if span[0] <= i <= span[1] or i in decl_rows:
            continue
        flat = re.sub(r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')", "''", ln)
        if not call.search(flat) or re.match(r"^\s*(def|function|func)\b", ln):
            continue
        fn = enclosing_function(tree, text, language, i)
        if fn is not None:
            lo, hi = _decorated_start(fn) + 1, fn.end_point[0] + 1
        else:
            lo, hi = i, i
        if (lo, hi) in seen_spans:
            continue
        seen_spans.add((lo, hi))
        if hi - lo + 1 <= cap:
            lines = list(range(lo, hi + 1))
        else:
            def_row = fn.start_point[0] + 1 if fn is not None else lo
            head = list(range(lo, min(def_row + 1, lo + 3) + 1)) if fn is not None else []
            room = cap - len(head)
            w_hi = min(hi, i + 2)
            w_lo = max(lo, w_hi - room + 1)
            lines = sorted(set(head) | set(range(w_lo, w_hi + 1)))
        out.append((i, lines))
        if len(out) >= limits.callers:
            break
    return out


def all_calls(rows: list[str], span: tuple[int, int]) -> list[str]:
    body = "\n".join(rows[span[0] - 1: span[1]])
    out: list[str] = []
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", body):
        if m.group(1) not in out and m.group(1) not in _KEYWORDS:
            out.append(m.group(1))
    return out


# ---------------------------------------------------------------------------
# Resolving a call to a definition in another file
# ---------------------------------------------------------------------------

def _dir(rel: str) -> str:
    d = str(PurePosixPath(rel).parent)
    return "" if d == "." else d


def _py_module_files(repo: RepoView, module: str, here: str) -> list[str]:
    if module.startswith("."):
        dots = len(module) - len(module.lstrip("."))
        base = PurePosixPath(here).parent
        for _ in range(dots - 1):
            base = base.parent
        parts = [p for p in module.lstrip(".").split(".") if p]
        bases = [base.as_posix()]
    else:
        parts = module.split(".")
        anc = PurePosixPath(here).parent
        bases = []
        while True:
            bases.append(anc.as_posix())
            if anc == anc.parent or anc.as_posix() in (".", ""):
                break
            anc = anc.parent
        bases.append(".")
    out = []
    for b in bases:
        stem = "/".join(([b] if b not in (".", "") else []) + parts)
        for cand in (f"{stem}.py", f"{stem}/__init__.py"):
            if repo.exists(cand) and cand not in out:
                out.append(cand)
        if out:
            break
    return out


def _js_module_files(repo: RepoView, spec: str, here: str) -> list[str]:
    if not spec.startswith("."):
        return []
    base = os.path.normpath(os.path.join(_dir(here) or ".", spec)).replace(os.sep, "/")
    base = base.lstrip("./") if base.startswith("./") else base
    exts = (".ts", ".tsx", ".js", ".mjs", ".cjs", ".jsx")
    stem = re.sub(r"\.(js|mjs|cjs|jsx)$", "", base)
    for cand in [base, *(stem + e for e in exts), *(f"{base}/index{e}" for e in exts)]:
        if repo.exists(cand):
            return [cand]
    return []


def _imports_map(text: str, language: str, here: str, repo: RepoView) -> dict[str, list[str]]:
    """Imported name (or module alias) -> repository files it may come from."""
    out: dict[str, list[str]] = {}
    if language == "python":
        for m in re.finditer(r"^\s*from\s+([.\w]+)\s+import\s+\(?([^)\n]+)", text, re.M):
            files = _py_module_files(repo, m.group(1), here)
            for part in m.group(2).split(","):
                bits = part.strip().split(" as ")
                name = bits[-1].strip()
                if name and name != "*":
                    sub = _py_module_files(repo, f"{m.group(1)}.{bits[0].strip()}"
                                           if not m.group(1).endswith(".") else m.group(1) + bits[0].strip(), here)
                    out.setdefault(name, files + sub)
        for m in re.finditer(r"^\s*import\s+([\w.]+)(?:\s+as\s+(\w+))?", text, re.M):
            out.setdefault(m.group(2) or m.group(1), _py_module_files(repo, m.group(1), here))
    elif language in ("typescript", "javascript"):
        for m in re.finditer(r"import\s+(?:type\s+)?([^'\";]+?)\s+from\s+['\"]([^'\"]+)['\"]", text):
            files = _js_module_files(repo, m.group(2), here)
            for name in re.findall(r"(?:\bas\s+)?([A-Za-z_$][\w$]*)\s*(?=[,}]|$)", m.group(1)):
                out.setdefault(name, files)
            ns = re.search(r"\*\s+as\s+([A-Za-z_$][\w$]*)", m.group(1))
            if ns:
                out[ns.group(1)] = files
            default = re.match(r"\s*([A-Za-z_$][\w$]*)", m.group(1))
            if default:
                out.setdefault(default.group(1), files)
        for m in re.finditer(r"(?:const|let|var)\s+(\{[^}]*\}|[A-Za-z_$][\w$]*)\s*=\s*require\(\s*['\"]([^'\"]+)['\"]",
                             text):
            files = _js_module_files(repo, m.group(2), here)
            for name in _IDENT.findall(m.group(1)):
                out.setdefault(name, files)
    elif language == "java":
        for m in re.finditer(r"^\s*import\s+(static\s+)?([\w.]+)\s*;", text, re.M):
            parts = m.group(2).split(".")
            if m.group(1):   # import static a.b.C.name;
                cls, name = parts[-2], parts[-1]
                suffix = "/".join(parts[:-1]) + ".java"
                out.setdefault(name, [p for p in repo.by_name(cls + ".java") if p.endswith(suffix)])
            else:
                suffix = "/".join(parts) + ".java"
                out.setdefault(parts[-1], [p for p in repo.by_name(parts[-1] + ".java") if p.endswith(suffix)])
    elif language == "go":
        for m in re.finditer(r'^\s*(?:import\s+)?(\w+\s+)?"([\w./-]+)"', text, re.M):
            path = m.group(2)
            alias = (m.group(1) or "").strip() or path.rsplit("/", 1)[-1]
            dirs = sorted(d for d in repo.go_dirs() if d and (path == d or path.endswith("/" + d)))
            if dirs:
                d = max(dirs, key=len)
                out.setdefault(alias, [p for p in repo.in_dir(d) if p.endswith(".go") and not p.endswith("_test.go")])
    elif language == "ruby":
        for m in re.finditer(r"^\s*require_relative\s+['\"]([^'\"]+)['\"]", text, re.M):
            cand = os.path.normpath(os.path.join(_dir(here) or ".", m.group(1))).replace(os.sep, "/")
            cand = cand if cand.endswith(".rb") else cand + ".rb"
            if repo.exists(cand):
                out.setdefault("__required__", []).append(cand)
    return out


def _receiver_type(recv: str, text: str, language: str) -> str | None:
    """The class name a receiver expression refers to, from its declaration
    in this file, or the receiver itself when it names a class."""
    if not recv:
        return None
    m = re.match(r"^(?:new)?([A-Za-z_$][\w$.]*)\(\)$", recv)
    if m:                                    # new Foo().m() / Foo().m()
        return m.group(1).rsplit(".", 1)[-1]
    head = recv.split(".")[0]
    r = re.escape(head)
    pats = {
        "java": [rf"([A-Z][\w.]*)(?:<[^;=()]*>)?(?:\[\])?\s+{r}\s*[=;,)]",
                 rf"\b{r}\s*=\s*new\s+([\w.]+)"],
        "python": [rf"\b{r}\s*=\s*([A-Z]\w*)\s*\(", rf"\b{r}\s*:\s*([A-Z]\w*)"],
        "typescript": [rf"\b{r}\s*=\s*new\s+([\w.]+)", rf"\b{r}\s*:\s*([A-Z][\w.]*)"],
        "javascript": [rf"\b{r}\s*=\s*new\s+([\w.]+)"],
        "ruby": [rf"\b{r}\s*=\s*([A-Z][\w:]*)\.new"],
        "go": [],
    }.get(language, [])
    for p in pats:
        m = re.search(p, text)
        if m:
            return re.split(r"[.:]", m.group(1))[-1]
    last = recv.split(".")[-1]
    if last[:1].isupper() and (len(recv.split(".")) == 1 or language == "java"):
        return last                          # a static call: Helper.m(), a.b.Helper.m()
    return None


def _candidate_files(recv: str, name: str, text: str, language: str, here: str, repo: RepoView,
                     imports: dict[str, list[str]]) -> list[str]:
    """Files where `recv.name(...)` / `name(...)` may be defined, most
    specific first. Empty when nothing ties the call to a repository file:
    a name alone is never enough."""
    exts = _EXTS.get(language, ())
    out: list[str] = []

    def add(files):
        for f in files:
            if f != here and f.endswith(exts) and f not in out:
                out.append(f)

    head = recv.split(".")[0] if recv else ""
    if recv and not recv.endswith("()"):
        if recv in imports:
            add(imports[recv])                       # import a.b; a.b.f()
        elif head in imports:
            add(imports[head])                       # module alias / imported class
        if language == "python" and "." in recv and head in imports:
            add(_py_module_files(repo, recv, here))
    if recv:
        cls = _receiver_type(recv, text, language)
        if cls:
            if cls in imports:
                add(imports[cls])
            if language == "java":
                fq = re.search(rf"([\w.]+\.{re.escape(cls)})\b", text)
                cands = repo.by_name(cls + ".java")
                if fq:
                    suffix = fq.group(1).replace(".", "/") + ".java"
                    add([p for p in cands if p.endswith(suffix)])
                add([p for p in cands if _dir(p) == _dir(here)])
                if len(cands) == 1:
                    add(cands)
            elif language in ("typescript", "javascript", "python", "ruby"):
                same = [p for p in repo.in_dir(_dir(here)) if PurePosixPath(p).stem.lower()
                        in (cls.lower(), re.sub(r"(?<!^)([A-Z])", r"_\1", cls).lower())]
                add(same)
        if language == "go" and not out and head and head not in imports:
            add([p for p in repo.in_dir(_dir(here)) if not p.endswith("_test.go")])
    else:
        if name in imports:
            add(imports[name])
        if language == "java":
            sup = re.search(r"\bextends\s+([A-Z]\w*)", text)
            if sup:
                cands = repo.by_name(sup.group(1) + ".java")
                same = [p for p in cands if _dir(p) == _dir(here)]
                add(same or (cands if len(cands) == 1 else []))
        elif language == "go":
            add([p for p in repo.in_dir(_dir(here)) if not p.endswith("_test.go")])
        elif language == "ruby":
            add(imports.get("__required__", []))
    return out


@dataclass
class Definition:
    path: str          # repository-relative
    lines: list[int]
    rows: list[str]    # that file's lines
    name: str
    receiver: str
    called_at: int


def resolve(recv: str, name: str, text: str, language: str, here: str, repo: RepoView,
            imports: dict[str, list[str]], limits: Limits = LIMITS) -> Definition | None:
    for rel in _candidate_files(recv, name, text, language, here, repo, imports)[:40]:
        other = repo.read(rel)
        if not other:
            continue
        lang = language_of(rel) or language
        span = named_functions(other, rel, lang).get(name)
        if span is None:
            continue
        lo, hi = span
        hi = min(hi, lo + limits.def_lines - 1)
        rows = other.splitlines()
        return Definition(path=rel, lines=list(range(lo, min(hi, len(rows)) + 1)), rows=rows,
                          name=name, receiver=recv, called_at=0)
    return None


# ---------------------------------------------------------------------------
# The excerpt
# ---------------------------------------------------------------------------

@dataclass
class Context:
    text: str                 # rendered and masked
    lines: set                # line numbers shown from the finding's own file
    start: int                # first and last line of the function (or window) shown
    end: int
    path: str
    definitions: list = field(default_factory=list)   # [(repo path, name)] from other files
    callers: list = field(default_factory=list)       # [call line] of same-file callers shown


def _imports(rows: list[str], language: str, stop: int, cap: int) -> list[int]:
    pat = _IMPORT.get(language)
    if pat is None:
        return []
    out = []
    in_go_block = False
    for i, ln in enumerate(rows[: min(stop, 300)], 1):
        if language == "go":
            if re.match(r"^\s*import\s*\($", ln):
                in_go_block = True
                out.append(i)
                continue
            if in_go_block:
                out.append(i)
                if ln.strip() == ")":
                    in_go_block = False
                continue
            if re.match(r"^\s*import\s", ln):
                out.append(i)
            continue
        if pat.match(ln):
            out.append(i)
    return out[:cap]


def _route_lines(rows: list[str], name: str, span: tuple[int, int], cap: int) -> list[int]:
    """Same-file lines outside the function that register it as a handler."""
    if not name:
        return []
    word = re.compile(rf"(?<![\w$.]){re.escape(name)}(?![\w$])")
    out = []
    for i, ln in enumerate(rows, 1):
        if span[0] <= i <= span[1]:
            continue
        if word.search(ln) and _ROUTE.search(ln) and not re.match(r"^\s*(def|function|func)\b", ln):
            out.append(i)
            if len(out) >= cap:
                break
    return out


def build(text: str, path: str, line: int, language: str | None = None, *,
          repo: RepoView | None = None, limits: Limits = LIMITS,
          span: tuple[int, int] | None = None, mask: bool = True) -> Context:
    """The bounded, numbered excerpt for one finding at `path`:`line`.

    `repo` gives access to the rest of the repository (None: this file only);
    `span` overrides the enclosing-function lookup (tests, callers that
    already know it). The flagged line is marked `>>`."""
    language = language or language_of(path)
    rows = text.splitlines()
    if not rows:
        return Context(text="", lines=set(), start=0, end=0, path=path)
    line = min(max(1, line), len(rows))
    tree = parse(text, path, language) if span is None else None
    fn = enclosing_function(tree, text, language, line) if tree is not None else None
    fname = ""
    if span is None and fn is not None:
        span = (_decorated_start(fn) + 1, fn.end_point[0] + 1)
        nm = fn.child_by_field_name("name")
        if nm is not None:
            fname = text.encode("utf-8")[nm.start_byte:nm.end_byte].decode("utf-8", "replace")
    in_fn = span is not None and span[0] <= line <= span[1]

    # 1. the function (signature + decorators kept when it is cut)
    if not in_fn:
        keep = list(range(max(1, line - limits.window), min(len(rows), line + limits.window) + 1))
    else:
        lo, hi = span[0], min(span[1], len(rows))
        cap = max(10, limits.function_lines)
        if hi - lo + 1 > cap:
            def_row = (fn.start_point[0] + 1) if fn is not None else lo
            head = min(def_row - lo + 3, 10)
            half = (cap - head - 1) // 2
            body_lo, body_hi = max(lo, line - half), min(hi, line + half)
            keep = list(range(lo, min(lo + head, body_lo))) + list(range(body_lo, body_hi + 1))
        else:
            keep = list(range(lo, hi + 1))
    shown = set(keep)
    room = limits.total - len(shown)

    def take(nums):
        nonlocal room
        new = [i for i in nums if i not in shown]
        if room <= 0 or not new:
            return
        new = new[:room]
        shown.update(new)
        room -= len(new)

    # 2. class header, 3. imports, 4. route registration
    if fn is not None:
        hdr = _class_header(fn)
        if hdr and hdr[1] < span[0]:
            take(list(range(hdr[0], min(hdr[1], hdr[0] + limits.header_lines - 1) + 1)))
    take([i for i in _imports(rows, language, keep[0], limits.import_lines) if i < keep[0]])
    if in_fn:
        take(_route_lines(rows, fname, span, limits.route_lines))

    defs: list[Definition] = []
    caller_at: list[int] = []
    if in_fn:
        funcs = named_functions(text, path, language, tree)
        enclosing = {n for n, s in funcs.items() if s[0] <= span[0] <= s[1] or s[0] <= line <= s[1]}
        on_path = path_calls(rows, span, line, language)
        path_helpers = [n for _r, n, _a in on_path if n in funcs and n not in enclosing]
        other_helpers = [n for n in all_calls(rows, span) if n in funcs and n not in enclosing]
        helpers = list(dict.fromkeys(path_helpers + other_helpers))[: limits.helpers]
        n_path = len([n for n in helpers if n in path_helpers])

        def helper_lines(n):
            lo, hi = funcs[n]
            return list(range(lo, min(hi, lo + limits.helper_lines - 1) + 1))

        # 5. same-file helpers on the taint path
        for n in helpers[:n_path]:
            take(helper_lines(n))
        # 6. definitions in other files
        if repo is not None:
            imports = _imports_map(text, language, path, repo)
            for recv, name, at in on_path:
                if len(defs) >= limits.defs or room <= 2:
                    break
                if name in funcs and not recv:
                    continue
                if name in funcs and _receiver_type(recv, text, language) in (None, *_local_classes(text)):
                    continue
                d = resolve(recv, name, text, language, path, repo, imports, limits)
                if d is None or any(x.path == d.path and x.name == d.name for x in defs):
                    continue
                d.called_at = at
                d.lines = d.lines[: max(0, room - 1)]
                if not d.lines:
                    break
                defs.append(d)
                room -= len(d.lines) + 1
        # 7. same-file callers, when the value at the line arrives as a parameter
        if fname and tainted_parameters(rows, span, line, language, parameter_names(fn, text)):
            for at, lines in callers(tree, text, rows, language, fname, span, limits):
                if room <= 0:
                    break
                take(lines)
                caller_at.append(at)
        # 8. other same-file helpers
        for n in helpers[n_path:]:
            take(helper_lines(n))

    out, prev = [], None
    for i in sorted(shown):
        if prev is not None and i != prev + 1:
            out.append("      ...")
        mark = ">>" if i == line else "  "
        out.append(f"{mark}{i:>5} {rows[i - 1][:240]}".rstrip())
        prev = i
    for d in defs:
        shown_path = repo.show(d.path) if repo is not None else d.path
        call = f"{d.receiver}.{d.name}" if d.receiver else d.name
        out.append(f"      --- {shown_path}: definition of `{d.name}`, called as `{call}(...)` "
                   f"at line {d.called_at} ---")
        prev = None
        for i in d.lines:
            if prev is not None and i != prev + 1:
                out.append("      ...")
            out.append(f"  {i:>5} {d.rows[i - 1][:240]}".rstrip())
            prev = i
    body = "\n".join(out)
    if mask:
        from .judgement import _mask_secrets
        body = _mask_secrets(body)
    return Context(text=body, lines=set(shown), start=keep[0], end=keep[-1], path=path,
                   definitions=[(d.path, d.name) for d in defs], callers=caller_at)


def _local_classes(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\b(?:class|interface|struct)\s+([A-Za-z_]\w*)", text))
