"""Shipped per-language rule packs: structural checks for every IDE language.

## Why

Most native checks were written for Python first. The languages an interview
workspace (and most services) actually use -- TypeScript/JavaScript, Go, Java,
Ruby, Python -- each get a pack here, so a race, a missing timeout or an
injection is caught the same way whichever language the code is in.

## Packs

One YAML file per pack under `packs/rules/ast/` (`python.yaml`,
`typescript.yaml`, `go.yaml`, `java.yaml`, `ruby.yaml`). Each pack becomes its
own probe, `lang_<pack>`, so a grammar this machine lacks shows up as that one
language not assessed rather than as a clean pass.

```yaml
pack: go                    # rule ids become arbiter/go.<id>
languages: [go]             # inventory language names this pack reads
rules:
  - id: http-no-timeout
    title: "`http.Get` uses the default client, which never times out"
    severity: medium         # info | low | medium | high | critical
    confidence: medium       # low | medium | high
    dimension: quality       # quality | security | ...
    cwe: [CWE-400]
    controls: [NIST-800-218:PW.5]
    tags: [timeout]
    roles: [source]          # inventory roles to read; default [source]
    files: "*.[jt]sx"        # only files whose repo-relative path matches this
                             # fnmatch glob (`*` also crosses `/`), or any glob
                             # of a list: [config/environments/production.rb,
                             # "*/config/environments/production.rb"]
    query: |                 # tree-sitter query; @hit marks the finding
      (call_expression function: (selector_expression) @fn
        (#match? @fn "^http\\.(Get|Post|Head)$")) @hit
    # Optional context conditions, each a tree-sitter query:
    unless_inside: "..."     # skip a hit enclosed by any capture of this query
    unless_in_scope: "..."   # skip a hit whose enclosing function (or file) matches this
    requires_in_scope: "..." # only report a hit whose enclosing function (or file) matches this
    unless_in_file: "..."    # skip every hit when the file matches this
    requires_file: "..."     # only report in a file that matches this
    requirements: [ARB-062]  # the requirement(s) it was built or fixed under
    tier: advisory           # verified | advisory (rule_meta.py, docs/precision.md)
    # Python and Ruby: skip a string-building hit when every value spliced
    # into it is safe (see _splices_safe, _rb_splices_safe). Each is a
    # regex, full-matched:
    safe_splice_calls: "..." # callee of a call whose result is safe (`quote_name`);
                             # in Ruby the method name (`quote_table_name`)
    safe_splice_names: "..." # a name or attribute that is safe (`TABLE`, `inner_sql`)
    # Ruby only:
    safe_splice_text: "..."  # searched in an interpolation's source (`#{...}`):
                             # a match makes it safe (the query's own exclusion)
    safe_splice_values: "..." # full-matched against a value or a local's
                             # assigned value (`query_value("PRAGMA x")`)
    description: "..."
    remediation: "..."
```

`{text}` in a title is replaced by the hit's source text (one line, 60
characters). Evidence is `<id>:<normalised hit text>`, which keeps a finding's
fingerprint stable across line shifts.
"""
from __future__ import annotations

import fnmatch
import re
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from .core import Finding, Location

if TYPE_CHECKING:  # annotation only: probes imports this module to register the packs
    from .probes import ProbeContext

# The same directory as `probes.PACKS / "rules" / "ast"`, spelled out here so
# this module never imports `probes` at load time. `probes` imports this module
# at its end to register the packs, so a top-level import back made
# `import arbiter.lang_rules` (before `arbiter.probes`) a circular ImportError
# (ARB-078). `register_lang_rules` imports `probes` lazily, when it runs.
AST_PACKS = Path(__file__).parent / "packs" / "rules" / "ast"

# Node types that bound a function body, for `unless_in_scope`.
FUNCTION_TYPES: dict[str, set[str]] = {
    "python": {"function_definition", "lambda"},
    "javascript": {"function_declaration", "function_expression", "arrow_function",
                   "method_definition", "generator_function_declaration", "function"},
    "typescript": {"function_declaration", "function_expression", "arrow_function",
                   "method_definition", "generator_function_declaration", "function"},
    "go": {"function_declaration", "method_declaration", "func_literal"},
    "java": {"method_declaration", "constructor_declaration", "lambda_expression"},
    "ruby": {"method", "singleton_method", "lambda"},
}

_KEYS = {"id", "title", "severity", "confidence", "dimension", "cwe", "controls", "tags",
         "roles", "files", "query", "unless_inside", "unless_in_scope", "unless_in_file",
         "requires_file", "requires_in_scope", "description", "remediation", "languages",
         "safe_splice_calls", "safe_splice_names", "safe_splice_text", "safe_splice_values", "taint",
         # Not detection logic: traceability and precision (rule_meta.py).
         "requirements", "tier", "precision_ref"}
# Keys of a rule's `taint:` block (Milestone 3, see dataflow.py).
_TAINT_KEYS = {"mode", "sources", "sinks", "sanitizers", "value", "where", "types", "hops", "requires", "built",
               "unless_element", "at", "inputs", "kill", "element0"}
TAINT_QUERY_KEYS = ("sources", "sinks", "sanitizers", "inputs")
_SEVERITIES = {"info", "low", "medium", "high", "critical"}
_CONFIDENCES = {"low", "medium", "high"}


