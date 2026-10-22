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
    description: "..."
    remediation: "..."
```

`{text}` in a title is replaced by the hit's source text (one line, 60
characters). Evidence is `<id>:<normalised hit text>`, which keeps a finding's
fingerprint stable across line shifts.
"""
from __future__ import annotations

import fnmatch
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
         "requires_file", "requires_in_scope", "description", "remediation", "languages"}
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
            for key in QUERY_KEYS:
                if not rule.get(key):
                    continue
                try:
                    ts.compile_query(grammar, rule[key])
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
    try:
        if rule.get("requires_file") and not _matches(lang, rule["requires_file"], parsed):
            return []
        if rule.get("unless_in_file") and _matches(lang, rule["unless_in_file"], parsed):
            return []
        hits = [c for c in ts.run_query(lang, rule["query"], parsed) if c["capture"] == "hit"]
        if not hits:
            return []
        inside = _matches(lang, rule["unless_inside"], parsed) if rule.get("unless_inside") else []
        scoped = _matches(lang, rule["unless_in_scope"], parsed) if rule.get("unless_in_scope") else []
        needed = (_matches(lang, rule["requires_in_scope"], parsed)
                  if rule.get("requires_in_scope") else None)
    except ValueError as exc:
        raise RuntimeError(f"rule arbiter/{pack}.{rule['id']}: {exc}") from exc

    out: list[Finding] = []
    seen: set[int] = set()
    for hit in hits:
        node = hit["node"]
        if node.start_byte in seen:
            continue
        seen.add(node.start_byte)
        if any(_encloses(o, node) for o in inside):
            continue
        if scoped or needed is not None:
            # Outside any function, the file is the scope.
            scope = _enclosing_function(node, lang) or parsed[0].root_node
            if any(_encloses(scope, s) for s in scoped):
                continue
            if needed is not None and not any(_encloses(scope, s) for s in needed):
                continue
        text = _snippet(hit["text"], 60)
        out.append(Finding(
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
            tags=["lang-rule", lang, *(rule.get("tags") or [])],
        ))
    return out


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
