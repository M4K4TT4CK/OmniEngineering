"""Intraprocedural, one-hop data flow for the language packs (Milestone 3).

## Why

Rule queries match one statement's shape. Code a model writes moves the
same defect around: the SQL built into a variable on the line before, the
request value passed through a small helper, the `"sh"` literal hoisted into
a constant. The mutation tool (`tools/variants.py`) showed injection rules
losing ~30% of their findings to exactly those rewrites. The other way round,
OWASP Benchmark's "safe" cases differ from the real ones only by constant
tricks (the value overwritten with a literal, an always-false branch, a
ternary whose condition is constant arithmetic) that a syntax-only rule
cannot see.

## What it does

For one rule and one parsed file, an abstract interpreter walks each
function that holds a sink (and each function that calls one that does, for
the one hop) statement by statement. Every local carries an abstract value:

    CLEAN    built only from literals (and its constant value when known)
    DERIVED  returned by a call the engine cannot see into whose inputs are
             all CLEAN (`System.getProperty("os.name")`)
    UNKNOWN  a parameter, a field, a call the engine cannot see into
    TAINT    reached from a rule source (a request accessor, a built SQL
             string), through assignments, destructuring, `+=`, string
             concatenation / format / template / f-string / interpolation,
             containers, and calls

Branches join (CLEAN < DERIVED < UNKNOWN < TAINT); a condition that folds to a
constant takes only its live branch, a constant `switch` only its case, a
ternary with a constant condition only its arm. A value reassigned to a
literal is CLEAN from there on. A call to a function defined in the same
file is followed `hops` deep (default 1): arguments bind to parameters, the
return values come back, and sinks inside the helper see the bound values.
A sanitizer (per rule) makes its result CLEAN. Unknown calls pass taint from
their receiver and arguments to their result, the usual taint default.

Two uses, both per rule (`taint:` in the pack, see `lang_rules.py`):

  * new findings: a `sinks` match whose `@sink` value is TAINT (mode
    `source`) or not CLEAN (mode `nonconst`) is reported at its `@hit`;
  * fewer false ones: a hit of the rule's own `query` whose `value`
    captures all evaluate to CLEAN is dropped. Only a provably constant
    value is dropped, so the old query keeps its recall.

Nothing crosses a file, and nothing is learnt about fields, globals other
than file-level literal constants, aliasing or exceptions. A file whose
analysis outgrows its step budget yields no new findings and drops none.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

CLEAN, DERIVED, UNKNOWN, TAINT = 0, 1, 2, 3


class _NoConst:
    __slots__ = ()

    def __repr__(self) -> str:
        return "NOCONST"


NOCONST = _NoConst()
_VIA_CAP = 8


class Budget(Exception):
    """The per-file step budget ran out."""


@dataclass(frozen=True)
class AV:
    """An abstract value. `lit` is the source text of the literal (or
    qualified constant) the value is known to be, for `where:` checks;
    `via` the spans it flowed through, for de-duplicating against the
    rule's own query; `items` a list/map model for constant indexes."""
    kind: int = UNKNOWN
    const: object = NOCONST
    lit: str | None = None
    via: frozenset = frozenset()
    items: tuple | None = None   # ("list", (AV, ...)) | ("map", ((key, AV), ...))
    built: bool = False          # a string spliced together from parts

    def key(self):
        c = self.const if isinstance(self.const, (str, int, float, bool, type(None))) else id(self.const)
        return (self.kind, c, self.lit, self.built)

    def plain(self, via=None) -> AV:
        return AV(self.kind, self.const, self.lit, self.via if via is None else via)


UNK = AV(UNKNOWN)
_FORMAT = re.compile(r"^(format|Sprintf|Sprint|Sprintln|Errorf|printf|sprintf|formatted)$")


def _built(av: AV) -> AV:
    return AV(av.kind, av.const, av.lit, av.via, av.items, av.kind != CLEAN)


def _via(*sets) -> frozenset:
    out: set = set()
    for s in sets:
        out |= s
        if len(out) > _VIA_CAP:
            break
    return frozenset(sorted(out)[:_VIA_CAP]) if len(out) > _VIA_CAP else frozenset(out)


def join(a: AV | None, b: AV | None) -> AV | None:
    if a is None:
        return b
    if b is None or a is b:
        return a
    const = a.const if (a.const is not NOCONST and b.const is not NOCONST
                        and type(a.const) is type(b.const) and a.const == b.const) else NOCONST
    lit = a.lit if a.lit == b.lit else None
    items = a.items if a.items == b.items else None
    if items is None and a.items is not None and b.items is not None and a.items[0] == b.items[0] == "list" \
            and len(a.items[1]) == len(b.items[1]):
        items = ("list", tuple(join(x, y) for x, y in zip(a.items[1], b.items[1])))
    return AV(max(a.kind, b.kind), const, lit, _via(a.via, b.via), items, a.built or b.built)


def mix(avs, via=frozenset()) -> AV:
    """The value derived from several inputs (no constant)."""
    kind = CLEAN
    vias = [via]
    for a in avs:
        kind = max(kind, a.kind)
        vias.append(a.via)
    return AV(kind, NOCONST, None, _via(*vias))


def clean(const=NOCONST, lit=None) -> AV:
    return AV(CLEAN, const, lit)


def span(node) -> tuple[int, int]:
    return (node.start_byte, node.end_byte)


def nkey(node) -> tuple[int, int, str]:
    return (node.start_byte, node.end_byte, node.type)


def text(node) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text is not None else ""


def named(node) -> list:
    return [c for c in node.named_children if c.type not in ("comment", "line_comment", "block_comment")]


# ---------------------------------------------------------------------------
# Per-language node vocabulary
# ---------------------------------------------------------------------------

FUNCS = {
    "python": {"function_definition", "lambda"},
    "javascript": {"function_declaration", "function_expression", "arrow_function",
                   "method_definition", "generator_function_declaration", "function"},
    "go": {"function_declaration", "method_declaration", "func_literal"},
    "java": {"method_declaration", "constructor_declaration", "lambda_expression"},
    "ruby": {"method", "singleton_method", "lambda"},
}
FUNCS["typescript"] = FUNCS["javascript"]

_STRINGS = {"string", "template_string", "interpreted_string_literal", "raw_string_literal",
            "string_literal", "text_block", "concatenated_string", "heredoc_body", "character_literal",
            "simple_symbol", "delimited_symbol", "string_content"}
_INTERP = {"interpolation", "template_substitution"}
_NUMBERS = {"integer", "float", "number", "int_literal", "float_literal", "decimal_integer_literal",
            "hex_integer_literal", "octal_integer_literal", "binary_integer_literal",
            "decimal_floating_point_literal", "imaginary_literal", "rune_literal"}
_TRUE = {"true"}
_FALSE = {"false"}
_NULL = {"none", "null", "nil", "null_literal", "undefined"}
_IDENTS = {"identifier", "shorthand_property_identifier", "instance_variable", "class_variable",
           "global_variable", "constant"}
_PASS = {"parenthesized_expression", "parenthesized_statements", "await_expression", "await",
         "as_expression", "non_null_expression", "satisfies_expression", "type_assertion",
         "type_assertion_expression", "cast_expression", "type_conversion_expression",
         "spread_element", "splat_argument", "list_splat", "expression_list_single", "unary"}
_TERNARY = {"conditional_expression", "ternary_expression", "conditional"}
_MEMBERS = {"attribute", "member_expression", "selector_expression", "field_access", "scope_resolution"}
_SUBSCRIPTS = {"subscript", "subscript_expression", "index_expression", "array_access", "element_reference"}
_LISTS = {"list", "tuple", "array", "array_initializer", "set"}
_MAPS = {"dictionary", "object", "hash"}
_CALLS = {"call", "call_expression", "method_invocation", "new_expression", "object_creation_expression"}
_BINARY = {"binary_operator", "binary_expression", "binary", "boolean_operator", "comparison_operator"}
_UNARY = {"not_operator", "unary_expression", "unary_operator"}
_ASSIGN_EXPR = {"assignment", "assignment_expression", "named_expression", "augmented_assignment",
                "augmented_assignment_expression", "operator_assignment"}
_BLOCKS = {"block", "module", "statement_block", "program", "source_file", "statement_list",
           "body_statement", "then", "do", "begin_block", "class_body", "switch_block",
           "expression_statement", "else_clause", "finally_clause", "ensure", "do_block",
           "block_body", "declaration_list", "export_statement", "labeled_statement",
           "decorated_definition", "with_clause"}
_SKIP_STMT = {"import_statement", "import_from_statement", "import_declaration", "package_clause",
              "comment", "line_comment", "block_comment", "pass_statement", "break_statement",
              "continue_statement", "type_declaration", "interface_declaration",
              "type_alias_declaration", "enum_declaration", "import_spec_list"}

# Calls whose result is as clean as their inputs: string building and
# conversion. Anything else returns UNKNOWN for clean inputs.
_PURE = re.compile(
    r"^(format|Sprintf|Sprint|Sprintln|Errorf|join|Join|valueOf|toString|to_s|to_str|str|String|"
    r"concat|append|substring|substr|slice|trim|strip|lstrip|rstrip|chomp|lower|upper|downcase|"
    r"upcase|toLowerCase|toUpperCase|ToLower|ToUpper|TrimSpace|Trim|replace|replaceAll|gsub|sub|"
    r"split|Split|charAt|getBytes|decode|encode|encodeBase64|decodeBase64|toCharArray|"
    r"StringBuilder|StringBuffer|freeze|dup|intern|Itoa|FormatInt|repeat|Repeat|padStart|padEnd|"
    r"capitalize|title|encodeURIComponent|decodeURIComponent|Quote|toArray|of|asList|asList|"
    r"Path|of|resolve|normalize|Clean|filepath\.Join|path\.Join|Join)$")