class PackError(ValueError):
    """A pack that does not match the schema above. Raised at load time so a
    broken pack fails the test suite instead of silently checking nothing."""


def validate_pack(data: dict, name: str) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise PackError(f"{name}: expected a mapping with a `rules` list")
    if not data.get("languages"):
        raise PackError(f"{name}: `languages` is required")
    seen: set[str] = set()
    for i, rule in enumerate(data["rules"]):
        where = f"{name} rule {i} ({rule.get('id', '?')})"
        if not isinstance(rule, dict):
            raise PackError(f"{where}: not a mapping")
        unknown = set(rule) - _KEYS
        if unknown:
            raise PackError(f"{where}: unknown keys {sorted(unknown)}")
        for key in ("id", "title", "query", "remediation"):
            if not rule.get(key):
                raise PackError(f"{where}: `{key}` is required")
        if rule["id"] in seen:
            raise PackError(f"{where}: duplicate id")
        seen.add(rule["id"])
        from .rule_meta import RuleMetaError, validate_meta
        try:
            validate_meta(rule, where)
        except RuleMetaError as exc:
            raise PackError(str(exc)) from exc
        if rule.get("severity", "medium") not in _SEVERITIES:
            raise PackError(f"{where}: bad severity")
        if rule.get("confidence", "medium") not in _CONFIDENCES:
            raise PackError(f"{where}: bad confidence")
        for cwe in rule.get("cwe") or []:
            if not str(cwe).startswith("CWE-"):
                raise PackError(f"{where}: cwe entries look like CWE-89, got {cwe!r}")
        files = rule.get("files")
        if files is not None and not (
                (isinstance(files, str) and files)
                or (isinstance(files, list) and files
                    and all(isinstance(g, str) and g for g in files))):
            raise PackError(f"{where}: `files` is a glob or a non-empty list of globs")
        if "@hit" not in rule["query"]:
            raise PackError(f"{where}: query must capture @hit")
        if "taint" in rule:
            _validate_taint(rule["taint"], where)
        for key in ("safe_splice_calls", "safe_splice_names", "safe_splice_text", "safe_splice_values"):
            if key not in rule:
                continue
            langs = set(rule.get("languages") or data["languages"])
            if key in ("safe_splice_text", "safe_splice_values") and langs != {"ruby"}:
                raise PackError(f"{where}: `{key}` is implemented for Ruby only")
            if len(langs) != 1 or not langs <= {"python", "ruby"}:
                raise PackError(f"{where}: `{key}` is implemented for Python and Ruby only")
            try:
                re.compile(rule[key])
            except (re.error, TypeError) as exc:
                raise PackError(f"{where}: `{key}` is not a regex: {exc}") from exc


def _validate_taint(spec, where: str) -> None:
    if not isinstance(spec, dict):
        raise PackError(f"{where}: `taint` is a mapping")
    unknown = set(spec) - _TAINT_KEYS
    if unknown:
        raise PackError(f"{where}: unknown taint keys {sorted(unknown)}")
    mode = spec.get("mode", "source")
    if mode not in ("source", "nonconst"):
        raise PackError(f"{where}: taint `mode` is source or nonconst")
    sinks = spec.get("sinks")
    if sinks is None and not spec.get("value"):
        raise PackError(f"{where}: taint needs `sinks` (a query capturing @sink and @hit) or `value`")
    if sinks is not None and (not isinstance(sinks, str) or "@sink" not in sinks or "@hit" not in sinks):
        raise PackError(f"{where}: taint `sinks` is a query capturing @sink and @hit")
    if sinks is not None and mode == "source" and not any(
            isinstance(spec.get(k), str) and f"@{c}" in spec[k] for k, c in (("sources", "source"),
                                                                           ("inputs", "input"))):
        raise PackError(f"{where}: taint mode `source` needs a `sources` query capturing @source")
    if spec.get("inputs") is not None and not (isinstance(spec["inputs"], str) and "@input" in spec["inputs"]):
        raise PackError(f"{where}: taint `inputs` is a query capturing @input")
    if spec.get("kill", "clean") not in ("clean", "derived"):
        raise PackError(f"{where}: taint `kill` is clean or derived")
    if spec.get("kill") == "derived" and not spec.get("inputs"):
        raise PackError(f"{where}: taint `kill: derived` needs `inputs`")
    if spec.get("sanitizers") is not None and not (
            isinstance(spec["sanitizers"], str) and "@sanitizer" in spec["sanitizers"]):
        raise PackError(f"{where}: taint `sanitizers` is a query capturing @sanitizer")
    value = spec.get("value", [])
    if not (isinstance(value, list) and all(isinstance(v, str) and v for v in value)):
        raise PackError(f"{where}: taint `value` is a list of capture names of `query`")
    for key in ("where", "types"):
        m = spec.get(key, {})
        if not isinstance(m, dict):
            raise PackError(f"{where}: taint `{key}` maps a capture name to a regex")
        for cap, rx in m.items():
            try:
                re.compile(rx)
            except (re.error, TypeError) as exc:
                raise PackError(f"{where}: taint `{key}.{cap}` is not a regex: {exc}") from exc
    if spec.get("at", "sink") not in ("sink", "source"):
        raise PackError(f"{where}: taint `at` is sink or source")
    for key in ("unless_element", "element0"):
        if key in spec:
            try:
                re.compile(spec[key])
            except (re.error, TypeError) as exc:
                raise PackError(f"{where}: taint `{key}` is not a regex: {exc}") from exc
    hops = spec.get("hops", 1)
    if not isinstance(hops, int) or isinstance(hops, bool) or not 0 <= hops <= 2:
        raise PackError(f"{where}: taint `hops` is 0, 1 or 2")
    for key in ("requires", "built"):
        if not isinstance(spec.get(key, False), bool):
            raise PackError(f"{where}: taint `{key}` is true or false")


