"""Contracts declared in one artifact and implemented in another.

## The shape of the problem

A repository usually contains at least two descriptions of the same thing,
written in different languages, maintained by different habits, and checked
against each other by nobody:

  * an OpenAPI document and the routes the server actually registers;
  * a set of database migrations and the model the application queries with;
  * infrastructure outputs and the configuration the application reads.

Each half is valid on its own terms. The spec parses, the routes compile, the
migrations apply. No linter compares them, because each tool sees one side.
The failure shows up at runtime as a 404 against a documented endpoint, or a
column that exists in the ORM and not in the schema.

This is the same idea as the cross-repo seam checks, moved inside a single
repository: the interesting defect is not in either artifact, it is in the gap
between them.

## Why this is deterministic rather than a question for a model

It is tempting to hand this to a language model -- it reads like a
comprehension task. It is not. A path either appears in the route table or it
does not; a column either appears in a migration or it does not. Those are
lookups. Handing a decidable question to a probabilistic answerer trades a
right answer for a plausible one, and then you cannot gate on the result.

## What it will not tell you

Route registration in most frameworks is dynamic enough that some paths are
built at runtime from variables the parser never sees. Every check here is
therefore biased hard toward silence: a path assembled from a variable is
skipped rather than guessed at, and a spec with fewer than three recognisable
routes on the code side is treated as unreadable rather than as evidence that
the code implements nothing. Missing a real drift is a bad outcome; announcing
that a working endpoint is missing is a worse one.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .core import Finding, Location
from .probes import ProbeContext, _line_of, _read

# A spec with fewer recognisable routes on the code side than this is treated
# as unreadable. Below it, "the code implements none of the spec" is almost
# always a parser limitation rather than a finding.
MIN_ROUTES_TO_TRUST = 3

_SPEC_NAMES = re.compile(r"(^|/)(openapi|swagger)[.-]?\w*\.(ya?ml|json)$", re.I)

# Route registration, across the frameworks that declare paths as literals.
_ROUTE_PATTERNS = [
    # express / fastify / koa-router:  app.get('/path', ...)
    re.compile(r"""\b(?:app|router|api|server|r)\s*\.\s*
                   (get|post|put|patch|delete|head|options|all)\s*\(\s*
                   ['"`]([^'"`]+)['"`]""", re.X),
    # flask / fastapi decorators:  @app.get("/path")  @app.route("/path")
    re.compile(r"""@\s*\w+\s*\.\s*(get|post|put|patch|delete|route)\s*\(\s*
                   ['"]([^'"]+)['"]""", re.X),
    # django urls:  path("route/", ...)  re_path(r"^route/$", ...)
    re.compile(r"""\b(?:path|re_path|url)\s*\(\s*r?['"]([^'"]*)['"]""", re.X),
    # go chi / gorilla / echo:  r.Get("/path", ...)  e.GET("/path", ...)
    re.compile(r"""\b\w+\.(Get|Post|Put|Patch|Delete|Handle|HandleFunc|GET|POST|
                   PUT|PATCH|DELETE)\s*\(\s*["`]([^"`]+)["`]""", re.X),
    # spring:  @GetMapping("/path")  @RequestMapping("/path")
    re.compile(r"""@(?:Get|Post|Put|Patch|Delete|Request)Mapping\s*\(\s*
                   (?:value\s*=\s*)?["]([^"]+)["]""", re.X),
]

# A path assembled from a variable cannot be compared, so it is skipped.
#
# Note what is NOT here: a bare `{` or `}`. Chi, FastAPI and OpenAPI all spell a
# route parameter `{id}`, so treating braces as evidence of interpolation made
# every parameterized Go route invisible and then reported the matching spec
# path as unimplemented. Only actual interpolation counts -- `${...}`, a printf
# placeholder, an f-string, .format(), or concatenation.
_DYNAMIC_PATH = re.compile(r"\$\{|%[sdvq]|\bf['\"]|\.format\(|\+")

# OpenAPI templates a path parameter as {id}; frameworks spell the same thing
# five different ways. Normalizing is what makes the comparison possible at all.
_PARAM_SPELLINGS = [
    re.compile(r"\{[^}/]+\}"),          # OpenAPI  /users/{id}
    re.compile(r":[A-Za-z_]\w*"),       # express  /users/:id
    re.compile(r"<[^>/]+>"),            # flask    /users/<int:id>
    re.compile(r"\(\?P<[^>]+>[^)]*\)"), # django   /users/(?P<id>[0-9]+)
    re.compile(r"\*\*?"),               # wildcards
]


def normalize_path(p: str) -> str:
    """Reduce a route to a shape two frameworks can be compared on."""
    p = p.strip()
    if not p.startswith("/"):
        p = "/" + p
    for rx in _PARAM_SPELLINGS:
        p = rx.sub("{}", p)
    p = re.sub(r"\^|\$", "", p)          # django regex anchors
    p = re.sub(r"/+", "/", p)
    return p.rstrip("/") or "/"


def _load_spec(text: str, path: str) -> dict | None:
    import yaml
    try:
        if path.lower().endswith(".json"):
            return json.loads(text)
        return yaml.safe_load(text)
    except (ValueError, yaml.YAMLError):
        return None


def _spec_routes(doc: dict) -> dict[str, set[str]]:
    """Declared path -> methods, normalized."""
    out: dict[str, set[str]] = {}
    paths = doc.get("paths")
    if not isinstance(paths, dict):
        return out
    for raw, item in paths.items():
        if not isinstance(item, dict):
            continue
        methods = {m.lower() for m in item
                   if m.lower() in ("get", "post", "put", "patch", "delete",
                                    "head", "options")}
        if methods:
            out.setdefault(normalize_path(str(raw)), set()).update(methods)
    return out


def _code_routes(ctx: ProbeContext, repo_id: str) -> tuple[dict[str, set[str]], int]:
    """Paths the code registers, and how many were skipped as dynamic."""
    found: dict[str, set[str]] = {}
    skipped = 0
    for f in ctx.inventory.text_files():
        if f.repo_id != repo_id or f.role in ("generated", "docs", "data", "test"):
            continue
        if f.language not in ("javascript", "typescript", "python", "go", "java",
                              "ruby", "php"):
            continue
        text = _read(f)
        if not text:
            continue
        for rx in _ROUTE_PATTERNS:
            for m in rx.finditer(text):
                groups = [g for g in m.groups() if g]
                if not groups:
                    continue
                raw = groups[-1]
                method = groups[0].lower() if len(groups) > 1 else ""
                if _DYNAMIC_PATH.search(raw):
                    skipped += 1
                    continue
                key = normalize_path(raw)
                found.setdefault(key, set())
                if method in ("get", "post", "put", "patch", "delete", "head",
                              "options"):
                    found[key].add(method)
    return found, skipped


# ---------------------------------------------------------------------------
# Migrations vs models
# ---------------------------------------------------------------------------

_MIGRATION_DIR = re.compile(r"(^|/)(migrations?|migrate|alembic/versions|db/migrate)(/|$)")
_ADD_COLUMN = re.compile(
    r"(?i)\bALTER\s+TABLE\s+[\"`\[]?(\w+)[\"`\]]?\s+ADD\s+(?:COLUMN\s+)?[\"`\[]?(\w+)"
    r"|\badd_column\s*\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]"
    r"|\bmigrations\.AddField\s*\(\s*model_name\s*=\s*['\"](\w+)['\"]\s*,\s*name\s*=\s*['\"](\w+)['\"]"
)
_DROP_COLUMN = re.compile(
    r"(?i)\bALTER\s+TABLE\s+[\"`\[]?(\w+)[\"`\]]?\s+DROP\s+(?:COLUMN\s+)?[\"`\[]?(\w+)"
    r"|\bremove_column\s*\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]"
    r"|\bmigrations\.RemoveField\s*\(\s*model_name\s*=\s*['\"](\w+)['\"]\s*,\s*name\s*=\s*['\"](\w+)['\"]"
)


def _pairs(rx: re.Pattern, text: str) -> set[tuple[str, str]]:
    out = set()
    for m in rx.finditer(text):
        g = [x for x in m.groups() if x]
        if len(g) >= 2:
            out.add((g[0].lower(), g[1].lower()))
    return out


def probe_contract(ctx: ProbeContext) -> list[Finding]:
    out: list[Finding] = []
    for repo in ctx.repos:
        out.extend(_spec_vs_routes(ctx, repo.id))
        out.extend(_dropped_column_still_referenced(ctx, repo.id))
    return out


def _spec_vs_routes(ctx: ProbeContext, repo_id: str) -> list[Finding]:
    specs = [f for f in ctx.inventory.text_files()
             if f.repo_id == repo_id and _SPEC_NAMES.search(f.path)]
    if not specs:
        return []
    routes, skipped = _code_routes(ctx, repo_id)
    out: list[Finding] = []
    for sf in specs:
        text = _read(sf)
        doc = _load_spec(text or "", sf.path)
        if not isinstance(doc, dict):
            continue
        declared = _spec_routes(doc)
        if not declared:
            continue
        if len(routes) < MIN_ROUTES_TO_TRUST:
            # The code side could not be read. Saying nothing is the only
            # honest option; saying "the code implements none of the spec"
            # would be a parser limitation wearing the costume of a finding.
            out.append(Finding(
                rule_id="arbiter/contract.spec-not-compared",
                title=f"`{Path(sf.path).name}` declares {len(declared)} paths, "
                      "and the routes could not be read",
                dimension="drift", severity="info", confidence="high",
                repo_id=repo_id, probe="contract",
                location=Location(path=sf.path, start_line=1, repo_id=repo_id),
                description=(
                    f"Only {len(routes)} route registration(s) were recognised in the "
                    f"code, and {skipped} more were assembled from variables and so "
                    "could not be compared. The specification was NOT checked against "
                    "the implementation — this is a coverage note, not a pass."
                ),
                remediation="No action. Recorded so the spec is not assumed verified.",
                evidence=f"spec:{sf.path}:{len(declared)}-paths",
                controls=["NIST-800-218:PW.7"],
                tags=["contract", "not-assessed"],
            ))
            continue
        missing = sorted(p for p in declared if p not in routes)
        for path in missing[:40]:
            m = re.search(re.escape(path.split("{")[0].rstrip("/")), text or "")
            out.append(Finding(
                rule_id="arbiter/contract.documented-route-not-registered",
                title=f"`{path}` is in the API specification and not in the code",
                dimension="drift", severity="medium", confidence="medium",
                repo_id=repo_id, probe="contract",
                location=Location(path=sf.path,
                                  start_line=_line_of(text or "", m.start()) if m else 1,
                                  logical=path, repo_id=repo_id),
                description=(
                    "The specification declares this path and no route registration "
                    "for it was found. Clients generated from the specification will "
                    "call it and get a 404. Medium confidence because routes built at "
                    "runtime from variables are invisible here — "
                    f"{skipped} such registration(s) were skipped in this repository."
                ),
                remediation="Implement the route, or remove it from the specification.",
                evidence=f"spec-only:{path}",
                controls=["NIST-800-218:PW.7", "NIST-800-53r5:CM-6"],
                tags=["contract", "api"],
            ))
    return out


def _dropped_column_still_referenced(ctx: ProbeContext, repo_id: str) -> list[Finding]:
    """A column a migration removed, still named by application code."""
    added: set[tuple[str, str]] = set()
    dropped: dict[tuple[str, str], tuple[str, int]] = {}
    for f in ctx.inventory.text_files():
        if f.repo_id != repo_id or not _MIGRATION_DIR.search(f.path):
            continue
        text = _read(f)
        if not text:
            continue
        added |= _pairs(_ADD_COLUMN, text)
        for pair in _pairs(_DROP_COLUMN, text):
            m = re.search(re.escape(pair[1]), text, re.I)
            dropped[pair] = (f.path, _line_of(text, m.start()) if m else 1)
    # A column dropped and later re-added is present.
    gone = {p: loc for p, loc in dropped.items() if p not in added}
    if not gone:
        return []

    out: list[Finding] = []
    for (table, column), (mpath, mline) in list(gone.items())[:40]:
        if len(column) < 4:
            continue   # short names collide with everything
        rx = re.compile(rf"""["'`\.\b]{re.escape(column)}["'`\s,\)\]]""")
        for f in ctx.inventory.text_files():
            if (f.repo_id != repo_id or _MIGRATION_DIR.search(f.path)
                    or f.role in ("generated", "docs", "data", "test")):
                continue
            text = _read(f)
            if not text or not rx.search(text):
                continue
            m = rx.search(text)
            out.append(Finding(
                rule_id="arbiter/contract.dropped-column-still-referenced",
                title=f"`{column}` was dropped from `{table}` and is still named in code",
                dimension="drift", severity="medium", confidence="low",
                repo_id=repo_id, probe="contract",
                location=Location(path=f.path, start_line=_line_of(text, m.start()),
                                  logical=f"{table}.{column}", repo_id=repo_id),
                description=(
                    f"A migration removes `{column}` from `{table}`, and this file "
                    "still refers to that name. If it is the column, the query fails "
                    "after the migration runs. Low confidence because a bare name "
                    "match cannot tell a column from a local variable that shares its "
                    "spelling — this points at a place to look, not a proven defect."
                ),
                remediation="Confirm whether this refers to the dropped column.",
                evidence=f"dropped:{table}.{column} dropped-in:{mpath}:{mline}",
                controls=["NIST-800-218:PW.7"],
                tags=["contract", "database"],
                related=[Location(path=mpath, start_line=mline, repo_id=repo_id)],
            ))
            break   # one finding per dropped column is enough to start looking
    return out


def register_contract() -> None:
    from .probes import Probe, register
    register(Probe(
        name="contract",
        dimensions=["drift"],
        checks=3,
        run=probe_contract,
    ))