# Calls whose result says something about a value without carrying it.
_OPAQUE = re.compile(
    r"^(len|length|size|count|hash|hashCode|equals|equalsIgnoreCase|contains|containsKey|"
    r"startsWith|endsWith|startswith|endswith|isEmpty|empty\?|nil\?|include\?|indexOf|index|"
    r"compare|compareTo|matches|test|has|includes|exists|isinstance|type|typeof|bool|"
    r"HasPrefix|HasSuffix|Contains|Index|EqualFold|any\?|all\?|start_with\?|end_with\?)$")
# Methods that put their arguments into the receiver.
_MUTATE = re.compile(r"^(append|add|addAll|push|unshift|insert|put|putAll|extend|update|write|"
                     r"WriteString|Write|WriteByte|WriteRune|setdefault|concat!|<<|prepend|"
                     r"store|merge!|appendLine|print|printf|println)$")


@dataclass
class Spec:
    """The compiled `taint:` block of one rule, for one file."""
    mode: str = "source"
    sources: set = field(default_factory=set)        # node keys
    source_params: set = field(default_factory=set)  # parameter spans whose value is a source
    sanitizers: set = field(default_factory=set)
    inputs: set = field(default_factory=set)         # always TAINT (request accessors)
    hops: int = 1


# ---------------------------------------------------------------------------
# The interpreter
# ---------------------------------------------------------------------------