QUERY_KEYS = ("query", "unless_inside", "unless_in_scope", "unless_in_file", "requires_file",
              "requires_in_scope")


# File names standing in for each grammar a language can be parsed with, so a
# rule narrowed by `files:` is compiled only for the grammars it can meet: the
# JSX rules are `files: "*.[jt]sx"` because plain typescript has no JSX nodes.
_SAMPLE_FILES = {
    "typescript": ("a.ts", "a.mts", "a.cts", "a.tsx"),
    "javascript": ("a.js", "a.mjs", "a.cjs", "a.jsx"),
}


def _grammars(rule: dict, languages) -> set[str]:
    from . import ast as ts
    out: set[str] = set()
    for lang in languages:
        samples = _SAMPLE_FILES.get(lang)
        if not samples:
            if ts.ts_name(lang):
                out.add(ts.ts_name(lang))
            continue
        for sample in samples:
            if not _path_selected(rule, sample):
                continue
            g = ts.grammar_for(sample, lang)
            if g:
                out.add(g)
    return out


def compile_pack(data: dict, name: str) -> None:
    """Compile every query in a pack against every grammar it will run on.

    A query that does not compile used to surface only mid-scan, as the whole
    `lang_<pack>` probe erroring out: a `#` comment inside a `|` query block
    took the Go planted score from 41/41 to 4/41 with nothing pointing at the
    rule. Raised as a PackError, like a schema error, naming pack, rule and
    reason. Grammars this machine has not cached are skipped (never
    downloaded); that language is then reported as not assessed anyway.
    """
    from . import ast as ts
    if not ts.available():
        return
    cached = ts.cached_grammars()
    for i, rule in enumerate(data["rules"]):
        for grammar in sorted(_grammars(rule, rule.get("languages") or data["languages"]) & cached):
            queries = [(k, rule[k]) for k in QUERY_KEYS if rule.get(k)]
            queries += [(f"taint.{k}", rule["taint"][k]) for k in TAINT_QUERY_KEYS
                        if rule.get("taint") and rule["taint"].get(k)]
            for key, q in queries:
                try:
                    ts.compile_query(grammar, q)
                except ValueError as exc:
                    raise PackError(
                        f"{name} rule {i} ({rule['id']}): `{key}` does not compile "
                        f"for {grammar}: {exc}") from exc


def _compiled_stamp(text: str) -> Path:
    """Where a successful compile of this exact pack text is remembered.

    Compiling every query costs ~4s (the Ruby pack alone ~2s), too much to
    pay on every import. A stamp keyed on the pack's bytes, the tree-sitter
    versions and the cached grammars means the cost is paid once per pack
    edit or upgrade; a broken pack is never stamped, so it fails every load.
    """
    import hashlib
    from importlib import metadata

    from . import ast as ts
    try:
        versions = [metadata.version(d) for d in ("tree-sitter", "tree-sitter-language-pack")]
    except metadata.PackageNotFoundError:
        versions = ["?"]
    key = hashlib.sha256("\0".join(
        [text, *versions, *sorted(ts.cached_grammars())]).encode()).hexdigest()
    return _stamp_dir() / key


def _stamp_dir() -> Path:
    import os
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "arbiter" / "compiled-packs"