class Interp:
    def __init__(self, lang: str, root, spec: Spec, watch: set, budget: int = 400_000):
        self.lang = "typescript" if lang in ("typescript", "javascript") else lang
        self.root = root
        self.spec = spec
        self.watch = watch
        self.seen: dict = {}
        self.steps = budget
        self.funcs = FUNCS.get(lang, set())
        self.by_name: dict[str, list] = {}
        self.class_names: set[str] = set()
        self.consts: dict[str, AV] = {}
        self._memo: dict = {}
        self._stack: list = []
        self._fn_nodes: list = []
        self.depth = 0
        self.ever: dict[str, AV] = {}
        self._index()

    # -- file index ---------------------------------------------------------

    def _index(self) -> None:
        stack = [self.root]
        top_assign: dict[str, list] = {}
        while stack:
            n = stack.pop()
            t = n.type
            if t in self.funcs:
                name = self.func_name(n)
                if name:
                    self.by_name.setdefault(name, []).append(n)
            elif t in ("class_declaration", "class_definition", "class", "type_spec"):
                nm = n.child_by_field_name("name")
                if nm is not None:
                    self.class_names.add(text(nm))
            if self._top_level_const(n):
                for name, val in self._const_bindings(n):
                    top_assign.setdefault(name, []).append(val)
            stack.extend(n.children)
        for name, vals in top_assign.items():
            if len(vals) == 1 and vals[0] is not None:
                v = vals[0]
                av = self._literal(v)
                if av is not None:
                    self.consts[name] = av

    def _top_level_const(self, n) -> bool:
        return n.type in ("assignment", "lexical_declaration", "variable_declaration", "const_declaration",
                          "var_declaration", "field_declaration", "const_spec") and self._outside_funcs(n)

    def _outside_funcs(self, n) -> bool:
        cur = n.parent
        while cur is not None:
            if cur.type in self.funcs:
                return False
            cur = cur.parent
        return True

    def _const_bindings(self, n):
        t = n.type
        if t == "assignment":
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            if left is not None and left.type in ("identifier", "constant"):
                yield text(left), right
        elif t in ("lexical_declaration", "variable_declaration", "field_declaration"):
            for d in named(n):
                if d.type == "variable_declarator":
                    nm, val = d.child_by_field_name("name"), d.child_by_field_name("value")
                    if nm is not None and nm.type == "identifier":
                        yield text(nm), val
        elif t in ("const_declaration", "var_declaration"):
            for spec in named(n):
                if spec.type in ("const_spec", "var_spec"):
                    names = spec.children_by_field_name("name")
                    val = spec.child_by_field_name("value")
                    vals = named(val) if val is not None else []
                    for i, nm in enumerate(names):
                        yield text(nm), (vals[i] if i < len(vals) else None)

    def _literal(self, v) -> AV | None:
        """A file-level constant's value, when it is a plain literal."""
        if v.type in _STRINGS and not any(c.type in _INTERP for c in v.named_children):
            return clean(self.str_const(v), text(v))
        if v.type in _NUMBERS:
            return clean(self.num_const(v), text(v))
        if v.type in _TRUE | _FALSE:
            return clean(v.type in _TRUE, text(v))
        return None

    def func_name(self, fn) -> str | None:
        nm = fn.child_by_field_name("name")
        if nm is not None:
            return text(nm)
        p = fn.parent
        if p is not None and p.type == "variable_declarator":
            nm = p.child_by_field_name("name")
            return text(nm) if nm is not None and nm.type == "identifier" else None
        if p is not None and p.type in ("pair", "assignment_expression"):
            k = p.child_by_field_name("key") or p.child_by_field_name("left")
            return text(k).split(".")[-1] if k is not None else None
        return None

    # -- bookkeeping --------------------------------------------------------

    def tick(self) -> None:
        self.steps -= 1
        if self.steps <= 0:
            raise Budget()

    def note(self, node, av: AV) -> AV:
        k = nkey(node)
        if k in self.watch:
            self.seen[k] = join(self.seen.get(k), av)
        return av

    # -- functions ----------------------------------------------------------

    def params(self, fn) -> list:
        """(name, node) per positional parameter; destructured ones carry
        their pattern node instead of a name."""
        ps = fn.child_by_field_name("parameters") or fn.child_by_field_name("parameter")
        if ps is None:
            # ruby lambda `->(x) {}` / block parameters
            for c in fn.named_children:
                if c.type in ("lambda_parameters", "block_parameters", "method_parameters"):
                    ps = c
                    break
        if ps is None:
            return []
        if ps.type == "identifier":
            return [(text(ps), ps)]
        out = []
        for p in named(ps):
            t = p.type
            if t == "identifier":
                out.append((text(p), p))
            elif t in ("typed_parameter", "list_splat_pattern", "dictionary_splat_pattern",
                       "splat_parameter", "hash_splat_parameter", "block_parameter"):
                ids = [c for c in p.named_children if c.type == "identifier"]
                if ids:
                    out.append((text(ids[0]), p))
            elif t in ("default_parameter", "typed_default_parameter", "optional_parameter",
                       "keyword_parameter", "formal_parameter", "spread_parameter"):
                nm = p.child_by_field_name("name")
                if nm is None:
                    ids = [c for c in p.named_children if c.type == "identifier"]
                    nm = ids[-1] if ids else None
                if nm is not None:
                    out.append((text(nm), p))
            elif t in ("required_parameter", "optional_parameter"):
                pat = p.child_by_field_name("pattern")
                if pat is not None:
                    out.append((text(pat) if pat.type == "identifier" else None, pat if pat.type != "identifier" else p))
            elif t == "assignment_pattern":
                left = p.child_by_field_name("left")
                if left is not None:
                    out.append((text(left), p))
            elif t in ("object_pattern", "array_pattern"):
                out.append((None, p))
            elif t == "parameter_declaration" or t == "variadic_parameter_declaration":
                for nm in p.children_by_field_name("name"):
                    out.append((text(nm), p))
            elif t == "receiver_parameter":
                continue
        return out

    def body(self, fn):
        return fn.child_by_field_name("body")

    def enter_env(self, fn, args: list[AV] | None, kwargs: dict | None = None) -> dict:
        env: dict[str, AV] = {}
        ps = self.params(fn)
        if args is not None and ps and ps[0][0] in ("self", "cls") and len(args) == len(ps) - 1:
            ps = ps[1:]
        for i, (name, node) in enumerate(ps):
            if args is not None:
                av = args[i] if i < len(args) else (kwargs or {}).get(name, UNK)
                if kwargs and name in kwargs:
                    av = kwargs[name]
            else:
                av = AV(TAINT, via=frozenset([span(node)])) if self._is_source_param(node) else UNK
            if name is None:
                self.bind(node, av, env)
            else:
                env[name] = av
        return env

    def _is_source_param(self, node) -> bool:
        s, e = span(node)
        return any(s <= a and b <= e for a, b in self.spec.source_params)

    def run_function(self, fn, args=None, kwargs=None) -> AV:
        """Interpret `fn`; returns the join of what it returns."""
        env = self.enter_env(fn, args, kwargs)
        self._stack.append(fn.id)
        self._fn_nodes.append(fn)
        try:
            body = self.body(fn)
            returns: list = []
            if body is None:
                return UNK
            if fn.type == "arrow_function" and body.type != "statement_block":
                return self.eval(body, env)
            if fn.type == "lambda" and self.lang == "python":
                return self.eval(body, env)
            out, last = self.exec_body(body, env, returns)
            if self.lang == "ruby" and last is not None:
                returns.append(last)
            res = None
            for r in returns:
                res = join(res, r)
            return res if res is not None else clean(None)
        finally:
            self._stack.pop()
            self._fn_nodes.pop()

    def call_same_file(self, call, name, args, kwargs, receiver_ok: bool) -> AV | None:
        if self.depth >= self.spec.hops or not receiver_ok:
            return None
        cands = self.by_name.get(name)
        if not cands:
            return None
        fit = [f for f in cands if self._arity_fits(f, len(args))]
        if not fit or len(fit) > 4:
            return None
        if any(f.id in self._stack for f in fit):
            return None
        res = None
        for f in fit:
            key = (f.id, tuple((a.key(), a.via) for a in args),
                   tuple(sorted((k, v.key(), v.via) for k, v in kwargs.items())))
            if key in self._memo:
                r = self._memo[key]
            else:
                self.depth += 1
                try:
                    r = self.run_function(f, args, kwargs)
                finally:
                    self.depth -= 1
                self._memo[key] = r
            res = join(res, r)
        return res

    def _arity_fits(self, fn, n: int) -> bool:
        ps = self.params(fn)
        k = len(ps)
        if k and ps[0][0] in ("self", "cls"):
            return n in (k, k - 1) or n <= k
        return n <= k or any(p.type in ("list_splat_pattern", "splat_parameter", "rest_pattern",
                                        "spread_parameter", "variadic_parameter_declaration")
                             for _, p in ps)

    # -- statements ---------------------------------------------------------

    def exec_body(self, node, env, returns) -> tuple[dict | None, AV | None]:
        """Run the statements of a block; returns (env or None if every path
        returned, value of the last statement for Ruby)."""
        last = None
        kids = named(node) if node.type in _BLOCKS or node.type in ("else", "when", "ensure") else [node]
        for c in kids:
            if env is None:
                break
            env, last = self.exec(c, env, returns)
        return env, last

    def exec(self, node, env, returns) -> tuple[dict | None, AV | None]:
        self.tick()
        t = node.type
        if t in _SKIP_STMT:
            return env, None
        if t in self.funcs:
            self.run_nested(node, env)
            return env, None
        if t in _BLOCKS:
            return self.exec_body(node, env, returns)
        if t in ("class_declaration", "class_definition", "class", "module", "singleton_class"):
            b = node.child_by_field_name("body")
            if b is not None:
                self.exec_body(b, dict(env), [])
            return env, None
        if t in ("return_statement", "return", "yield_statement"):
            vals = [c for c in named(node)]
            if len(vals) == 1 and vals[0].type in ("expression_list", "argument_list") \
                    and len(named(vals[0])) > 1:
                vals = named(vals[0])
            av = None
            if not vals and self.lang == "go" and self._fn_nodes:
                res = self._fn_nodes[-1].child_by_field_name("result")
                names = [text(n) for p in (named(res) if res is not None and res.type == "parameter_list" else [])
                         for n in p.children_by_field_name("name")]
                if names:
                    av = self.mk_list([env.get(n, clean(None)) for n in names]) if len(names) > 1 \
                        else env.get(names[0], clean(None))
            if len(vals) > 1:
                av = self.mk_list([self.eval(v, env) for v in vals])
            for v in vals[:1]:
                av = av if av is not None else self.eval(v, env)
            returns.append(av if av is not None else clean(None))
            return None, None
        if t in ("throw_statement", "raise_statement", "raise"):
            for v in named(node):
                self.eval(v, env)
            return None, None
        if t in ("if_statement", "if", "unless", "elsif", "elif_clause"):
            return self.exec_if(node, env, returns), None
        if t in ("if_modifier", "unless_modifier"):
            cond = self.eval(node.child_by_field_name("condition"), env)
            truth = self.truth(cond)
            if t == "unless_modifier" and truth is not None:
                truth = not truth
            if truth is False:
                return env, None
            b = node.child_by_field_name("body")
            e2, last = self.exec(b, dict(env), returns) if b is not None else (env, None)
            return (e2 if truth else self.join_env(env, e2)), last
        if t in ("for_statement", "for_in_statement", "enhanced_for_statement", "while_statement",
                 "do_statement", "while", "until", "for", "while_modifier", "until_modifier",
                 "for_clause", "range_clause"):
            return self.exec_loop(node, env, returns), None
        if t in ("switch_statement", "switch_expression", "expression_switch_statement", "case",
                 "match_statement", "type_switch_statement", "select_statement"):
            return self.exec_switch(node, env, returns), None
        if t in ("try_statement", "try_with_resources_statement", "begin", "with_statement",
                 "try_expression"):
            return self.exec_try(node, env, returns), None
        if t in ("local_variable_declaration", "lexical_declaration", "variable_declaration",
                 "field_declaration"):
            for d in named(node):
                if d.type == "variable_declarator":
                    nm, val = d.child_by_field_name("name"), d.child_by_field_name("value")
                    if val is not None:
                        av = self.eval(val, env)
                        if nm is not None:
                            self.bind(nm, self._via_def(av, val), env)
                    elif nm is not None and nm.type == "identifier":
                        env[text(nm)] = clean(None)
            return env, None
        if t in ("short_var_declaration", "assignment_statement"):
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            op = node.child_by_field_name("operator")
            self.assign_lists(left, right, env, text(op) if op is not None else "=")
            return env, None
        if t in ("var_declaration", "const_declaration"):
            for spec in named(node):
                if spec.type in ("var_spec", "const_spec"):
                    names = spec.children_by_field_name("name")
                    val = spec.child_by_field_name("value")
                    vals = named(val) if val is not None else []
                    avs = [self.eval(v, env) for v in vals]
                    for i, nm in enumerate(names):
                        if i < len(avs):
                            env[text(nm)] = self._via_def(avs[i], vals[i])
                        elif len(avs) == 1:
                            env[text(nm)] = avs[0].plain()
                        else:
                            env[text(nm)] = clean(None) if not vals else UNK
            return env, None
        if t in ("inc_statement", "dec_statement"):
            return env, None
        if t in ("defer_statement", "go_statement", "synchronized_statement", "global_statement",
                 "nonlocal_statement", "delete_statement", "assert_statement", "print_statement",
                 "exec_statement", "send_statement"):
            for c in named(node):
                if c.type in ("block",):
                    env, _ = self.exec(c, env, returns)
                else:
                    self.eval(c, env)
            return env, None
        # Anything else is an expression evaluated for its effects.
        av = self.eval(node, env)
        return env, av

    def _via_def(self, av: AV, val) -> AV:
        return AV(av.kind, av.const, av.lit, _via(av.via, frozenset([span(val)])), av.items, av.built)

    def run_nested(self, fn, env) -> None:
        """A function defined inside the one being run: its body runs once,
        here, on a copy of the surrounding locals (closures read them)."""
        inner = dict(env)
        inner.update(self.enter_env(fn, None))
        body = self.body(fn)
        if body is None:
            return
        self._stack.append(fn.id)
        try:
            if body.type in _BLOCKS or body.type in ("block",):
                self.exec_body(body, inner, [])
            else:
                self.eval(body, inner)
        finally:
            self._stack.pop()

    def join_env(self, a, b):
        if a is None:
            return None if b is None else dict(b)
        if b is None:
            return dict(a)
        out = dict(a)
        for k, v in b.items():
            out[k] = join(out.get(k), v) if k in out else join(UNK if k not in a else None, v)
        for k in a:
            if k not in b:
                out[k] = join(a[k], UNK) if False else a[k]
        return out

    def truth(self, av: AV | None):
        if av is None or av.kind != CLEAN or av.const is NOCONST:
            return None
        c = av.const
        if self.lang == "ruby":
            return not (c is None or c is False)
        try:
            return bool(c)
        except Exception:  # noqa: BLE001
            return None

    def exec_if(self, node, env, returns):
        cond_n = node.child_by_field_name("condition")
        init = node.child_by_field_name("initializer")
        if init is not None:
            env, _ = self.exec(init, env, returns)
            if env is None:
                return None
        truth = self.truth(self.eval(cond_n, env)) if cond_n is not None else None
        if node.type == "unless" and truth is not None:
            truth = not truth
        cons = node.child_by_field_name("consequence")
        alts = node.children_by_field_name("alternative")
        if node.type == "if_statement" and self.lang == "python":
            alts = [c for c in node.named_children if c.type in ("elif_clause", "else_clause")]
        a_env = None
        if truth is not False:
            a_env = self.run_branch(cons, dict(env), returns) if cons is not None else dict(env)
        b_env = None
        if truth is not True:
            if alts:
                b_env = self.run_alts(alts, dict(env), returns)
            else:
                b_env = dict(env)
        if truth is True:
            return a_env
        if truth is False:
            return b_env
        return self.join_env(a_env, b_env)

    def run_alts(self, alts, env, returns):
        first = alts[0]
        if first.type in ("elif_clause", "elsif"):
            # the rest of the chain hangs off the clause (ruby) or follows it (python)
            if self.lang == "python":
                cond = self.truth(self.eval(first.child_by_field_name("condition"), env))
                cons = first.child_by_field_name("consequence")
                a = self.run_branch(cons, dict(env), returns) if cond is not False and cons is not None else None
                b = (self.run_alts(alts[1:], dict(env), returns) if alts[1:] else dict(env)) \
                    if cond is not True else None
                return a if cond is True else b if cond is False else self.join_env(a, b)
            return self.exec_if(first, env, returns)
        return self.run_branch(first, env, returns)

    def run_branch(self, node, env, returns):
        if node is None:
            return env
        if node.type in ("else_clause", "else") :
            b = node.child_by_field_name("body")
            if b is not None:
                return self.exec(b, env, returns)[0]
            return self.exec_body(node, env, returns)[0]
        return self.exec(node, env, returns)[0]

    def exec_loop(self, node, env, returns):
        t = node.type
        cond = node.child_by_field_name("condition")
        if t in ("while_statement", "while", "until") and cond is not None:
            truth = self.truth(self.eval(cond, env))
            if t == "until" and truth is not None:
                truth = not truth
            if truth is False:
                return env
        body = node.child_by_field_name("body")
        e = dict(env)
        # the loop variable
        if t in ("for_in_statement", "for_statement", "for") and self.lang in ("python", "typescript", "ruby"):
            left = node.child_by_field_name("left") or node.child_by_field_name("pattern")
            right = node.child_by_field_name("right") or node.child_by_field_name("value")
            if left is not None and right is not None:
                self.bind(left, self.element_of(self.eval(right, e)), e)
        elif t == "enhanced_for_statement":
            nm, val = node.child_by_field_name("name"), node.child_by_field_name("value")
            if nm is not None and val is not None:
                e[text(nm)] = self.element_of(self.eval(val, e))
        elif t == "for_statement" and self.lang == "go":
            for c in named(node):
                if c.type == "range_clause":
                    left, right = c.child_by_field_name("left"), c.child_by_field_name("right")
                    if right is not None:
                        el = self.element_of(self.eval(right, e))
                        if left is not None:
                            for nm in named(left):
                                self.bind(nm, el, e)
                elif c.type == "for_clause":
                    init = c.child_by_field_name("initializer")
                    if init is not None:
                        e = self.exec(init, e, returns)[0] or e
        elif t == "for_statement" and self.lang in ("java", "typescript"):
            for f in ("init", "initializer"):
                for init in node.children_by_field_name(f):
                    e = self.exec(init, e, returns)[0] or e
        if body is None:
            # Python/JS keep the body under `body`; ruby `while` under `body` (do)
            return self.join_env(env, e)
        once = self.exec(body, dict(e), returns)[0]
        merged = self.join_env(e, once)
        if merged is not None and once is not None and any(merged.get(k) != e.get(k) for k in merged):
            twice = self.exec(body, dict(merged), returns)[0]
            merged = self.join_env(merged, twice)
        return self.join_env(env, merged)

    def exec_switch(self, node, env, returns):
        subj = node.child_by_field_name("value") or node.child_by_field_name("condition") \
            or node.child_by_field_name("subject")
        init = node.child_by_field_name("initializer")
        if init is not None:
            env = self.exec(init, env, returns)[0] or env
        sv = self.eval(subj, env) if subj is not None else None
        cases = self.switch_cases(node)
        if not cases:
            for c in named(node):
                if c is not subj:
                    self.eval(c, env)
            return env
        known = sv is not None and sv.kind == CLEAN and sv.const is not NOCONST
        if known:
            start = None
            for i, (labels, _stmts, is_default) in enumerate(cases):
                vals = [self.eval(lb, env) for lb in labels]
                if any(v.kind == CLEAN and v.const is not NOCONST and v.const == sv.const for v in vals):
                    start = i
                    break
            if start is None:
                for i, (_l, _s, d) in enumerate(cases):
                    if d:
                        start = i
                        break
            if start is None:
                return env
            if all(all(v.kind == CLEAN and v.const is not NOCONST for v in (self.eval(lb, env) for lb in labels))
                   for labels, _s, d in cases if not d):
                e = dict(env)
                for labels, stmts, _d in cases[start:]:
                    stop = False
                    for st in stmts:
                        if st.type in ("break_statement", "break"):
                            stop = True
                            break
                        e, _ = self.exec(st, e, returns)
                        if e is None:
                            return None
                    if stop or self.lang in ("go", "ruby", "python") or node.type == "switch_expression" \
                            and any(s.type == "switch_rule" for s in named(node.child_by_field_name("body") or node)):
                        break
                return e
        out = None
        has_default = False
        for labels, stmts, is_default in cases:
            has_default = has_default or is_default
            for lb in labels:
                self.eval(lb, env)
            e = dict(env)
            for st in stmts:
                if st.type in ("break_statement", "break"):
                    break
                e, _ = self.exec(st, e, returns)
                if e is None:
                    break
            out = self.join_env(out, e) if out is not None or e is not None else None
        if not has_default:
            out = self.join_env(out, env)
        return out

    def switch_cases(self, node):
        """[(label nodes, statement nodes, is_default)] for every language."""
        out = []
        body = node.child_by_field_name("body")
        holder = body if body is not None else node
        for c in named(holder):
            t = c.type
            if t in ("switch_case", "switch_default"):
                labels = c.children_by_field_name("value")
                stmts = c.children_by_field_name("body")
                out.append((labels, stmts, t == "switch_default"))
            elif t in ("expression_case", "default_case", "type_case", "communication_case"):
                val = c.child_by_field_name("value")
                labels = named(val) if val is not None and t == "expression_case" else []
                stmts = [s for s in named(c) if s is not val and s.type != "expression_list"]
                if len(stmts) == 1 and stmts[0].type == "statement_list":
                    stmts = named(stmts[0])
                out.append((labels, stmts, t == "default_case"))
            elif t in ("switch_block_statement_group", "switch_rule"):
                labels, stmts, dflt = [], [], False
                for s in named(c):
                    if s.type == "switch_label":
                        ls = named(s)
                        labels.extend(ls)
                        dflt = dflt or not ls
                    else:
                        stmts.append(s)
                out.append((labels, stmts, dflt))
            elif t == "when":
                labels = [p for p in c.children_by_field_name("pattern")]
                labels = [named(p)[0] if p.type == "pattern" and named(p) else p for p in labels]
                b = c.child_by_field_name("body")
                out.append((labels, [b] if b is not None else [], False))
            elif t == "else" and node.type == "case":
                out.append(([], [c], True))
            elif t == "case_clause":
                pats = [p for p in named(c) if p.type == "case_pattern"]
                cons = c.child_by_field_name("consequence")
                is_d = any(text(p).strip() == "_" for p in pats)
                out.append(([named(p)[0] for p in pats if named(p) and not is_d], [cons] if cons else [], is_d))
        return out

    def exec_try(self, node, env, returns):
        t = node.type
        if t == "with_statement":
            for c in named(node):
                if c.type == "with_clause":
                    for item in named(c):
                        val = item.child_by_field_name("value")
                        if val is not None:
                            if val.type == "as_pattern":
                                src = named(val)[0] if named(val) else None
                                av = self.eval(src, env) if src is not None else UNK
                                alias = val.child_by_field_name("alias")
                                if alias is not None:
                                    self.bind(named(alias)[0] if named(alias) else alias, av.plain(), env)
                            else:
                                self.eval(val, env)
            b = node.child_by_field_name("body")
            return self.exec(b, env, returns)[0] if b is not None else env
        res = node.child_by_field_name("resources")
        if res is not None:
            for r in named(res):
                nm, val = r.child_by_field_name("name"), r.child_by_field_name("value")
                if val is not None:
                    av = self.eval(val, env)
                    if nm is not None:
                        env[text(nm)] = av
        b = node.child_by_field_name("body")
        main = self.exec(b, dict(env), returns)[0] if b is not None else dict(env)
        if t == "begin":
            main = self.exec_body(node, dict(env), returns)[0]
        out = main
        handlers = [c for c in named(node) if c.type in ("catch_clause", "except_clause", "rescue")]
        for h in handlers:
            hb = h.child_by_field_name("body")
            start = self.join_env(env, main)
            if hb is None:
                hb_kids = [k for k in named(h) if k.type in ("block", "statement_block", "then")]
                hb = hb_kids[-1] if hb_kids else None
            if hb is not None and start is not None:
                out = self.join_env(out, self.exec(hb, dict(start), returns)[0])
        for c in named(node):
            if c.type in ("finally_clause", "ensure", "else_clause") and out is not None:
                fb = c.child_by_field_name("body") or c
                out = self.exec(fb, out, returns)[0] if fb is not c else self.exec_body(c, out, returns)[0]
        return out

    # -- binding ------------------------------------------------------------

    def bind(self, target, av: AV, env) -> None:
        t = target.type
        if t in ("identifier", "instance_variable", "class_variable", "global_variable", "constant",
                 "shorthand_property_identifier_pattern"):
            env[text(target)] = av
            self.ever[text(target)] = join(self.ever.get(text(target)), av)
        elif t in _MEMBERS:
            env[text(target)] = av
        elif t in _SUBSCRIPTS:
            obj = (target.child_by_field_name("value") or target.child_by_field_name("object")
                   or target.child_by_field_name("operand") or target.child_by_field_name("array")
                   or (named(target)[0] if named(target) else None))
            idx = [c for c in named(target) if c is not obj]
            if obj is not None and obj.type in _IDENTS | _MEMBERS:
                name = text(obj)
                cur = env.get(name)
                k = self.eval(idx[0], env) if idx else UNK
                env[name] = self.container_set(cur, k, av)
        elif t in ("pattern_list", "tuple_pattern", "list_pattern", "expression_list", "array_pattern",
                   "left_assignment_list", "destructured_left_assignment", "tuple", "list",
                   "object_pattern", "rest_pattern", "list_splat_pattern", "splat_parameter",
                   "rest_assignment"):
            el = av.plain()
            el = AV(el.kind, NOCONST, None, el.via)
            if av.items is not None and av.items[0] == "list" and t in ("pattern_list", "tuple_pattern",
                                                                       "list_pattern", "array_pattern",
                                                                       "left_assignment_list"):
                parts = named(target)
                vals = av.items[1]
                if len(parts) == len(vals):
                    for p, v in zip(parts, vals):
                        self.bind(p, v, env)
                    return
            for c in named(target):
                self.bind(c, el, env)
        elif t == "pair_pattern":
            v = target.child_by_field_name("value")
            if v is not None:
                self.bind(v, AV(av.kind, NOCONST, None, av.via), env)
        elif t in ("object_assignment_pattern", "assignment_pattern"):
            left, right = target.child_by_field_name("left"), target.child_by_field_name("right")
            if left is not None:
                dflt = self.eval(right, env) if right is not None else clean()
                self.bind(left, join(AV(av.kind, NOCONST, None, av.via), dflt), env)
        elif t in ("required_parameter", "optional_parameter"):
            pat = target.child_by_field_name("pattern")
            if pat is not None:
                self.bind(pat, av, env)
        elif t in ("parenthesized_expression",) and named(target):
            self.bind(named(target)[0], av, env)

    def assign_lists(self, left, right, env, op: str) -> None:
        lefts = named(left) if left is not None and left.type == "expression_list" else [left]
        rights = named(right) if right is not None and right.type == "expression_list" else [right]
        avs = [self.eval(r, env) if r is not None else UNK for r in rights]
        if len(lefts) > 1 and len(avs) == 1 and avs[0].items is not None and avs[0].items[0] == "list" \
                and len(avs[0].items[1]) == len(lefts):
            avs, rights = list(avs[0].items[1]), [rights[0]] * len(lefts)
        for i, lt in enumerate(lefts):
            if lt is None:
                continue
            if len(avs) == len(lefts):
                av = self._via_def(avs[i], rights[i])
            elif avs:
                av = AV(avs[0].kind, NOCONST, None, _via(avs[0].via, frozenset([span(rights[0])])))
            else:
                av = UNK
            if op not in ("=", ":="):
                av = self.compound(op, self.eval(lt, env), av)
            if text(lt) == "_":
                continue
            self.bind(lt, av, env)

    def compound(self, op: str, cur: AV, rhs: AV) -> AV:
        base = op[:-1]
        if base in ("+", "-", "*", "/", "%", "<<", "||", "&&", "|", "&", "//"):
            folded = self.fold(base, cur, rhs)
            if folded is not None:
                return folded
        return mix([cur, rhs])

    # -- containers ---------------------------------------------------------

    def element_of(self, av: AV) -> AV:
        if av.items is not None:
            vals = av.items[1] if av.items[0] == "list" else [v for _k, v in av.items[1]]
            out = None
            for v in vals:
                out = join(out, v)
            return AV(out.kind, NOCONST, None, out.via) if out is not None else clean()
        return AV(av.kind, NOCONST, None, av.via)

    def container_set(self, cur: AV | None, k: AV, v: AV) -> AV:
        if cur is not None and cur.items is not None and cur.items[0] == "map" \
                and k.kind == CLEAN and k.const is not NOCONST:
            pairs = [(kk, vv) for kk, vv in cur.items[1] if kk != k.const] + [(k.const, v)]
            return self.mk_map(pairs)
        if cur is not None and cur.items is not None and cur.items[0] == "list" \
                and k.kind == CLEAN and isinstance(k.const, int) and 0 <= k.const < len(cur.items[1]):
            vals = list(cur.items[1])
            vals[k.const] = v
            return self.mk_list(vals)
        base = cur if cur is not None else UNK
        return AV(max(base.kind, v.kind), NOCONST, None, _via(base.via, v.via))

    def mk_list(self, vals) -> AV:
        vals = tuple(vals)
        kind = max([v.kind for v in vals], default=CLEAN)
        return AV(kind, NOCONST, None, _via(*[v.via for v in vals]), ("list", vals))

    def mk_map(self, pairs) -> AV:
        pairs = tuple(pairs)
        kind = max([v.kind for _k, v in pairs], default=CLEAN)
        return AV(kind, NOCONST, None, _via(*[v.via for _k, v in pairs]), ("map", pairs))

    def container_get(self, cur: AV, k: AV) -> AV:
        if cur.items is not None and k.kind == CLEAN and k.const is not NOCONST:
            kind, vals = cur.items
            if kind == "map":
                for kk, vv in vals:
                    if kk == k.const:
                        return vv
                return clean(None)
            if kind == "list" and isinstance(k.const, int) and not isinstance(k.const, bool):
                i = k.const
                if -len(vals) <= i < len(vals):
                    return vals[i]
        return self.element_of(cur) if cur.items is not None else AV(cur.kind, NOCONST, None, cur.via)

    # -- expressions --------------------------------------------------------

    def eval(self, node, env) -> AV:
        if node is None:
            return UNK
        self.tick()
        k = nkey(node)
        if k in self.spec.sanitizers:
            for c in named(node):
                self.eval(c, env)
            return self.note(node, clean())
        if k in self.spec.inputs:
            inner = self._eval(node, env)
            return self.note(node, AV(TAINT, NOCONST, None, _via(inner.via, frozenset([span(node)]))))
        if k in self.spec.sources:
            inner = self._eval(node, env)
            if inner.kind == CLEAN or (inner.built and inner.kind <= DERIVED):
                return self.note(node, inner)
            return self.note(node, AV(TAINT, NOCONST, None, _via(inner.via, frozenset([span(node)]))))
        return self.note(node, self._eval(node, env))

    def _eval(self, node, env) -> AV:
        t = node.type
        if t in _NUMBERS:
            return clean(self.num_const(node), text(node))
        if t in _TRUE:
            return clean(True, text(node))
        if t in _FALSE:
            return clean(False, text(node))
        if t in _NULL:
            return clean(None, text(node))
        if t in _STRINGS or t in ("subshell", "heredoc_beginning", "string_array", "symbol_array",
                                   "regex", "regex_literal"):
            return self.eval_string(node, env)
        if t in _IDENTS or t in ("this", "self", "field_identifier", "property_identifier"):
            return self.lookup(node, env)
        if t in self.funcs:
            self.run_nested(node, env)
            return UNK
        if t in _PASS:
            kids = named(node)
            if t in ("cast_expression", "type_conversion_expression"):
                v = node.child_by_field_name("value") or node.child_by_field_name("operand")
                kids = [v] if v is not None else kids[-1:]
            elif t in ("as_expression", "satisfies_expression", "type_assertion_expression"):
                kids = kids[:1]
            elif t == "type_assertion":
                kids = kids[-1:]
            elif t == "unary":
                op = node.child_by_field_name("operator")
                operand = node.child_by_field_name("operand")
                if operand is not None:
                    v = self.eval(operand, env)
                    return self.fold_unary(text(op) if op is not None else "", v)
            if not kids:
                return UNK
            out = None
            for c in kids:
                out = self.eval(c, env)
            return out
        if t in _TERNARY:
            cond = node.child_by_field_name("condition")
            cons = node.child_by_field_name("consequence")
            alt = node.child_by_field_name("alternative")
            if t == "conditional_expression":
                kids = named(node)
                if len(kids) == 3:
                    cons, cond, alt = kids
            truth = self.truth(self.eval(cond, env))
            if truth is True:
                return self.eval(cons, env)
            if truth is False:
                return self.eval(alt, env)
            return join(self.eval(cons, env), self.eval(alt, env)) or UNK
        if t in _ASSIGN_EXPR:
            return self.eval_assign(node, env)
        if t in _BINARY:
            return self.eval_binary(node, env)
        if t in _UNARY:
            arg = node.child_by_field_name("argument") or node.child_by_field_name("operand")
            if arg is None and named(node):
                arg = named(node)[-1]
            op = node.child_by_field_name("operator")
            v = self.eval(arg, env)
            return self.fold_unary(text(op) if op is not None else ("not" if t == "not_operator" else ""), v)
        if t in _CALLS:
            return self.eval_call(node, env)
        if t in _MEMBERS:
            return self.eval_member(node, env)
        if t in _SUBSCRIPTS:
            return self.eval_subscript(node, env)
        if t in _LISTS or t == "argument_list":
            vals = [self.eval(c, env) for c in named(node) if c.type != "block_argument"]
            return self.mk_list(vals)
        if t in ("array_creation_expression",):
            v = node.child_by_field_name("value")
            return self.eval(v, env) if v is not None else clean()
        if t in _MAPS:
            pairs = []
            known = True
            for c in named(node):
                if c.type == "pair":
                    kn, vn = c.child_by_field_name("key"), c.child_by_field_name("value")
                    kv = self.key_const(kn, env)
                    vv = self.eval(vn, env)
                    if kv is NOCONST:
                        known = False
                        pairs.append((object(), vv))
                    else:
                        pairs.append((kv, vv))
                elif c.type == "shorthand_property_identifier":
                    pairs.append((text(c), self.lookup(c, env)))
                else:
                    known = False
                    pairs.append((object(), self.eval(c, env)))
            m = self.mk_map(pairs)
            return m if known else AV(m.kind, NOCONST, None, m.via)
        if t == "composite_literal":
            b = node.child_by_field_name("body")
            pairs, vals = [], []
            if b is not None:
                for el in named(b):
                    if el.type == "keyed_element":
                        kids = named(el)
                        if len(kids) == 2:
                            kv = self.key_const(kids[0], env)
                            vv = self.eval(kids[1], env)
                            pairs.append((kv if kv is not NOCONST else object(), vv))
                    else:
                        vals.append(self.eval(el, env))
            if pairs and not vals:
                return self.mk_map(pairs)
            return self.mk_list(vals) if vals else mix([v for _k, v in pairs])
        if t in ("literal_element", "keyed_element", "argument", "pair", "expression_list",
                 "template_substitution", "interpolation", "keyword_argument"):
            out = None
            for c in named(node):
                out = self.eval(c, env)
            if t == "expression_list":
                avs = [self.eval(c, env) for c in named(node)]
                return mix(avs) if len(avs) != 1 else avs[0]
            return out or UNK
        if t in ("list_comprehension", "generator_expression", "dictionary_comprehension",
                 "set_comprehension"):
            inner = dict(env)
            for c in named(node):
                if c.type == "for_in_clause":
                    left, right = c.child_by_field_name("left"), c.child_by_field_name("right")
                    if left is not None and right is not None:
                        self.bind(left, self.element_of(self.eval(right, inner)), inner)
            b = node.child_by_field_name("body")
            v = self.eval(b, inner) if b is not None else UNK
            return AV(v.kind, NOCONST, None, v.via)
        if t in ("block", "do_block", "statement_block"):
            self.exec_body(node, dict(env), [])
            return UNK
        # Unknown expression: evaluate the parts (sinks may sit inside), and
        # let taint through.
        avs = []
        for c in named(node):
            if c.type in _BLOCKS or c.type in ("block", "do_block"):
                self.exec(c, dict(env), [])
            else:
                avs.append(self.eval(c, env))
        if avs and max(a.kind for a in avs) == TAINT:
            return mix(avs)
        return AV(UNKNOWN, NOCONST, None, _via(*[a.via for a in avs]))

    def key_const(self, kn, env):
        if kn is None:
            return NOCONST
        if kn.type in ("property_identifier", "identifier") and self.lang in ("typescript",):
            return text(kn)
        if kn.type in ("hash_key_symbol", "simple_symbol"):
            return text(kn).lstrip(":")
        if kn.type == "field_identifier":
            return text(kn)
        v = self.eval(kn, env)
        return v.const if v.kind == CLEAN else NOCONST

    def lookup(self, node, env) -> AV:
        name = text(node)
        if name in env:
            return env[name]
        if name in self.consts:
            return self.consts[name]
        if self.lang == "ruby" and node.type == "identifier" and name in self.by_name:
            r = self.call_same_file(node, name, [], {}, True)
            if r is not None:
                return r
        return AV(UNKNOWN)

    def eval_string(self, node, env) -> AV:
        t = node.type
        if t == "heredoc_beginning":
            body = self.heredoc_body(node)
            return self.eval(body, env) if body is not None else clean()
        parts = []
        lits = []
        for c in node.named_children:
            ct = c.type
            if ct in _INTERP:
                inner = named(c)
                v = None
                for x in inner:
                    v = self.eval(x, env)
                parts.append(v if v is not None else clean(""))
            elif ct in ("string", "template_string", "interpreted_string_literal", "raw_string_literal",
                        "string_literal"):
                parts.append(self.eval_string(c, env))
            elif ct in ("string_content", "string_fragment", "interpreted_string_literal_content",
                        "raw_string_literal_content", "heredoc_content", "escape_sequence",
                        "multiline_string_fragment"):
                parts.append(clean(text(c)))
            elif ct in ("string_start", "string_end", "heredoc_end", "comment"):
                continue
            else:
                parts.append(self.eval(c, env))
            lits.append(c)
        if not node.named_children or all(p.kind == CLEAN and isinstance(p.const, str) for p in parts) \
                and not any(c.type in _INTERP for c in node.named_children):
            return clean(self.str_const(node), text(node))
        if all(p.kind == CLEAN for p in parts):
            if all(isinstance(p.const, str) for p in parts):
                return clean("".join(p.const for p in parts))
            return clean()
        return _built(mix(parts))

    def heredoc_body(self, beginning):
        cur = beginning.parent
        while cur is not None and cur.type not in ("call", "assignment", "body_statement", "program",
                                                   "method_call", "argument_list"):
            cur = cur.parent
        while cur is not None:
            for c in cur.children:
                if c.type == "heredoc_body" and c.start_byte >= beginning.end_byte:
                    return c
            if cur.type in ("body_statement", "program"):
                break
            cur = cur.parent
        return None

    def str_const(self, node) -> str:
        frags = [text(c) for c in node.named_children
                 if c.type in ("string_content", "string_fragment", "interpreted_string_literal_content",
                               "raw_string_literal_content", "escape_sequence", "heredoc_content",
                               "multiline_string_fragment")]
        if frags or node.named_children:
            if node.type == "concatenated_string":
                return "".join(self.str_const(c) for c in node.named_children)
            return "".join(frags)
        s = text(node)
        if node.type in ("simple_symbol",):
            return s.lstrip(":")
        if node.type == "character_literal":
            return s[1:-1]
        m = re.match(r"^[rbuRBUf]*(\"\"\"|'''|\"|'|`)(.*)\1$", s, re.S)
        return m.group(2) if m else s

    def num_const(self, node):
        s = text(node).replace("_", "").rstrip("lLfFdDn")
        try:
            if re.fullmatch(r"[+-]?\d+", s):
                return int(s)
            if re.fullmatch(r"0[xX][0-9a-fA-F]+", s):
                return int(s, 16)
            return float(s)
        except ValueError:
            return NOCONST

    def eval_assign(self, node, env) -> AV:
        t = node.type
        left = node.child_by_field_name("left") or node.child_by_field_name("name")
        right = node.child_by_field_name("right") or node.child_by_field_name("value")
        op_n = node.child_by_field_name("operator")
        op = text(op_n) if op_n is not None else "="
        if t in ("augmented_assignment", "augmented_assignment_expression", "operator_assignment") and op == "=":
            op = "+="
        if right is not None and right.type in ("right_assignment_list",):
            av = self.mk_list([self.eval(c, env) for c in named(right)])
        elif right is not None and right.type == "expression_list" and left is not None \
                and left.type in ("pattern_list", "tuple_pattern"):
            av = self.mk_list([self.eval(c, env) for c in named(right)])
        else:
            av = self.eval(right, env) if right is not None else UNK
        if right is not None:
            av = self._via_def(av, right)
        if op not in ("=", ":=") and left is not None:
            av = self.compound(op, self.eval(left, env), av)
        if left is not None:
            self.bind(left, av, env)
        return av

    def eval_binary(self, node, env) -> AV:
        t = node.type
        if t == "comparison_operator":
            kids = named(node)
            avs = [self.eval(c, env) for c in kids]
            ops = [text(c) for c in node.children if not c.is_named]
            if len(avs) == 2 and len(ops) == 1:
                f = self.fold(ops[0], avs[0], avs[1])
                if f is not None:
                    return f
            return AV(UNKNOWN if any(a.kind for a in avs) else CLEAN)
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        op_n = node.child_by_field_name("operator")
        op = text(op_n) if op_n is not None else next((text(c) for c in node.children if not c.is_named), "")
        lv = self.eval(left, env)
        if op in ("&&", "and", "||", "or"):
            tl = self.truth(lv)
            if tl is not None:
                if (op in ("&&", "and") and not tl) or (op in ("||", "or") and tl):
                    return lv
                return self.eval(right, env)
        rv = self.eval(right, env)
        if op == "%" and self.lang in ("python", "ruby") and left is not None and left.type in _STRINGS:
            rv2 = self.element_of(rv) if rv.items is not None else rv
            return _built(mix([lv, rv2, rv]))
        f = self.fold(op, lv, rv)
        if f is not None:
            return f
        if op in ("==", "!=", "<", ">", "<=", ">=", "===", "!==", "in", "not in", "is", "instanceof",
                  "=~", "!~"):
            return AV(UNKNOWN if lv.kind or rv.kind else CLEAN)
        return _built(mix([lv, rv])) if op in ("+", "<<", ".") else mix([lv, rv])

    def fold(self, op, a: AV, b: AV) -> AV | None:
        if a.kind != CLEAN or b.kind != CLEAN:
            return None
        x, y = a.const, b.const
        if x is NOCONST or y is NOCONST:
            if op == "+" and a.kind == CLEAN and b.kind == CLEAN:
                return clean()
            return None
        try:
            if op == "+":
                if isinstance(x, str) or isinstance(y, str):
                    if self.lang in ("python", "go", "ruby") and not (isinstance(x, str) and isinstance(y, str)):
                        return clean()
                    return clean(self.to_s(x) + self.to_s(y))
                return clean(x + y)
            if op == "-":
                return clean(x - y)
            if op == "*":
                if isinstance(x, str) or isinstance(y, str):
                    return clean() if self.lang not in ("python", "ruby") else clean(x * y)
                return clean(x * y)
            if op in ("/", "//"):
                if y == 0:
                    return None
                if isinstance(x, int) and isinstance(y, int) and (op == "//" or self.lang in ("java", "go", "ruby")):
                    q = abs(x) // abs(y)
                    return clean(q if (x >= 0) == (y >= 0) else -q) if op == "/" else clean(x // y)
                return clean(x / y)
            if op == "%":
                if isinstance(x, str):
                    return clean()
                if y == 0:
                    return None
                return clean(x % y)
            if op in ("==", "==="):
                return clean(x == y)
            if op in ("!=", "!=="):
                return clean(x != y)
            if op == "<":
                return clean(x < y)
            if op == ">":
                return clean(x > y)
            if op == "<=":
                return clean(x <= y)
            if op == ">=":
                return clean(x >= y)
            if op in ("&&", "and"):
                return clean(bool(x) and bool(y))
            if op in ("||", "or"):
                return clean(bool(x) or bool(y))
        except TypeError:
            return clean()
        return clean()

    def fold_unary(self, op, v: AV) -> AV:
        if v.kind != CLEAN:
            return AV(v.kind, NOCONST, None, v.via)
        if v.const is NOCONST:
            return clean()
        try:
            if op in ("!", "not"):
                return clean(not v.const)
            if op == "-":
                return clean(-v.const)
            if op == "+":
                return clean(v.const)
        except TypeError:
            return clean()
        return clean()

    def to_s(self, x) -> str:
        if isinstance(x, bool):
            return "true" if x else "false"
        if x is None:
            return "null"
        return str(x)

    def eval_member(self, node, env) -> AV:
        name = text(node)
        if name in env:
            return env[name]
        obj = (node.child_by_field_name("object") or node.child_by_field_name("operand")
               or node.child_by_field_name("scope"))
        prop = (node.child_by_field_name("property") or node.child_by_field_name("attribute")
                or node.child_by_field_name("field") or node.child_by_field_name("name"))
        if obj is None:
            return AV(UNKNOWN)
        ov = self.eval(obj, env)
        if ov.items is not None and ov.items[0] == "map" and prop is not None:
            for kk, vv in ov.items[1]:
                if kk == text(prop):
                    return vv
        pn = text(prop) if prop is not None else ""
        if pn in ("length", "size"):
            return AV(UNKNOWN)
        if ov.kind == TAINT:
            return AV(TAINT, NOCONST, None, ov.via)
        if pn in self.consts and obj.type in ("identifier", "this") and (text(obj) in self.class_names or text(obj) == "this"):
            return self.consts[pn]
        # a qualified constant (`http.StatusInternalServerError`): its text
        # is all `where:` can know about it
        return AV(UNKNOWN, NOCONST, name if re.fullmatch(r"[\w.]+", name) else None)

    def eval_subscript(self, node, env) -> AV:
        obj = (node.child_by_field_name("value") or node.child_by_field_name("object")
               or node.child_by_field_name("operand") or node.child_by_field_name("array"))
        kids = named(node)
        if obj is None and kids:
            obj = kids[0]
        idx = node.child_by_field_name("index") or node.child_by_field_name("subscript")
        if idx is None:
            rest = [c for c in kids if c is not obj]
            idx = rest[0] if rest else None
        ov = self.eval(obj, env)
        kv = self.eval(idx, env) if idx is not None else UNK
        return self.container_get(ov, kv)

    def call_parts(self, node):
        """(callee name, receiver node or None, callee node, args, kwargs nodes)."""
        t = node.type
        args_n = node.child_by_field_name("arguments")
        args, kwargs = [], {}
        if args_n is not None:
            if args_n.type in ("generator_expression", "string", "template_string"):
                args = [args_n]
            else:
                for a in named(args_n):
                    if a.type == "keyword_argument":
                        nm, v = a.child_by_field_name("name"), a.child_by_field_name("value")
                        if nm is not None and v is not None:
                            kwargs[text(nm)] = v
                    elif a.type == "pair" and self.lang == "ruby":
                        k, v = a.child_by_field_name("key"), a.child_by_field_name("value")
                        if k is not None and v is not None:
                            kwargs[text(k).rstrip(":").lstrip(":")] = v
                    elif a.type == "block_argument":
                        continue
                    else:
                        args.append(a)
        if t in ("call_expression", "call"):
            fn = node.child_by_field_name("function")
            if t == "call" and self.lang == "ruby":
                recv = node.child_by_field_name("receiver")
                m = node.child_by_field_name("method")
                return (text(m) if m is not None else ""), recv, m, args, kwargs
            if fn is None:
                return "", None, None, args, kwargs
            if fn.type in ("member_expression", "attribute", "selector_expression"):
                recv = fn.child_by_field_name("object") or fn.child_by_field_name("operand")
                prop = (fn.child_by_field_name("property") or fn.child_by_field_name("attribute")
                        or fn.child_by_field_name("field"))
                return (text(prop) if prop is not None else ""), recv, fn, args, kwargs
            if fn.type in ("generic_function", "index_expression"):
                inner = fn.child_by_field_name("function") or fn.child_by_field_name("operand")
                return (text(inner) if inner is not None else text(fn)), None, fn, args, kwargs
            return text(fn), None, fn, args, kwargs
        if t == "method_invocation":
            recv = node.child_by_field_name("object")
            nm = node.child_by_field_name("name")
            return (text(nm) if nm is not None else ""), recv, nm, args, kwargs
        if t in ("new_expression", "object_creation_expression"):
            ty = node.child_by_field_name("constructor") or node.child_by_field_name("type")
            name = re.sub(r"<.*$", "", text(ty)).split(".")[-1] if ty is not None else ""
            return name, None, ty, args, kwargs
        return "", None, None, args, kwargs

    def eval_call(self, node, env) -> AV:
        name, recv, callee, arg_nodes, kw_nodes = self.call_parts(node)
        t = node.type
        rv = self.eval(recv, env) if recv is not None else None
        if callee is not None and callee.type in ("subscript_expression", "member_expression") \
                and recv is None:
            self.eval(callee, env)
        args = [self.eval(a, env) for a in arg_nodes]
        kwargs = {k: self.eval(v, env) for k, v in kw_nodes.items()}
        # blocks / lambdas passed along run here
        blk = node.child_by_field_name("block")
        if blk is not None:
            inner = dict(env)
            params = blk.child_by_field_name("parameters")
            el = self.element_of(rv) if rv is not None else UNK
            if params is not None:
                for p in named(params):
                    self.bind(p if p.type == "identifier" else (named(p)[0] if named(p) else p), el, inner)
            b = blk.child_by_field_name("body")
            if b is not None:
                inner = self.exec_body(b, inner, [])[0] or inner
                for k, v in inner.items():
                    if k in env:
                        env[k] = join(env[k], v)
        if t in ("new_expression", "object_creation_expression"):
            if name in ("ArrayList", "LinkedList", "Vector", "Array") and not args:
                return self.mk_list([])
            if name in ("HashMap", "TreeMap", "LinkedHashMap", "Hashtable", "Map", "Properties") and not args:
                return self.mk_map([])
            if name in ("StringBuilder", "StringBuffer", "String"):
                if not args:
                    return clean("")
                a = args[0]
                return a if a.kind != CLEAN or isinstance(a.const, str) else clean()
            body = node.child_by_field_name("body") or next(
                (c for c in node.named_children if c.type == "class_body"), None)
            if body is not None:
                self.exec(body, dict(env), [])
        # same-file call, one hop
        receiver_ok = recv is None or text(recv) in ("this", "self", "cls", "super") \
            or (recv is not None and recv.type in ("object_creation_expression", "new_expression")) \
            or (recv is not None and text(recv) in self.class_names) \
            or (self.lang == "go" and name in self.by_name and any(
                f.type == "method_declaration" for f in self.by_name[name]))
        if t not in ("new_expression", "object_creation_expression") and name in self.by_name:
            r = self.call_same_file(node, name, args, kwargs, receiver_ok)
            if r is not None:
                return AV(r.kind, r.const, r.lit, _via(r.via, frozenset([span(node)])), r.items, r.built)
        # a static call on a class (`Utils.get("x")`, `System.getProperty`):
        # the receiver names code, not data
        static = recv is not None and rv is not None and rv.kind == UNKNOWN and recv.type in (
            "identifier", "field_access", "scoped_identifier", "selector_expression", "attribute",
            "member_expression", "scope_resolution", "constant") and \
            re.fullmatch(r"([a-z_][\w]*\.)*[A-Z]\w*", text(recv)) is not None and \
            text(recv) not in self.ever
        inputs = ([rv] if rv is not None and not static else []) + args + list(kwargs.values())
        # Go out-parameters: `dec.Decode(&req)`, `c.ShouldBindJSON(&req)`
        if self.lang == "go" and (nkey(node) in self.spec.sources or any(a.kind == TAINT for a in inputs)):
            src = AV(TAINT, NOCONST, None, _via(*[a.via for a in inputs], frozenset([span(node)])))
            for a in arg_nodes:
                if a.type == "unary_expression" and text(a).startswith("&"):
                    target = a.child_by_field_name("operand")
                    if target is not None and target.type in _IDENTS | _MEMBERS:
                        self.bind(target, src, env)
        # containers held in a local
        if recv is not None and rv is not None and (recv.type in _IDENTS or recv.type in _MEMBERS):
            rname = text(recv)
            if rv.items is not None:
                kind, vals = rv.items
                if kind == "list":
                    if name in ("add", "append", "push", "<<") and len(args) == 1:
                        env[rname] = self.mk_list(list(vals) + [args[0]])
                        return clean(True)
                    if name in ("remove", "pop", "delete_at") and len(args) == 1 and \
                            isinstance(args[0].const, int) and args[0].kind == CLEAN:
                        i = args[0].const
                        if -len(vals) <= i < len(vals):
                            lst = list(vals)
                            got = lst.pop(i)
                            env[rname] = self.mk_list(lst)
                            return got
                    if name in ("get", "fetch", "at", "elementAt") and len(args) == 1:
                        return self.container_get(rv, args[0])
                    if name in ("pop", "shift", "first", "last") and not args and vals:
                        return vals[-1] if name in ("pop", "last") else vals[0]
                if kind == "map":
                    if name in ("put", "store", "setdefault") and len(args) == 2:
                        env[rname] = self.container_set(rv, args[0], args[1])
                        return clean(None)
                    if name in ("get", "fetch", "getOrDefault", "get_or_default", "dig") and args:
                        got = self.container_get(rv, args[0])
                        if len(args) > 1 and got.kind == CLEAN and got.const is None:
                            return args[1]
                        return got
            if _MUTATE.match(name) and args and recv.type in _IDENTS:
                new = mix([rv] + args)
                if rv.kind == CLEAN and all(a.kind == CLEAN for a in args):
                    new = clean()
                env[rname] = AV(max(rv.kind, new.kind), NOCONST, None, new.via)
                return env[rname]
        if name in ("toString", "to_s", "String", "str", "valueOf", "build") and len(inputs) == 1:
            return inputs[0] if inputs[0].kind != CLEAN or isinstance(inputs[0].const, str) else clean()
        if name == "charAt" and rv is not None and rv.kind == CLEAN and isinstance(rv.const, str) \
                and len(args) == 1 and isinstance(args[0].const, int):
            i = args[0].const
            return clean(rv.const[i]) if 0 <= i < len(rv.const) else clean()
        if name in ("toUpperCase", "upper", "upcase", "ToUpper") and rv is not None and isinstance(rv.const, str):
            return clean(rv.const.upper())
        if name in ("toLowerCase", "lower", "downcase", "ToLower") and rv is not None and isinstance(rv.const, str):
            return clean(rv.const.lower())
        if name in ("equals",) and rv is not None and args and rv.const is not NOCONST \
                and args[0].const is not NOCONST and rv.kind == CLEAN and args[0].kind == CLEAN:
            return clean(rv.const == args[0].const)
        if name in ("asList", "of", "listOf") and recv is not None and text(recv) in ("Arrays", "List", "Set"):
            return self.mk_list(args)
        if _OPAQUE.match(name):
            return AV(UNKNOWN)
        if any(a.kind == TAINT for a in inputs):
            if _FORMAT.match(name) or (name in ("join", "Join") and args):
                return _built(mix(inputs))
            if name in ("toString", "to_s", "concat", "append", "trim", "strip") and rv is not None:
                return AV(TAINT, NOCONST, None, mix(inputs).via, None, any(a.built for a in inputs))
            return mix(inputs)
        if _PURE.match(name) and inputs and all(a.kind == CLEAN for a in inputs):
            return clean()
        if inputs and all(a.kind <= DERIVED for a in inputs):
            return AV(DERIVED, NOCONST, None, _via(*[a.via for a in inputs]))
        return AV(UNKNOWN, NOCONST, None, _via(*[a.via for a in inputs]))


# ---------------------------------------------------------------------------
# Running one rule over one file
# ---------------------------------------------------------------------------

def _encl_func(node, funcs):
    """The outermost function enclosing `node`, or None at file level."""
    out = None
    cur = node.parent
    while cur is not None:
        if cur.type in funcs:
            out = cur
        cur = cur.parent
    return out


def _innermost_func(node, funcs):
    cur = node.parent
    while cur is not None:
        if cur.type in funcs:
            return cur
        cur = cur.parent
    return None


def analyse(lang: str, root, spec: Spec, watch_nodes: list, budget: int = 400_000) -> dict | None:
    """Abstract values for every watched node: {node key: AV}. None when the
    budget ran out (the caller then neither adds nor drops findings)."""
    funcs = FUNCS.get(lang, set())
    watch = {nkey(n) for n in watch_nodes}
    it = Interp(lang, root, spec, watch, budget)
    groups: dict = {}
    callers: set = set()
    for n in watch_nodes:
        g = _encl_func(n, funcs)
        groups[g.id if g is not None else None] = g if g is not None else root
    # one hop back: callers of a function that holds a watched node
    if spec.hops > 0 and None not in groups:
        names = set()
        for n in watch_nodes:
            f = _innermost_func(n, funcs)
            nm = it.func_name(f) if f is not None else None
            if nm:
                names.add(nm)
        if names:
            stack = [root]
            while stack:
                x = stack.pop()
                if x.type in ("call", "call_expression", "method_invocation"):
                    nm, _r, _c, _a, _k = it.call_parts(x)
                    if nm in names:
                        g = _encl_func(x, funcs)
                        if g is not None:
                            if g.id not in groups:
                                callers.add(g.id)
                            groups.setdefault(g.id, g)
                        else:
                            groups[None] = root
                stack.extend(x.children)
    try:
        if None in groups:
            it.exec_body(root, {}, [])
        else:
            for gid, g in groups.items():
                # a caller is run one hop further out, so the function it
                # calls keeps its own hop
                it.depth = -1 if gid in callers else 0
                it.run_function(g)
            it.depth = 0
        # anything the walk did not reach: flow-insensitive, every value
        # each local was ever given
        for n in watch_nodes:
            if nkey(n) not in it.seen:
                it.eval(n, dict(it.ever))
                it.seen.setdefault(nkey(n), UNK)
    except (Budget, RecursionError):
        return None
    except Exception:  # noqa: BLE001 - an engine fault must not cost the rule its own findings
        if os.environ.get("ARBITER_DATAFLOW_STRICT"):
            raise
        return None
    return it.seen


def source_nodes(root, spec: Spec, via) -> list:
    """The source nodes among the spans a value flowed through."""
    keys = spec.sources | spec.inputs
    want = {(k[0], k[1]) for k in keys} & set(via)
    out, stack = [], [root]
    while stack and want:
        n = stack.pop()
        if (n.start_byte, n.end_byte) in want and nkey(n) in keys:
            out.append(n)
        stack.extend(c for c in n.children if c.start_byte <= max(e for _s, e in want))
    return out


# ---------------------------------------------------------------------------
# Static helpers for `where:` and `types:`
# ---------------------------------------------------------------------------

_LITERALISH = _STRINGS | _NUMBERS | _TRUE | _FALSE | _MEMBERS | {"identifier", "constant"}


def _assigned_in(scope, name: str, before: int) -> list:
    """Right-hand sides assigned to `name` in `scope` (not inside nested
    functions' own scopes is not checked: a hoisted temporary is local)."""
    out = []
    stack = [scope]
    while stack:
        n = stack.pop()
        t = n.type
        left = right = None
        if t in ("assignment", "assignment_expression", "augmented_assignment", "operator_assignment",
                 "augmented_assignment_expression"):
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            if t != "assignment" and t != "assignment_expression":
                if left is not None and text(left) == name:
                    out.append(None)
                left = None
        elif t == "variable_declarator":
            left, right = n.child_by_field_name("name"), n.child_by_field_name("value")
        elif t in ("short_var_declaration", "assignment_statement"):
            ls = named(n.child_by_field_name("left")) if n.child_by_field_name("left") is not None else []
            rs = named(n.child_by_field_name("right")) if n.child_by_field_name("right") is not None else []
            for i, lt in enumerate(ls):
                if text(lt) == name:
                    out.append(rs[i] if len(rs) == len(ls) else None)
        elif t in ("var_spec", "const_spec"):
            names = n.children_by_field_name("name")
            val = n.child_by_field_name("value")
            rs = named(val) if val is not None else []
            for i, nm in enumerate(names):
                if text(nm) == name:
                    out.append(rs[i] if i < len(rs) else None)
        if left is not None and left.type in ("identifier", "constant") and text(left) == name:
            out.append(right)
        stack.extend(n.children)
    return out


def static_text(lang: str, node, funcs=None, depth: int = 0) -> str:
    """The source text a `where:` regex is matched against: the node's own
    text, or, for a local assigned exactly one literal-ish value (a hoisted
    temporary) or an identity helper's argument, that value's text."""
    funcs = funcs or FUNCS.get(lang, set())
    if node is None or depth > 3:
        return ""
    t = node.type
    if t == "identifier" or t == "constant":
        name = text(node)
        scope = node.parent
        while scope is not None and scope.type not in funcs:
            scope = scope.parent
        for sc in ([scope] if scope is not None else []) + [_root(node)]:
            vals = _assigned_in(sc, name, node.start_byte)
            if vals:
                if len(vals) == 1 and vals[0] is not None and vals[0].type in _LITERALISH:
                    return static_text(lang, vals[0], funcs, depth + 1)
                return text(node)
        return text(node)
    if t in ("call_expression", "call", "method_invocation"):
        fn = node.child_by_field_name("function") or node.child_by_field_name("method") \
            or node.child_by_field_name("name")
        args = node.child_by_field_name("arguments")
        argv = named(args) if args is not None else []
        if fn is not None and len(argv) == 1:
            name = text(fn)
            for f in _functions_named(_root(node), name, funcs):
                body = f.child_by_field_name("body")
                ps = [c for c in (named(f.child_by_field_name("parameters"))
                                  if f.child_by_field_name("parameters") is not None else [])]
                if body is None or len(ps) != 1:
                    continue
                pname = _param_name(ps[0])
                stmts = [c for c in named(body)]
                while len(stmts) == 1 and stmts[0].type in ("statement_list", "block", "body_statement"):
                    stmts = named(stmts[0])
                if len(stmts) == 1:
                    r = stmts[0]
                    if r.type in ("return_statement", "return"):
                        r = named(r)[0] if named(r) else None
                        if r is not None and r.type in ("expression_list", "argument_list") and len(named(r)) == 1:
                            r = named(r)[0]
                    if r is not None and r.type == "expression_statement" and named(r):
                        r = named(r)[0]
                    if r is not None and text(r) == pname:
                        return static_text(lang, argv[0], funcs, depth + 1)
    if t in ("parenthesized_expression",) and named(node):
        return static_text(lang, named(node)[0], funcs, depth + 1)
    return text(node)


def _param_name(p) -> str:
    if p.type == "identifier":
        return text(p)
    nm = p.child_by_field_name("name") or p.child_by_field_name("pattern")
    if nm is not None:
        return text(nm)
    ids = [c for c in p.named_children if c.type == "identifier"]
    return text(ids[0]) if ids else ""


def _root(node):
    while node.parent is not None:
        node = node.parent
    return node


_FN_CACHE: dict = {}


def _functions_named(root, name: str, funcs) -> list:
    key = (id(root), root.end_byte)
    idx = _FN_CACHE.get(key)
    if idx is None or idx[0] is not root:
        table: dict[str, list] = {}
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type in funcs:
                nm = n.child_by_field_name("name")
                if nm is not None:
                    table.setdefault(text(nm), []).append(n)
            stack.extend(n.children)
        if len(_FN_CACHE) > 64:
            _FN_CACHE.clear()
        idx = (root, table)
        _FN_CACHE[key] = idx
    return idx[1].get(name, [])


def declared_type(lang: str, node, funcs=None) -> str | None:
    """The declared type text of the identifier `node`: a parameter or a
    typed local of an enclosing function. None when it is not declared."""
    funcs = funcs or FUNCS.get(lang, set())
    if node is None or node.type not in ("identifier",):
        return None
    name = text(node)
    cur = node.parent
    while cur is not None:
        if cur.type == "catch_clause":
            for p in named(cur):
                if p.type == "catch_formal_parameter":
                    nm = p.child_by_field_name("name")
                    ty = next((c for c in named(p) if c.type == "catch_type"), None)
                    if nm is not None and text(nm) == name and ty is not None:
                        return text(ty)
        if cur.type in ("block", "statement_block", "statement_list"):
            for st in named(cur):
                if st.start_byte >= node.start_byte:
                    break
                if st.type in ("local_variable_declaration", "var_declaration"):
                    ty = st.child_by_field_name("type")
                    decls = [d for d in named(st) if d.type in ("variable_declarator", "var_spec")]
                    for d in decls:
                        nm = d.child_by_field_name("name")
                        dty = d.child_by_field_name("type") or ty
                        if nm is not None and text(nm) == name and dty is not None:
                            return text(dty)
        if cur.type in funcs:
            ps = cur.child_by_field_name("parameters")
            for p in (named(ps) if ps is not None else []):
                ty = p.child_by_field_name("type")
                names = p.children_by_field_name("name") or [p.child_by_field_name("pattern")]
                if any(n is not None and text(n) == name for n in names) and ty is not None:
                    return text(ty).lstrip(": ").strip()
        cur = cur.parent
    return None