@cache
def load_packs() -> dict[str, dict]:
    import yaml
    out: dict[str, dict] = {}
    if not AST_PACKS.is_dir():
        return out
    for path in sorted(AST_PACKS.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        name = data.get("pack") or path.stem
        validate_pack(data, path.name)
        stamp = _compiled_stamp(text)
        if not stamp.exists():
            compile_pack(data, path.name)
            try:
                stamp.parent.mkdir(parents=True, exist_ok=True)
                stamp.touch()
            except OSError:
                pass  # unwritable cache: compile again next load, still correct
        out[name] = data
    return out


def _encloses(outer, inner) -> bool:
    return outer.start_byte <= inner.start_byte and inner.end_byte <= outer.end_byte


def _file_globs(rule: dict) -> list[str]:
    files = rule.get("files")
    if not files:
        return []
    return [files] if isinstance(files, str) else list(files)


def _path_selected(rule: dict, path: str) -> bool:
    """True when the rule has no `files:` or `path` matches one of its globs."""
    globs = _file_globs(rule)
    return not globs or any(fnmatch.fnmatch(path, g) for g in globs)


def _enclosing_function(node, language: str):
    types = FUNCTION_TYPES.get(language, set())
    cur = node.parent
    while cur is not None:
        if cur.type in types:
            return cur
        cur = cur.parent
    return None


def _matches(language: str, query: str, parsed) -> list:
    from . import ast as ts
    return [c["node"] for c in ts.run_query(language, query, parsed)]


def _snippet(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


def run_pack(pack: str, ctx: ProbeContext) -> list[Finding]:
    from . import ast as ts

    data = load_packs()[pack]
    languages = set(data["languages"])
    out: list[Finding] = []
    for f in ctx.inventory.text_files():
        if f.language not in languages:
            continue
        parsed = None
        for rule in data["rules"]:
            if f.role not in (rule.get("roles") or ["source"]):
                continue
            if rule.get("languages") and f.language not in rule["languages"]:
                continue
            if not _path_selected(rule, f.path):
                continue
            if parsed is None:
                parsed = ts.parse_file(f.abspath, f.language)
                if parsed is None:
                    break
            out.extend(_run_rule(pack, rule, f, parsed))
    return out


def _run_rule(pack: str, rule: dict, f, parsed) -> list[Finding]:
    from . import ast as ts
    lang = f.language
    taint = rule.get("taint")
    try:
        if rule.get("unless_in_file") and _matches(lang, rule["unless_in_file"], parsed):
            return []
        file_ok = not rule.get("requires_file") or bool(_matches(lang, rule["requires_file"], parsed))
        if not file_ok and not (taint and not taint.get("requires")):
            return []
        caps = ts.run_query(lang, rule["query"], parsed) if file_ok else []
        hits = [c for c in caps if c["capture"] == "hit"]
        if not hits and not taint:
            return []
        inside = _matches(lang, rule["unless_inside"], parsed) if rule.get("unless_inside") else []
        scoped = _matches(lang, rule["unless_in_scope"], parsed) if rule.get("unless_in_scope") else []
        needed = (_matches(lang, rule["requires_in_scope"], parsed)
                  if rule.get("requires_in_scope") else None)
    except ValueError as exc:
        raise RuntimeError(f"rule arbiter/{pack}.{rule['id']}: {exc}") from exc

    def accept(node, use_requires: bool = True, whole_file: bool = False) -> bool:
        if any(_encloses(o, node) for o in inside):
            return False
        if scoped or (needed is not None and use_requires):
            # Outside any function, the file is the scope; so it is for a
            # value that crossed a function on its way (the check may sit
            # in either function, or in a third the value went through).
            scope = (parsed[0].root_node if whole_file else None) \
                or _enclosing_function(node, lang) or parsed[0].root_node
            if any(_encloses(scope, s) for s in scoped):
                return False
            if use_requires and needed is not None and not any(_encloses(scope, s) for s in needed):
                return False
        return True

    kept: list[dict] = []
    seen: set[int] = set()
    for hit in hits:
        node = hit["node"]
        if node.start_byte in seen:
            continue
        seen.add(node.start_byte)
        if (rule.get("safe_splice_calls") or rule.get("safe_splice_names")) \
                and lang == "python" and _splices_safe(node, rule):
            continue
        if not accept(node):
            continue
        kept.append(hit)
    if taint:
        try:
            kept = _taint_pass(rule, lang, parsed, kept, caps, accept)
        except ValueError as exc:
            raise RuntimeError(f"rule arbiter/{pack}.{rule['id']}: {exc}") from exc
    if lang == "ruby" and any(k.startswith("safe_splice_") for k in rule):
        # After the taint pass, which would otherwise report the same call
        # again as a sink of the string's own interpolations.
        kept = [h for h in kept if not _rb_splices_safe(h["node"], rule)]
    return [_finding(pack, rule, f, hit) for hit in kept]


def _finding(pack: str, rule: dict, f, hit: dict) -> Finding:
    text = _snippet(hit["text"], 60)
    return Finding(
        rule_id=f"arbiter/{pack}.{rule['id']}",
        title=rule["title"].replace("{text}", text),
        dimension=rule.get("dimension", "quality"),
        severity=rule.get("severity", "medium"),
        confidence=rule.get("confidence", "medium"),
        repo_id=f.repo_id, probe=f"lang_{pack}",
        location=Location(path=f.path, start_line=hit["start_line"],
                          end_line=hit["end_line"], repo_id=f.repo_id),
        description=rule.get("description", ""),
        remediation=rule["remediation"],
        evidence=f"{rule['id']}:{_snippet(hit['text'], 120)}",
        controls=list(rule.get("controls") or []),
        cwe=list(rule.get("cwe") or []),
        tags=["lang-rule", f.language, *(rule.get("tags") or [])],
    )


# ---------------------------------------------------------------------------
# One-hop data flow (`taint:`), see dataflow.py
# ---------------------------------------------------------------------------

def _hit_dict(node) -> dict:
    return {"capture": "hit", "type": node.type, "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1, "node": node,
            "text": node.text.decode("utf-8", "replace") if node.text is not None else ""}


def _taint_pass(rule: dict, lang: str, parsed, kept: list[dict], caps: list[dict], accept) -> list[dict]:
    """Drop the rule's own hits whose `value` is provably constant, and add
    sink hits the data flow reaches. Returns the new list of hits."""
    try:
        return _taint_hits(rule, lang, parsed, kept, caps, accept)
    except ValueError:
        raise
    except Exception:  # noqa: BLE001 - see dataflow.analyse: never lose the rule's own hits
        import os
        if os.environ.get("ARBITER_DATAFLOW_STRICT"):
            raise
        return kept


def _taint_hits(rule: dict, lang: str, parsed, kept: list[dict], caps: list[dict], accept) -> list[dict]:
    from . import ast as ts
    from . import dataflow as df
    spec_cfg = rule["taint"]
    root = parsed[0].root_node
    funcs = FUNCTION_TYPES.get(lang, set())
    spec = df.Spec(mode=spec_cfg.get("mode", "source"), hops=spec_cfg.get("hops", 1))
    where = {k: re.compile(v) for k, v in (spec_cfg.get("where") or {}).items()}
    types = {k: re.compile(v) for k, v in (spec_cfg.get("types") or {}).items()}

    def constraints_ok(match: dict) -> bool:
        for cap, rx in where.items():
            for n in match.get(cap, []):
                if not rx.search(df.static_text(lang, n, funcs)):
                    return False
        for cap, rx in types.items():
            for n in match.get(cap, []):
                if not rx.search(df.declared_type(lang, n, funcs) or ""):
                    return False
        return True

    if spec_cfg.get("sources"):
        for m in ts.run_matches(lang, spec_cfg["sources"], parsed):
            if not constraints_ok(m):
                continue
            for n in m.get("source", []):
                spec.sources.add(df.nkey(n))
                spec.source_params.add(df.span(n))
    input_nodes = []
    if spec_cfg.get("inputs"):
        for m in ts.run_matches(lang, spec_cfg["inputs"], parsed):
            if not constraints_ok(m):
                continue
            for n in m.get("input", []):
                spec.inputs.add(df.nkey(n))
                spec.source_params.add(df.span(n))
                input_nodes.append(n)
    if spec_cfg.get("sanitizers"):
        for m in ts.run_matches(lang, spec_cfg["sanitizers"], parsed):
            for n in m.get("sanitizer", []):
                spec.sanitizers.add(df.nkey(n))

    value_names = set(spec_cfg.get("value") or [])
    kill_vals: dict[int, list] = {i: [] for i in range(len(kept))}
    if value_names and kept:
        # values pair with their hit by match: a declaration's value sits
        # outside the call it reaches (`String sql = ...; execute(sql)`)
        by_hit: dict[tuple, list] = {}
        for m in ts.run_matches(lang, rule["query"], parsed):
            for h in m.get("hit", []):
                vals = [v for name in value_names for v in m.get(name, [])]
                by_hit.setdefault((h.start_byte, h.end_byte), []).extend(vals)
        for i, hit in enumerate(kept):
            node = hit["node"]
            kill_vals[i] = list({df.nkey(v): v for v in by_hit.get((node.start_byte, node.end_byte), [])}.values())
    sinks: list[tuple] = []
    for m in (ts.run_matches(lang, spec_cfg["sinks"], parsed) if spec_cfg.get("sinks") else []):
        if not m.get("hit") or not m.get("sink") or not constraints_ok(m):
            continue
        sinks.append((m["hit"][0], m["sink"]))
    if spec.mode == "source" and not spec.sources and not spec.inputs and not any(kill_vals.values()):
        return kept
    watch = [v for vs in kill_vals.values() for v in vs] + [s for _h, ss in sinks for s in ss]
    if not watch:
        return kept
    seen = df.analyse(lang, root, spec, watch)
    if seen is None:
        return kept
    derived = spec_cfg.get("kill") == "derived"

    def killed(i: int) -> bool:
        vals = kill_vals[i]
        if not vals:
            return False
        kinds = [seen.get(df.nkey(v), df.UNK).kind for v in vals]
        if all(k == df.CLEAN for k in kinds):
            return True
        if derived and all(k <= df.DERIVED for k in kinds):
            # the function reads request input, and none of it gets here
            fn = _enclosing_function(kept[i]["node"], lang)
            return fn is not None and any(_encloses(fn, n) for n in input_nodes)
        return False

    out = [hit for i, hit in enumerate(kept) if not killed(i)]
    lines = {h["start_line"] for h in out}
    reported: set = set()
    starts = {h["node"].start_byte for h in out}
    for hit_node, sink_nodes in sinks:
        avs = [seen.get(df.nkey(s), df.UNK) for s in sink_nodes]
        if spec_cfg.get("built"):
            avs = [a for a in avs if a.built]
        if spec_cfg.get("element0"):
            rx0 = re.compile(spec_cfg["element0"])
            avs = [a for a in avs if a.items and a.items[0] == "list" and a.items[1]
                   and isinstance(a.items[1][0].const, str) and rx0.search(a.items[1][0].const)]
        if spec_cfg.get("unless_element"):
            rx = re.compile(spec_cfg["unless_element"])
            avs = [a for a in avs if not (a.items and a.items[0] == "list" and any(
                isinstance(e.const, str) and rx.search(e.const) for e in a.items[1]))]
        if spec.mode == "source" and spec_cfg.get("sources") and spec_cfg.get("inputs"):
            # with both, a sink must be reached from a `sources` node (the
            # built SQL); `inputs` alone only serve the `kill: derived` check
            src_spans = {(k[0], k[1]) for k in spec.sources}
            bad = [a for a in avs if a.kind == df.TAINT and src_spans & set(a.via)]
        elif spec.mode == "source":
            bad = [a for a in avs if a.kind == df.TAINT]
        else:
            bad = [a for a in avs if a.kind != df.CLEAN]
        if not bad:
            continue
        line = hit_node.start_point[0] + 1
        if hit_node.start_byte in starts or line in lines:
            continue
        via = set().union(*[a.via for a in bad])
        if any(h["node"].start_byte <= s and e <= h["node"].end_byte for h in out for s, e in via):
            continue
        if spec_cfg.get("at") == "source":
            # report where the value was built, when that is in the sink's own
            # function (the convention of the rule's own query)
            fn = _enclosing_function(hit_node, lang)
            srcs = [n for n in df.source_nodes(root, spec, via)
                    if _enclosing_function(n, lang) == fn and n.end_byte <= hit_node.start_byte]
            if srcs:
                hit_node = max(srcs, key=lambda n: n.start_byte)
                line = hit_node.start_point[0] + 1
                if line in lines:
                    continue
        sink_fn = _enclosing_function(hit_node, lang)
        srcs_all = df.source_nodes(root, spec, via)
        crossed = any(_enclosing_function(n, lang) != sink_fn for n in srcs_all) or any(
            not (sink_fn is not None and sink_fn.start_byte <= s0 and e0 <= sink_fn.end_byte)
            for s0, e0 in via)
        if not accept(hit_node, use_requires=bool(spec_cfg.get("requires")), whole_file=crossed):
            continue
        # one finding per source per function: the same value written to
        # three paths is one defect
        sig = (sink_fn.id if sink_fn is not None else None,
               frozenset((n.start_byte, n.end_byte) for n in srcs_all) or None)
        if sig[1] is not None and sig in reported:
            continue
        reported.add(sig)
        out.append(_hit_dict(hit_node))
        lines.add(line)
        starts.add(hit_node.start_byte)
    return out


# ---------------------------------------------------------------------------
# Python: values spliced into a built string, and whether each is safe
# ---------------------------------------------------------------------------
#
# Simple in-function constant tracking, for `python.sql-injection` (round 2,
# ARB-065). Django's cache backend writes `table = quote_name(self._table)`
# and then `"DELETE FROM %s" % table`; celery builds `col_name =
# preparer.quote_identifier(col.name)` before an f-string. A spliced value is
# safe when it is a literal, a call to a quoting function, a safe name (by
# the rule's regex), `str()` of a safe value, a `sep.join(...)` of safe
# elements, a conditional or string-format of safe values, or a local
# variable every assignment of which in the enclosing function is safe.
# A parameter, a loop variable, anything assigned from elsewhere, and any
# expression not listed are not. This is not data flow: nothing crosses a
# function boundary (that is Milestone 3).

_LITERALS = {"integer", "float", "true", "false", "none"}


def _txt(node) -> str:
    return node.text.decode("utf-8", "replace") if node.text is not None else ""


def _splices(node) -> list | None:
    """The values spliced into a string-building expression; None if unknown."""
    t = node.type
    if t == "string":
        return [c.named_children[0] for c in node.named_children
                if c.type == "interpolation" and c.named_children]
    if t == "concatenated_string":
        out: list = []
        for c in node.named_children:
            out.extend(_splices(c) or [])
        return out
    if t == "parenthesized_expression" and node.named_children:
        inner = node.named_children[0]
        return _splices(inner) if inner.type in ("string", "concatenated_string",
                                                 "binary_operator", "call") else [inner]
    if t == "binary_operator":
        op = node.child_by_field_name("operator")
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        if op is None or left is None or right is None:
            return None
        if _txt(op) == "%" and left.type in ("string", "concatenated_string"):
            values = _splices(left) or []
            if right.type == "tuple":
                return values + list(right.named_children)
            if right.type == "dictionary":
                return values + [p.child_by_field_name("value") for p in right.named_children
                                 if p.type == "pair"]
            return values + [right]
        if _txt(op) == "+":
            out = []
            for side in (left, right):
                inner = _splices(side) if side.type in ("string", "concatenated_string") \
                    or (side.type == "binary_operator" and _txt(side.child_by_field_name("operator")) == "+") \
                    else [side]
                if inner is None:
                    return None
                out.extend(inner)
            return out
        return None
    if t == "call":
        fn, args = node.child_by_field_name("function"), node.child_by_field_name("arguments")
        if fn is not None and fn.type == "attribute" and args is not None:
            obj, attr = fn.child_by_field_name("object"), fn.child_by_field_name("attribute")
            if attr is not None and _txt(attr) == "format" and obj is not None \
                    and obj.type in ("string", "concatenated_string"):
                out = list(_splices(obj) or [])
                for a in args.named_children:
                    if a.type == "keyword_argument":
                        out.append(a.child_by_field_name("value"))
                    else:
                        out.append(a)
                return out
    return None


def _scope_of(node):
    cur = node.parent
    while cur is not None and cur.type != "function_definition":
        cur = cur.parent
    return cur


def _assignments(scope, name: str) -> list | None:
    """Right-hand sides assigned to `name` in this function; None when it is a
    parameter or is bound some other way (a loop, `with ... as`, `global`)."""
    params = scope.child_by_field_name("parameters")
    if params is not None and re.search(rf"(?<![\w.]){re.escape(name)}\b", _txt(params)):
        return None
    out: list = []
    stack = [scope.child_by_field_name("body")]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        if n.type in ("function_definition", "lambda", "class_definition"):
            continue
        if n.type in ("assignment", "augmented_assignment"):
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            if left is not None and _txt(left) == name:
                if right is None:
                    return None
                out.append(right)
            elif left is not None and re.search(rf"(?<![\w.]){re.escape(name)}\b", _txt(left)):
                return None     # tuple unpacking or a subscript
        if n.type in ("for_statement", "for_in_clause", "with_item", "as_pattern",
                      "global_statement", "nonlocal_statement", "named_expression"):
            target = n.child_by_field_name("left") or n.child_by_field_name("alias") or n
            if re.search(rf"(?<![\w.]){re.escape(name)}\b", _txt(target)) and n.type != "with_item":
                return None
            if n.type == "with_item" and re.search(rf"\bas\s+{re.escape(name)}\b", _txt(n)):
                return None
        stack.extend(n.children)
    return out


def _value_safe(node, rule: dict, scope, depth: int = 0) -> bool:
    if node is None or depth > 4:
        return False
    t = node.type
    calls = rule.get("safe_splice_calls")
    names = rule.get("safe_splice_names")
    if t in _LITERALS:
        return True
    if t in ("string", "concatenated_string", "binary_operator"):
        parts = _splices(node)
        if t == "binary_operator" and parts is None:
            # arithmetic on safe values (`a * 2`) stays safe
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            return _value_safe(left, rule, scope, depth + 1) and _value_safe(right, rule, scope, depth + 1)
        return parts is not None and all(_value_safe(p, rule, scope, depth + 1) for p in parts)
    if t == "parenthesized_expression":
        return bool(node.named_children) and _value_safe(node.named_children[0], rule, scope, depth + 1)
    if t == "conditional_expression":
        kids = node.named_children
        return len(kids) == 3 and _value_safe(kids[0], rule, scope, depth + 1) \
            and _value_safe(kids[2], rule, scope, depth + 1)
    if t in ("list", "tuple"):
        return all(_value_safe(k, rule, scope, depth + 1) for k in node.named_children)
    if t in ("generator_expression", "list_comprehension"):
        body = node.child_by_field_name("body")
        return body is not None and body.type == "call" and _value_safe(body, rule, scope, depth + 1)
    if t in ("identifier", "attribute"):
        if names and re.fullmatch(names, _txt(node)):
            return True
        if t == "identifier" and scope is not None:
            name = _txt(node)
            rhs = _assignments(scope, name)
            if not rhs:
                return False
            # A container filled later (`query = []`, then `query.append(...)`)
            # is only as safe as what goes in, which this does not follow.
            if any(r.type in ("list", "dictionary", "set") for r in rhs) and re.search(
                    rf"(?<![\w.]){re.escape(name)}\s*(?:\.\w+\s*\(|\[)", _txt(scope)):
                return False
            return all(_value_safe(r, rule, scope, depth + 1) for r in rhs)
        return False
    if t == "call":
        fn, args = node.child_by_field_name("function"), node.child_by_field_name("arguments")
        if fn is None:
            return False
        callee = _txt(fn)
        if calls and re.fullmatch(calls, callee):
            return True
        if args is not None and args.type == "generator_expression":
            argv = [args]       # `", ".join(q(c) for c in cols)`: no argument list
        else:
            argv = [a for a in (args.named_children if args is not None else [])
                    if a.type != "comment"]
        if callee == "str" and len(argv) == 1:
            return _value_safe(argv[0], rule, scope, depth + 1)
        if fn.type == "attribute":
            obj, attr = fn.child_by_field_name("object"), fn.child_by_field_name("attribute")
            if attr is not None and _txt(attr) == "join" and obj is not None \
                    and obj.type == "string" and len(argv) == 1:
                arg = argv[0]
                if arg.type == "binary_operator" and _txt(arg.child_by_field_name("operator")) == "*":
                    # `", ".join(["%s"] * len(keys))`: a placeholder list
                    return _value_safe(arg.child_by_field_name("left"), rule, scope, depth + 1)
                return _value_safe(arg, rule, scope, depth + 1)
            if attr is not None and _txt(attr) == "format" and obj is not None \
                    and obj.type == "string":
                return all(_value_safe(p, rule, scope, depth + 1)
                           for p in (_splices(node) or [None]))
        return False
    return False


def _splices_safe(hit, rule: dict) -> bool:
    parts = _splices(hit)
    if not parts:
        return False
    scope = _scope_of(hit)
    return all(_value_safe(p, rule, scope) for p in parts)


def register_lang_rules() -> None:
    from . import ast as ts
    from .probes import Probe, register
    for pack, data in load_packs().items():
        grammars = sorted({g for lang in data["languages"] for g in [ts.ts_name(lang)] if g}
                          | ({"tsx"} if "typescript" in data["languages"] else set()))
        register(Probe(
            name=f"lang_{pack}",
            scope="file",
            dimensions=sorted({r.get("dimension", "quality") for r in data["rules"]}) or ["quality"],
            checks=max(1, len(data["rules"])),
            stacks=list(data["languages"]),
            modules=["tree_sitter_language_pack"],
            grammars=grammars,
            run=lambda ctx, _pack=pack: run_pack(_pack, ctx),
            cacheable=True,
        ))


def pack_path(pack: str) -> Path:
    return AST_PACKS / f"{pack}.yaml"


# ---------------------------------------------------------------------------
# Ruby: the same question for an interpolated string argument
# ---------------------------------------------------------------------------
#
# `ruby.sql-injection` (round 4): every one of its gold findings labelled
# `v3` was a false positive, and eight of them interpolate only values that
# cannot carry outside data: a literal picked by a condition (`#{enforced ?
# 'ENFORCED' : 'NOT ENFORCED'}`, `#{' IF EXISTS' if if_exists}`) beside
# `quote_table_name(...)`, or a PRAGMA setting read back from the database
# a few lines earlier (`old = query_value("PRAGMA foreign_keys")`, then
# `execute("PRAGMA foreign_keys = #{old}")`). A call whose first argument is
# a string is skipped when every interpolation in it is safe: its source
# matches `safe_splice_text`, or its value is a literal, a constant, a
# conditional of safe values, a call `safe_splice_calls` names, a value
# `safe_splice_values` matches, or a local every assignment of which in the
# enclosing method is safe. A parameter, a block or loop variable, an
# instance variable and a bare name never assigned (a method call) are not.

_RB_LITERALS = {"integer", "float", "true", "false", "nil", "simple_symbol", "constant",
                "string_content", "escape_sequence"}
_RB_SCOPES = {"method", "singleton_method", "class", "module", "program"}


def _rb_scope(node):
    cur = node.parent
    while cur is not None and cur.type not in _RB_SCOPES:
        cur = cur.parent
    return cur


def _rb_assignments(scope, name: str) -> list | None:
    """Right-hand sides assigned to local `name` in this method; None when it
    is a parameter, a block or loop variable, or bound some other way."""
    word = re.compile(rf"(?<![\w@$:.]){re.escape(name)}(?![\w?!])")
    params = scope.child_by_field_name("parameters")
    if params is not None and word.search(_txt(params)):
        return None
    out: list = []
    stack = list(scope.named_children)
    while stack:
        n = stack.pop()
        if n.type in ("method", "singleton_method", "class", "module"):
            continue
        if n.type in ("block_parameters", "lambda_parameters", "exception_variable",
                      "left_assignment_list", "for"):
            target = n.child_by_field_name("pattern") if n.type == "for" else n
            if target is not None and word.search(_txt(target)):
                return None
        if n.type in ("assignment", "operator_assignment"):
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            if left is not None and left.type == "identifier" and _txt(left) == name:
                if right is None:
                    return None
                out.append(right)
        stack.extend(n.named_children)
    return out


def _rb_value_safe(node, rule: dict, scope, depth: int = 0) -> bool:
    if node is None or depth > 4:
        return False
    t = node.type
    values = rule.get("safe_splice_values")
    if values and re.fullmatch(values, _txt(node)):
        return True
    if t in _RB_LITERALS:
        return True
    if t == "string":
        return all(c.type in _RB_LITERALS or
                   (c.type == "interpolation" and _rb_interpolation_safe(c, rule, scope, depth + 1))
                   for c in node.named_children)
    if t == "parenthesized_statements":
        kids = [k for k in node.named_children if k.type != "comment"]
        return len(kids) == 1 and _rb_value_safe(kids[0], rule, scope, depth + 1)
    if t == "conditional":
        return _rb_value_safe(node.child_by_field_name("consequence"), rule, scope, depth + 1) \
            and _rb_value_safe(node.child_by_field_name("alternative"), rule, scope, depth + 1)
    if t in ("if_modifier", "unless_modifier"):
        return _rb_value_safe(node.child_by_field_name("body"), rule, scope, depth + 1)
    if t == "call":
        calls = rule.get("safe_splice_calls")
        m = node.child_by_field_name("method")
        return bool(calls and m is not None and re.fullmatch(calls, _txt(m)))
    if t == "identifier":
        names = rule.get("safe_splice_names")
        if names and re.fullmatch(names, _txt(node)):
            return True
        if scope is None:
            return False
        rhs = _rb_assignments(scope, _txt(node))
        return bool(rhs) and all(_rb_value_safe(r, rule, scope, depth + 1) for r in rhs)
    return False


def _rb_interpolation_safe(interp, rule: dict, scope, depth: int = 0) -> bool:
    text_rx = rule.get("safe_splice_text")
    if text_rx and re.search(text_rx, _txt(interp)):
        return True
    kids = [k for k in interp.named_children if k.type != "comment"]
    return len(kids) == 1 and _rb_value_safe(kids[0], rule, scope, depth)


def _rb_splices_safe(hit, rule: dict) -> bool:
    """A call whose first argument is a string every interpolation of which
    is safe. Anything else (a heredoc, a local, a concatenation) is not
    judged here and keeps its finding."""
    if hit.type != "call":
        return False
    args = hit.child_by_field_name("arguments")
    first = next((a for a in (args.named_children if args is not None else []) if a.type != "comment"), None)
    if first is None or first.type != "string":
        return False
    interps = [c for c in first.named_children if c.type == "interpolation"]
    if not interps:
        return False
    scope = _rb_scope(hit)
    return all(_rb_interpolation_safe(i, rule, scope) for i in interps)
