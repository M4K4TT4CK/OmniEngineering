"""Component map: what the code is made of, what is reusable, and what a change affects.

Reads a project graph (the dict `omni graph build` writes to .ai/project-graph.json) and groups its source modules
into applications -> components -> sub-components (the modules themselves). For each one it records evidence-based
annotations:

* reusability  -- who consumes it (modules, components, other applications), classified and scored;
* maintainability -- size, churn, tests, recorded failures, coupling and cycles, graded A-E with the reasons;
* dependencies -- what it depends on and what depends on it, directly and transitively (the downtrace and the
  change impact), plus the requirements that touched it and the external packages it uses;
* duplication suspects -- same-named definitions or near-identical files in different components.

Everything is derived from facts already in the graph plus the source files themselves (for similarity), so every
annotation can be traced back to an edge or a line count. Human judgment the graph cannot see (ownership, "this is
platform-specific by design", "deprecated") goes in .ai/component-annotations.json and is merged in.

Stdlib only. Used by `omni components ...`, by `omni graph build` (the components layer) and by the graph viewer.
"""

from __future__ import annotations

import fnmatch
import json
import math
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

MAP_VERSION = "1.0.0"
CONFIG_PATH = ".ai/component-config.json"
ANNOTATIONS_PATH = ".ai/component-annotations.json"
MAP_JSON_PATH = ".ai/component-map.json"
MAP_MD_PATH = ".ai/component-map.md"

LAYER_COMPONENTS = "components"

_DEFAULT_CONFIG: dict[str, Any] = {
    # {"application name": ["glob", ...]}; unset -> one application per top-level directory.
    "applications": {},
    # Explicit components, first match wins: [{"id", "name", "paths": [globs], "description", "role"}].
    "components": [],
    # A directory with more modules than this, and sub-directories of its own, is split one level deeper.
    "split_threshold": 25,
    # Modules never placed in a component (globs). Tests and OmniEngineering tooling are excluded already.
    "exclude": [],
    # Modules that are meant to have no in-project consumers (globs), on top of the built-in heuristics.
    "entry_points": [],
    # Libraries meant to be shared (globs): their modules are reported as shared even before anything uses them.
    "shared_paths": [],
    # Symbol names too generic to count as duplicated effort when two components both define them.
    "duplicate_ignore_names": [],
    # Report file pairs at or above this token similarity (0-1) as duplication suspects.
    "similarity_threshold": 0.5,
}

# Build output, vendored code and hidden directories are never components.
_SKIP_DIRS = {"build", "dist", "out", "target", "node_modules", "vendor", "generated", "Pods", "DerivedData", "coverage", "__pycache__"}
_SOURCE_ROOT_MARKERS = (
    ("src", "main", "java"), ("src", "main", "kotlin"), ("src", "main", "scala"),
    ("src", "main"), ("src",), ("lib",), ("app",),
)
_ENTRY_NAME = re.compile(
    r"^(main|index|app|application|server|cli|manage|setup|wsgi|asgi|__main__|routes?|router|"
    r"[A-Za-z0-9]*(Page|Screen|Controller|Application|Main|Activity|Navigator|Handler|Resource|Endpoint)|"
    r".*\.config|.*\.setup)$"
)
_FRAMEWORK_STEREOTYPES = {"controller", "configuration", "component", "service", "repository", "entity"}
_GENERIC_NAMES = {
    "default", "index", "main", "app", "props", "state", "config", "constants", "styles", "style", "types", "utils",
    "helpers", "init", "setup", "test", "tests", "run", "build", "render", "handler", "handle", "get", "set", "update",
    "create", "delete", "load", "save", "parse", "format", "validate", "constructor", "tostring", "equals", "hashcode",
}
_ROLE_WORDS = [
    (("pages", "page", "screens", "screen", "views", "routes"), "ui-screens", "Screens / pages"),
    (("components", "widgets", "atoms", "molecules", "organisms", "templates", "ui"), "ui-components", "UI components"),
    (("hooks",), "hooks", "React hooks (state and data logic)"),
    # singular "service" is left to the stereotype check: a Java service package is business logic, not an API client
    (("services", "api", "client", "clients", "requests"), "services", "Services / API access"),
    (("web", "controller", "controllers", "rest", "endpoints", "resource", "resources", "handlers"), "api-endpoints", "API endpoints"),
    (("migration", "migrations", "flyway", "liquibase"), "database-schema", "Database schema (migrations)"),
    (("repository", "repositories", "dao", "persistence", "db", "database", "offline", "storage"), "data-access", "Data access / storage"),
    (("domain", "model", "models", "entity", "entities", "schema"), "domain-model", "Domain model"),
    (("dto", "dtos", "contracts", "types", "interfaces"), "data-contracts", "Types / data contracts"),
    (("config", "configuration", "settings"), "configuration", "Configuration"),
    (("security", "auth", "authn", "authz"), "security", "Authentication / security"),
    (("shared", "common", "lib", "libs", "utils", "util", "helpers", "logic", "core"), "library", "Shared library code"),
    (("navigation", "nav"), "navigation", "Navigation"),
    (("chat", "messaging", "messages", "notifications", "push"), "messaging", "Messaging / notifications"),
    (("assistant", "bot", "chatbot", "agent", "ai", "llm"), "assistant", "Assistant / AI"),
    (("public", "static", "www"), "static-assets", "Static files served as-is"),
    (("theme", "styles", "style", "assets"), "presentation", "Styling / theme"),
    (("scripts", "tools", "bin"), "tooling", "Scripts / tooling"),
]
_REUSE_ORDER = ["cross-app", "shared", "reused", "single-use", "entry-point", "framework-wired", "test-only", "unreferenced"]
REUSE_HELP = {
    "cross-app": "Used by another application (e.g. web and mobile). The most valuable reuse; changes ripple across apps.",
    "shared": "Used by modules in two or more other components. A real building block.",
    "reused": "Used by two or more modules. Reusable within its area.",
    "single-use": "Used by exactly one module. Candidate to inline, or to generalize if a second user appears.",
    "entry-point": "Nothing imports it because it is a top of the tree (page, screen, controller, main).",
    "framework-wired": "Nothing imports it, but a framework instantiates it (Spring component, configuration, entity).",
    "test-only": "Only tests use it. Possibly dead code kept alive by its tests.",
    "unreferenced": "Nothing in the project uses it. Possibly dead code -- or used by something the graph cannot see.",
}
COMPONENT_HELP = {
    "cross-app": "Another application uses it. Shared across apps; a change here ripples into each of them.",
    "library": "Most of its files are reused, by two or more other components. A building block: extend it rather than copy it.",
    "feature": "Used by one or two other components (or mostly by itself). Application-specific feature code.",
    "top-level": "Nothing else uses it: pages, screens, controllers, the app shell, migrations.",
    "unreferenced": "None of its files are used. Possibly dead code.",
}
GRADE_HELP = {
    "A": "Easy to maintain: small, tested, stable.",
    "B": "Healthy, with a minor concern.",
    "C": "Watch: one or two real concerns.",
    "D": "Hard to maintain: several concerns; plan work before extending it.",
    "E": "High risk: large, untested, churning or failure-prone; refactor before adding to it.",
}


# --------------------------------------------------------------------------------------------------------------------
# configuration and annotations
# --------------------------------------------------------------------------------------------------------------------


def _read_json(path: Path, problems: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        problems.append(f"{path.name}: unreadable ({exc}); ignored")
        return None


def load_config(root: Path, problems: list[str] | None = None) -> dict[str, Any]:
    found: list[str] = []
    config = json.loads(json.dumps(_DEFAULT_CONFIG))
    data = _read_json(root / CONFIG_PATH, found)
    if isinstance(data, dict):
        for key, value in data.items():
            if key.startswith("_"):
                continue
            if key not in _DEFAULT_CONFIG:
                found.append(f"{CONFIG_PATH}: unknown key '{key}' ignored")
                continue
            default = _DEFAULT_CONFIG[key]
            if type(value) is not type(default) and not (isinstance(default, float) and isinstance(value, int)):
                found.append(f"{CONFIG_PATH}: '{key}' has the wrong type; using the default")
                continue
            config[key] = value
    elif data is not None:
        found.append(f"{CONFIG_PATH}: must be a JSON object")
    if problems is not None:
        problems.extend(found)
    return config


def load_annotations(root: Path, problems: list[str] | None = None) -> dict[str, dict[str, Any]]:
    found: list[str] = []
    data = _read_json(root / ANNOTATIONS_PATH, found)
    out: dict[str, dict[str, Any]] = {"components": {}, "modules": {}}
    if isinstance(data, dict):
        for section in ("components", "modules"):
            value = data.get(section) or {}
            if isinstance(value, dict):
                out[section] = {k: v for k, v in value.items() if isinstance(v, dict)}
            else:
                found.append(f"{ANNOTATIONS_PATH}: '{section}' must be an object keyed by id")
    elif data is not None:
        found.append(f"{ANNOTATIONS_PATH}: must be a JSON object")
    if problems is not None:
        problems.extend(found)
    return out


def _match(path: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        pattern = pattern.rstrip("/")
        if fnmatch.fnmatch(path, pattern) or path == pattern or path.startswith(pattern + "/"):
            return True
    return False


# --------------------------------------------------------------------------------------------------------------------
# reading the graph
# --------------------------------------------------------------------------------------------------------------------

_SYMBOL_KINDS = {"class", "interface", "function", "method", "table", "enum", "struct", "trait", "type"}
_DEP_EDGE_TYPES = {"imports", "uses", "inherits", "implements", "calls", "references", "maps_to"}
_JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
_FAMILY = {"typescript": "js", "javascript": "js", "java": "jvm", "kotlin": "jvm", "scala": "jvm", "groovy": "jvm",
           "c": "c", "cpp": "c", "objc": "c", "csharp": "dotnet", "fsharp": "dotnet"}


def _family(language: str | None) -> str | None:
    return _FAMILY.get(language or "", language)


def _is_test(node: dict[str, Any]) -> bool:
    attrs = node.get("attrs") or {}
    return bool(attrs.get("test")) or node.get("layer") == "assurance"


def _is_tooling(node: dict[str, Any]) -> bool:
    attrs = node.get("attrs") or {}
    return bool(attrs.get("tooling")) or node.get("layer") == "workspace"


def _noext(path: str) -> str:
    p = PurePosixPath(path)
    return str(p.with_suffix("")) if p.suffix else path


class _Index:
    """Lookups over the graph: modules, symbol -> module, and import resolution for edges the graph left external."""

    def __init__(self, graph: dict[str, Any]) -> None:
        self.nodes: dict[str, dict[str, Any]] = {n["id"]: n for n in graph.get("nodes", [])}
        self.edges: list[dict[str, Any]] = graph.get("edges", [])
        self.modules: dict[str, dict[str, Any]] = {
            nid: n for nid, n in self.nodes.items() if n.get("kind") == "module" and n.get("file")
        }
        self.by_file: dict[str, str] = {n["file"]: nid for nid, n in self.modules.items()}
        self.by_noext: dict[str, str] = {}
        self.by_last: dict[str, list[str]] = defaultdict(list)
        for nid, n in self.modules.items():
            stem = _noext(n["file"])
            self.by_noext.setdefault(stem, nid)
            self.by_last[stem.rsplit("/", 1)[-1]].append(nid)

    def module_of(self, node_id: str) -> str | None:
        if node_id in self.modules:
            return node_id
        node = self.nodes.get(node_id)
        if node and node.get("file"):
            return self.by_file.get(node["file"])
        if node_id.startswith("file:"):
            return self.by_file.get(node_id[5:])
        return None

    def resolve_external(self, source_module: str, target: str) -> str | None:
        """An import the graph kept as external:<spec> that is really project code."""
        spec = target[len("external:"):] if target.startswith("external:") else target
        spec = spec.split("?")[0].strip().strip("'\"")
        if not spec:
            return None
        src_file = self.modules[source_module]["file"]
        if spec.startswith(".") and src_file.endswith(".py") and "/" not in spec:
            dots = len(spec) - len(spec.lstrip("."))
            base = PurePosixPath(src_file).parent
            for _ in range(dots - 1):
                base = base.parent
            stem = str(base / spec[dots:].replace(".", "/")) if spec[dots:] else str(base)
            return self.by_file.get(stem + ".py") or self.by_file.get(stem + "/__init__.py")
        if spec.startswith("."):
            base = PurePosixPath(src_file).parent
            joined = _normpath(str(base / spec))
            if joined is None:
                return None
            for candidate in [joined] + [joined + ext for ext in _JS_EXTS + (".py",)] + [joined + "/index" + ext for ext in _JS_EXTS] + [joined + "/__init__.py"]:
                if candidate in self.by_file:
                    return self.by_file[candidate]
            return self.by_noext.get(joined)
        if "." in spec and "/" not in spec:
            dotted = spec.rstrip(".*").replace(".", "/")
            parts = dotted.split("/")
            # a.b.C (class), or a.b.C.member: try the longest prefix that names a module.
            for cut in range(len(parts), 0, -1):
                tail = "/".join(parts[:cut])
                for nid in self.by_last.get(parts[cut - 1], []):
                    if _noext(self.modules[nid]["file"]).endswith("/" + tail) or _noext(self.modules[nid]["file"]) == tail:
                        return nid
                if cut <= max(1, len(parts) - 2):
                    break
        return None


def _normpath(path: str) -> str | None:
    out: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not out:
                return None
            out.pop()
        else:
            out.append(part)
    return "/".join(out)


# --------------------------------------------------------------------------------------------------------------------
# grouping modules into applications and components
# --------------------------------------------------------------------------------------------------------------------


def _source_root(parts: list[str]) -> int:
    """Index just past the source root (src/, src/main/java/, ...) in a path's directory parts, or 1 (the app dir)."""
    for marker in _SOURCE_ROOT_MARKERS:
        n = len(marker)
        for i in range(1, len(parts) - n + 1):
            if tuple(parts[i:i + n]) == marker:
                return i + n
    return 1


def _role_for(component_path: str, members: list[dict[str, Any]]) -> tuple[str, str]:
    """Role from the component's own directory name, else its classes' framework stereotype, else its parent's name."""
    words = [w.lower() for w in re.split(r"[/_\-.]", component_path) if w]

    def by_word(word: str) -> tuple[str, str] | None:
        for keys, role, label in _ROLE_WORDS:
            if word in keys:
                return role, label
        return None

    if words and by_word(words[-1]):
        return by_word(words[-1])  # type: ignore[return-value]
    stereotypes = [(m.get("attrs") or {}).get("stereotype") for m in members]
    stereotypes = [s for s in stereotypes if s]
    if stereotypes:
        top = max(sorted(set(stereotypes)), key=stereotypes.count)
        found = {"controller": ("api-endpoints", "API endpoints"), "service": ("business-logic", "Business logic"),
                 "repository": ("data-access", "Data access / storage"), "entity": ("domain-model", "Domain model"),
                 "configuration": ("configuration", "Configuration")}.get(top)
        if found:
            return found
    for word in reversed(words[:-1]):
        if by_word(word):
            return by_word(word)  # type: ignore[return-value]
    return "module-group", "Module group"


def _group(index: _Index, config: dict[str, Any], tooling: set[str]) -> tuple[dict[str, str], dict[str, str], dict[str, dict[str, Any]]]:
    """module id -> component id, module id -> application, component id -> {path, app, explicit spec}."""
    apps_cfg: dict[str, list[str]] = config.get("applications") or {}
    explicit: list[dict[str, Any]] = [c for c in config.get("components") or [] if isinstance(c, dict) and c.get("paths")]
    exclude = list(config.get("exclude") or [])
    module_app: dict[str, str] = {}
    module_comp: dict[str, str] = {}
    comps: dict[str, dict[str, Any]] = {}

    candidates = []
    for nid, node in index.modules.items():
        path = node["file"]
        dirs = path.split("/")[:-1]
        if nid in tooling or _is_test(node) or _is_tooling(node) or _match(path, exclude) \
                or any(d.startswith(".") or d in _SKIP_DIRS for d in dirs):
            continue
        candidates.append((nid, path))

    def app_of(path: str) -> str:
        for name, globs in apps_cfg.items():
            if _match(path, globs):
                return name
        return path.split("/", 1)[0] if "/" in path else "(root)"

    # explicit components first; everything else is grouped by directory under its source root
    pending: dict[str, dict[str, list[tuple[str, list[str]]]]] = defaultdict(lambda: defaultdict(list))
    for nid, path in candidates:
        app = app_of(path)
        module_app[nid] = app
        spec = next((c for c in explicit if _match(path, c["paths"])), None)
        if spec:
            cid = spec.get("id") or spec.get("name")
            module_comp[nid] = cid
            comps.setdefault(cid, {"path": cid, "app": app, "spec": spec})
            continue
        parts = path.split("/")
        root = "/".join(parts[:_source_root(parts)]) if len(parts) > 1 else ""
        pending[app][root].append((nid, parts))

    threshold = int(config.get("split_threshold") or 25)
    for app, roots in pending.items():
        for root, members in roots.items():
            depth = len(root.split("/")) if root else 0
            # a package prefix every module shares (com/acme/api) is not a component boundary
            dir_lists = [p[:-1] for _, p in members]
            while len({d[depth] if len(d) > depth else None for d in dir_lists} - {None}) == 1 and all(len(d) > depth for d in dir_lists):
                depth += 1
            _assign(members, depth, threshold, module_comp, comps, app)
    return module_comp, module_app, comps


def _assign(members: list[tuple[str, list[str]]], depth: int, threshold: int, module_comp: dict[str, str],
            comps: dict[str, dict[str, Any]], app: str) -> None:
    buckets: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    loose: list[tuple[str, list[str]]] = []
    for nid, parts in members:
        dirs = parts[:-1]
        if len(dirs) > depth:
            buckets["/".join(dirs[: depth + 1])].append((nid, parts))
        else:
            loose.append((nid, parts))
    for path, items in buckets.items():
        subdirs = {p[depth + 1] for _, p in items if len(p) - 1 > depth + 1}
        if len(items) > threshold and len(subdirs) >= 2:
            _assign(items, depth + 1, threshold, module_comp, comps, app)
            continue
        for nid, _ in items:
            module_comp[nid] = path
        comps.setdefault(path, {"path": path, "app": app, "spec": None})
    if loose:
        base = "/".join(loose[0][1][:depth]) if depth else app
        cid = (base or app) + " (files)" if buckets else (base or app)
        for nid, _ in loose:
            module_comp[nid] = cid
        comps.setdefault(cid, {"path": base or app, "app": app, "spec": None})


# --------------------------------------------------------------------------------------------------------------------
# similarity (duplication suspects)
# --------------------------------------------------------------------------------------------------------------------

_COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*|#[^\n]*|\"\"\".*?\"\"\"", re.S)
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\sA-Za-z0-9_]")
_GENERATED_RE = re.compile(r"(?:@generated|GENERATED\b|DO NOT EDIT)(?:\s+from\s+(\S+))?", re.I)


def _read_lines(root: Path, relpath: str, cache: dict[str, list[str] | None]) -> list[str] | None:
    if relpath not in cache:
        try:
            cache[relpath] = (root / relpath).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            cache[relpath] = None
    return cache[relpath]


def _shingles(text: str, k: int = 4) -> set[int]:
    tokens = _TOKEN_RE.findall(_COMMENT_RE.sub(" ", text))
    if len(tokens) < k:
        return {hash(" ".join(tokens))} if tokens else set()
    return {hash(" ".join(tokens[i:i + k])) for i in range(len(tokens) - k + 1)}


def _jaccard(a: set[int], b: set[int]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# --------------------------------------------------------------------------------------------------------------------
# the analysis
# --------------------------------------------------------------------------------------------------------------------


def analyze(graph: dict[str, Any], root: Path, config: dict[str, Any] | None = None,
            annotations: dict[str, dict[str, Any]] | None = None, tooling_paths: Iterable[str] = ()) -> dict[str, Any]:
    problems: list[str] = []
    if config is None:
        config = load_config(root, problems)
    if annotations is None:
        annotations = load_annotations(root, problems)
    index = _Index(graph)
    tooling = {nid for nid, n in index.modules.items() if _match(n["file"], tooling_paths)}
    module_comp, module_app, comps = _group(index, config, tooling)
    file_cache: dict[str, list[str] | None] = {}

    # generated copies (a sync script's output) count as their source, not as separate code
    copy_of: dict[str, str] = {}
    for nid in list(module_comp):
        lines = _read_lines(root, index.modules[nid]["file"], file_cache) or []
        head = "\n".join(lines[:3])
        m = _GENERATED_RE.search(head)
        if m:
            src = (m.group(1) or "").strip("`'\".,:;")
            if src and src in index.by_file and index.by_file[src] in module_comp:
                copy_of[nid] = index.by_file[src]

    def canon(mid: str | None) -> str | None:
        return copy_of.get(mid, mid) if mid else mid

    # ---- module-level dependencies -------------------------------------------------------------------------------
    deps: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))  # src -> tgt -> edge types
    test_users: dict[str, set[str]] = defaultdict(set)
    externals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for edge in index.edges:
        etype = edge.get("type")
        if etype == "verifies":
            src = index.module_of(edge["source"])
            tgt = canon(index.module_of(edge["target"]))
            if src and tgt and tgt in module_comp:
                test_users[tgt].add(src)
            continue
        if etype not in _DEP_EDGE_TYPES:
            continue
        src = index.module_of(edge["source"])
        if not src or src in copy_of:
            # a generated copy's own links duplicate its source's (and its name-resolved calls can land in the
            # app it was copied into), so only links *to* a copy count -- as links to the source
            continue
        target = edge["target"]
        tgt = index.module_of(target)
        if tgt is None and target.startswith("external:") and src in index.modules:
            tgt = index.resolve_external(src, target)
            if tgt is None and etype == "imports" and src in module_comp:
                pkg = _package_name(target[len("external:"):])
                if pkg:
                    externals[canon(src)][pkg] += 1
        if not tgt or tgt == src:
            continue
        src_node = index.modules.get(src)
        if src_node is not None and _is_test(src_node):
            tgt_c = canon(tgt)
            if tgt_c in module_comp:
                test_users[tgt_c].add(src)
            continue
        src_c, tgt_c = canon(src), canon(tgt)
        if src_c in module_comp and tgt_c in module_comp and src_c != tgt_c:
            # Calls and uses are partly resolved by bare name, which can land in another language or application
            # (a mobile screen's addStep() "resolving" to the web page's addStep). Only same-family links count, and
            # links between applications only when they are real imports.
            if _family(index.modules[src_c].get("language")) != _family(index.modules[tgt_c].get("language")):
                continue
            if module_app.get(src) != module_app.get(tgt) and etype != "imports":
                continue
            deps[src_c][tgt_c].add(etype)

    _text_links(root, index, module_comp, module_app, copy_of, deps, file_cache)

    real_modules = [m for m in module_comp if m not in copy_of]
    consumers: dict[str, set[str]] = defaultdict(set)
    consumer_sites: dict[str, set[str]] = defaultdict(set)  # including the generated copies' importers, by copy
    for src, targets in deps.items():
        for tgt in targets:
            consumers[tgt].add(src)
    # importers of a generated copy are consumers of its source, and they live in the copy's application
    for copy, source in copy_of.items():
        consumer_sites[source].add(module_app[copy])

    # ---- history, governance, assurance facts per module ----------------------------------------------------------
    commit_dates: dict[str, str] = {}
    for nid, node in index.nodes.items():
        if node.get("kind") == "commit":
            commit_dates[nid] = str((node.get("attrs") or {}).get("date") or "")[:10]
    latest = max((d for d in commit_dates.values() if d), default="")
    recent_cut = _days_before(latest, 90)
    churn: dict[str, set[str]] = defaultdict(set)
    reqs: dict[str, set[str]] = defaultdict(set)
    failures: dict[str, set[str]] = defaultdict(set)
    for edge in index.edges:
        etype = edge.get("type")
        if etype not in ("modifies", "touches", "affects"):
            continue
        mid = canon(index.module_of(edge["target"]))
        if not mid or mid not in module_comp:
            continue
        if etype == "modifies":
            churn[mid].add(edge["source"])
        elif etype == "touches":
            req = index.nodes.get(edge["source"], {})
            reqs[mid].add(req.get("name") or edge["source"].split(":", 1)[-1])
        else:
            fail = index.nodes.get(edge["source"], {})
            failures[mid].add(fail.get("name") or edge["source"].split(":", 1)[-1])

    # top-level definitions per module (its public surface, roughly)
    defs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in index.edges:
        if edge.get("type") != "defines" or edge["source"] not in module_comp:
            continue
        sym = index.nodes.get(edge["target"])
        if sym and sym.get("kind") in _SYMBOL_KINDS:
            defs[edge["source"]].append(sym)

    # ---- duplication suspects -------------------------------------------------------------------------------------
    duplicates = _find_duplicates(root, index, module_comp, module_app, copy_of, defs, config, file_cache)
    dup_of: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in duplicates:
        for side in d["modules"]:
            dup_of[side].append(d)

    # ---- per-module records ----------------------------------------------------------------------------------------
    shared_globs = list(config.get("shared_paths") or [])
    entry_globs = list(config.get("entry_points") or [])
    modules_out: dict[str, dict[str, Any]] = {}
    for mid in real_modules:
        node = index.modules[mid]
        path = node["file"]
        comp = module_comp[mid]
        app = module_app[mid]
        users = consumers.get(mid, set())
        user_comps = {module_comp[u] for u in users if u in module_comp} - {comp}
        user_apps = ({module_app[u] for u in users if u in module_app} | consumer_sites.get(mid, set())) - {app}
        if mid in {s for s in copy_of.values()}:
            user_apps |= {module_app[c] for c, s in copy_of.items() if s == mid}
            user_apps.discard(app)
        stereos = {(s.get("attrs") or {}).get("stereotype") for s in defs.get(mid, [])} - {None}
        name = PurePosixPath(path).stem
        # scripts are run, and files under public/ or static/ are served as-is: nothing imports them by design
        dirs = path.split("/")[:-1]
        served = any(d in ("public", "static", "www") for d in dirs)
        is_entry = bool(_ENTRY_NAME.match(name)) or _match(path, entry_globs) or served or name.endswith(".d") \
            or node.get("language") in ("bash", "shell", "powershell", "batch") or any(d in ("scripts", "bin", "tools") for d in dirs)
        # SQL migrations and init scripts are run by a tool (Flyway, Liquibase, an init container), never imported
        framework_run = node.get("language") == "sql"
        category = _reuse_category(len(users), len(user_comps), len(user_apps), is_entry, bool(stereos & _FRAMEWORK_STEREOTYPES) or framework_run,
                                   len(test_users.get(mid, ())), _match(path, shared_globs))
        loc = int(node.get("end_line") or 0)
        fan_out = len(deps.get(mid, {}))
        recent = sum(1 for c in churn.get(mid, ()) if commit_dates.get(c, "") >= recent_cut) if recent_cut else 0
        maint = _maintainability(loc=loc, largest=loc, tested=bool(test_users.get(mid)), tested_share=None,
                                 commits=len(churn.get(mid, ())), recent=recent, failures=len(failures.get(mid, ())),
                                 fan_out=fan_out, in_cycle=False, duplicated=bool(dup_of.get(mid)), unit="module",
                                 schema=node.get("language") == "sql")
        reuse_score, reuse_why = _reuse_score(len(users), len(user_comps), len(user_apps), fan_out, category)
        rec = {
            "id": mid,
            "file": path,
            "name": name,
            "component": comp,
            "application": app,
            "language": node.get("language"),
            "loc": loc,
            "exports": sorted({s["name"] for s in defs.get(mid, []) if s.get("kind") != "method"})[:20],
            "stereotypes": sorted(stereos),
            "reuse": {"category": category, "score": reuse_score, "why": reuse_why,
                      "consumers": len(users), "consumer_components": sorted(user_comps), "consumer_apps": sorted(user_apps)},
            "maintainability": maint,
            "depends_on": sorted(deps.get(mid, {})),
            "used_by": sorted(users),
            "tests": sorted(test_users.get(mid, ())),
            "commits": len(churn.get(mid, ())),
            "recent_commits": recent,
            "requirements": _sort_ids(reqs.get(mid, ())),
            "failures": _sort_ids(failures.get(mid, ())),
            "external_packages": dict(sorted(externals.get(mid, {}).items(), key=lambda kv: (-kv[1], kv[0]))[:12]),
            "copies": sorted(c for c, s in copy_of.items() if s == mid),
            "duplicates": [d["id"] for d in dup_of.get(mid, [])],
        }
        note = annotations.get("modules", {}).get(mid) or annotations.get("modules", {}).get(path)
        if note:
            rec["annotation"] = note
            if note.get("reuse"):
                rec["reuse"]["category"] = note["reuse"]
                rec["reuse"]["overridden"] = True
        modules_out[mid] = rec

    # ---- component records -----------------------------------------------------------------------------------------
    comp_members: dict[str, list[str]] = defaultdict(list)
    for mid in real_modules:
        comp_members[module_comp[mid]].append(mid)
    comp_deps: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for src, targets in deps.items():
        for tgt in targets:
            a, b = module_comp.get(src), module_comp.get(tgt)
            if a and b and a != b:
                comp_deps[a][b] += 1
    cycles = _cycles({c: set(t) for c, t in comp_deps.items()}, set(comp_members))
    in_cycle = {c for cyc in cycles for c in cyc}
    rev: dict[str, set[str]] = defaultdict(set)
    for a, targets in comp_deps.items():
        for b in targets:
            rev[b].add(a)

    components_out: dict[str, dict[str, Any]] = {}
    for cid, members in comp_members.items():
        info = comps.get(cid, {"path": cid, "app": module_app[members[0]], "spec": None})
        spec = info.get("spec") or {}
        member_nodes = [index.modules[m] for m in members]
        member_defs = [s for m in members for s in defs.get(m, [])]
        role, role_label = (spec.get("role"), spec.get("role")) if spec.get("role") else _role_for(info["path"], member_defs)
        mset = set(members)
        outside_users = {u for m in members for u in consumers.get(m, ()) if u not in mset}
        user_comps = {module_comp[u] for u in outside_users if u in module_comp} - {cid}
        user_apps = {module_app[u] for u in outside_users if u in module_app} - {info["app"]}
        for m in members:
            user_apps |= set(modules_out[m]["reuse"]["consumer_apps"])
        user_apps.discard(info["app"])
        locs = [modules_out[m]["loc"] for m in members]
        tested = [m for m in members if modules_out[m]["tests"]]
        commits = set().union(*(churn.get(m, set()) for m in members)) if members else set()
        recent = sum(1 for c in commits if commit_dates.get(c, "") >= recent_cut) if recent_cut else 0
        fails = set().union(*(failures.get(m, set()) for m in members)) if members else set()
        c_reqs = set().union(*(reqs.get(m, set()) for m in members)) if members else set()
        fan_out = len(comp_deps.get(cid, {}))
        maint = _maintainability(loc=sum(locs), largest=max(locs or [0]), tested=bool(tested), tested_share=len(tested) / len(members),
                                 commits=len(commits), recent=recent, failures=len(fails), fan_out=fan_out,
                                 in_cycle=cid in in_cycle, duplicated=any(modules_out[m]["duplicates"] for m in members), unit="component",
                                 files=len(members), schema=all(n.get("language") == "sql" for n in member_nodes))
        category = _component_category(len(user_comps), len(user_apps), [modules_out[m]["reuse"]["category"] for m in members])
        reuse_score, reuse_why = _reuse_score(len(outside_users), len(user_comps), len(user_apps), fan_out, category)
        ext: dict[str, int] = defaultdict(int)
        for m in members:
            for pkg, n in modules_out[m]["external_packages"].items():
                ext[pkg] += n
        reusable = sorted((m for m in members if modules_out[m]["reuse"]["category"] in ("cross-app", "shared", "reused")),
                          key=lambda m: (-modules_out[m]["reuse"]["score"], m))
        rec = {
            "id": cid,
            "name": spec.get("name") or _display_name(cid, info["app"]),
            "path": info["path"],
            "application": info["app"],
            "role": role,
            "role_label": role_label,
            "description": spec.get("description", ""),
            "modules": sorted(members),
            "module_count": len(members),
            "loc": sum(locs),
            "largest_module": max(members, key=lambda m: modules_out[m]["loc"]) if members else None,
            "languages": sorted({n.get("language") for n in member_nodes if n.get("language")}),
            "reuse": {"category": category, "score": reuse_score, "why": reuse_why, "consumers": len(outside_users),
                      "consumer_components": sorted(user_comps), "consumer_apps": sorted(user_apps),
                      "reusable_modules": reusable},
            "maintainability": maint,
            "depends_on": dict(sorted(comp_deps.get(cid, {}).items(), key=lambda kv: (-kv[1], kv[0]))),
            "used_by": dict(sorted(((a, comp_deps[a][cid]) for a in rev.get(cid, ())), key=lambda kv: (-kv[1], kv[0]))),
            "downtrace": sorted(_reach(cid, comp_deps)),
            "impact": sorted(_reach(cid, {k: dict.fromkeys(v, 1) for k, v in rev.items()})),
            "in_cycle": cid in in_cycle,
            "tests": len({t for m in members for t in modules_out[m]["tests"]}),
            "tested_modules": len(tested),
            "commits": len(commits),
            "recent_commits": recent,
            "requirements": _sort_ids(c_reqs),
            "failures": _sort_ids(fails),
            "external_packages": dict(sorted(ext.items(), key=lambda kv: (-kv[1], kv[0]))[:12]),
        }
        note = annotations.get("components", {}).get(cid)
        if note:
            rec["annotation"] = note
            if note.get("reuse"):
                rec["reuse"]["category"] = note["reuse"]
                rec["reuse"]["overridden"] = True
        components_out[cid] = rec

    for name, note in annotations.get("components", {}).items():
        if name not in components_out:
            problems.append(f"{ANNOTATIONS_PATH}: component '{name}' not found in the map (renamed or removed?)")

    apps_out: dict[str, dict[str, Any]] = {}
    for cid, rec in components_out.items():
        app = apps_out.setdefault(rec["application"], {"id": rec["application"], "components": [], "modules": 0, "loc": 0, "languages": set(), "depends_on": defaultdict(int)})
        app["components"].append(cid)
        app["modules"] += rec["module_count"]
        app["loc"] += rec["loc"]
        app["languages"].update(rec["languages"])
        for other, n in rec["depends_on"].items():
            other_app = components_out[other]["application"]
            if other_app != rec["application"]:
                app["depends_on"][other_app] += n
    for copy, source in copy_of.items():
        a, b = module_app[copy], module_app.get(source)
        if b and a != b:
            apps_out[a]["depends_on"][b] += 1
    for app in apps_out.values():
        app["components"].sort()
        app["languages"] = sorted(app["languages"])
        app["depends_on"] = dict(sorted(app["depends_on"].items()))

    return {
        "version": MAP_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "graph_generated_at": graph.get("generated_at"),
        "history_through": latest or None,
        "legend": {"reuse": REUSE_HELP, "component_reuse": COMPONENT_HELP, "grades": GRADE_HELP},
        "applications": dict(sorted(apps_out.items())),
        "components": dict(sorted(components_out.items())),
        "modules": dict(sorted(modules_out.items())),
        "generated_copies": dict(sorted(copy_of.items())),
        "duplicates": duplicates,
        "cycles": cycles,
        "problems": problems,
    }


_JS_SPEC_RE = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+)['"]([^'"\n]+)['"]""", re.M)
_JVM_EXTS = (".java", ".kt", ".scala", ".groovy")


def _text_links(root: Path, index: _Index, module_comp: dict[str, str], module_app: dict[str, str], copy_of: dict[str, str],
                deps: dict[str, dict[str, set[str]]], cache: dict[str, list[str] | None]) -> None:
    """Links the code graph does not record: JS/TS re-exports and imports it missed, and JVM same-package references.

    `export { X } from "./X"` (an index file re-exporting a component) and a Java class using another class from its own
    package (no import needed) are real dependencies that would otherwise leave the target looking unreferenced.
    """
    by_dir: dict[str, list[str]] = defaultdict(list)
    for mid in module_comp:
        if mid in copy_of:
            continue
        path = index.modules[mid]["file"]
        by_dir[str(PurePosixPath(path).parent)].append(mid)
        if path.endswith(_JS_EXTS):
            text = "\n".join(_read_lines(root, path, cache) or [])
            for spec in _JS_SPEC_RE.findall(text):
                if not spec.startswith("."):
                    continue
                tgt = index.resolve_external(mid, spec)
                tgt = copy_of.get(tgt, tgt) if tgt else None
                if tgt and tgt != mid and tgt in module_comp and module_app.get(tgt) == module_app.get(mid):
                    deps[mid][tgt].add("imports")
    for folder, mids in by_dir.items():
        jvm = [m for m in mids if index.modules[m]["file"].endswith(_JVM_EXTS)]
        if len(jvm) < 2:
            continue
        names = {PurePosixPath(index.modules[m]["file"]).stem: m for m in jvm}
        pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")\b")
        for mid in jvm:
            own = PurePosixPath(index.modules[mid]["file"]).stem
            text = _COMMENT_RE.sub(" ", "\n".join(_read_lines(root, index.modules[mid]["file"], cache) or []))
            for name in set(pattern.findall(text)) - {own}:
                deps[mid][names[name]].add("references")


def _package_name(spec: str) -> str | None:
    spec = spec.strip("'\"")
    if not spec or spec.startswith("."):
        return None
    if spec.startswith("@"):
        return "/".join(spec.split("/")[:2])
    if "/" in spec:
        return spec.split("/")[0]
    if "." in spec:  # java.util.List -> java.util ; org.springframework.http.X -> org.springframework
        parts = spec.split(".")
        return ".".join(parts[:2]) if parts[0] in ("java", "javax", "jakarta", "kotlin") else ".".join(parts[:2])
    return spec


def _sort_ids(ids: Iterable[str]) -> list[str]:
    def key(x: str) -> tuple[str, int, str]:
        m = re.match(r"([A-Za-z]+)-(\d+)", x)
        return (m.group(1), int(m.group(2)), x) if m else (x, 0, x)
    return sorted(set(ids), key=key)


def _days_before(day: str, days: int) -> str:
    try:
        d = date.fromisoformat(day[:10])
    except ValueError:
        return ""
    return date.fromordinal(d.toordinal() - days).isoformat()


def _display_name(cid: str, app: str) -> str:
    if cid == f"{app} (files)":
        return f"{app} · top-level files"
    rest = cid[len(app) + 1:] if cid.startswith(app + "/") else cid
    for marker in ("src/main/java/", "src/main/kotlin/", "src/"):
        if marker in rest:
            rest = rest.split(marker, 1)[1]
    return f"{app} · {rest}" if rest and rest != app else app


def _reuse_category(users: int, user_comps: int, user_apps: int, entry: bool, framework: bool, tests: int, shared: bool) -> str:
    if user_apps:
        return "cross-app"
    if user_comps >= 2 or (shared and users):
        return "shared"
    if users >= 2:
        return "reused"
    if users == 1:
        return "single-use"
    if entry:
        return "entry-point"
    if framework:
        return "framework-wired"
    if shared:
        return "shared"
    if tests:
        return "test-only"
    return "unreferenced"


def _component_category(user_comps: int, user_apps: int, member_categories: list[str]) -> str:
    if user_apps:
        return "cross-app"
    if member_categories and all(c in ("unreferenced", "test-only") for c in member_categories):
        return "unreferenced"
    reusable = sum(1 for c in member_categories if c in ("cross-app", "shared", "reused"))
    if user_comps >= 2 and reusable * 2 >= len(member_categories):
        return "library"
    if user_comps:
        return "feature"
    return "top-level"


def _reuse_score(users: int, user_comps: int, user_apps: int, fan_out: int, category: str) -> tuple[int, list[str]]:
    """0-100. Half breadth (how many places use it), half portability (cross-app use, few dependencies of its own)."""
    if category in ("entry-point", "framework-wired", "top-level"):
        return 0, ["top of the tree: not meant to be reused"]
    why = []
    breadth = min(1.0, math.log2(1 + users) / math.log2(1 + 8))
    reach = min(1.0, user_comps / 3)
    portable = 1.0 if user_apps else 0.0
    lean = 1.0 / (1.0 + fan_out / 5)
    score = round(100 * (0.30 * breadth + 0.25 * reach + 0.25 * portable + 0.20 * lean))
    why.append(f"{users} consumer module(s)")
    if user_comps:
        why.append(f"used from {user_comps} other component(s)")
    if user_apps:
        why.append(f"used by {user_apps} other application(s)")
    why.append(f"depends on {fan_out} other module(s)" if fan_out else "no dependencies of its own")
    return score, why


def _maintainability(*, loc: int, largest: int, tested: bool, tested_share: float | None, commits: int, recent: int,
                     failures: int, fan_out: int, in_cycle: bool, duplicated: bool, unit: str, files: int = 1,
                     schema: bool = False) -> dict[str, Any]:
    score = 100
    reasons: list[str] = []
    big, huge = (400, 800)
    if largest > huge:
        score -= 25; reasons.append(f"very large file ({largest} lines)" if unit == "module" else f"largest file is {largest} lines")
    elif largest > big:
        score -= 12; reasons.append(f"large file ({largest} lines)" if unit == "module" else f"largest file is {largest} lines")
    if unit == "component" and loc > 4000:
        score -= 8; reasons.append(f"{loc} lines in total")
    if schema:
        pass  # migrations are exercised through the code that maps them, and append-only by design
    elif tested_share is None:
        if not tested:
            score -= 20; reasons.append("no test exercises it")
    elif tested_share == 0:
        score -= 20; reasons.append("no test exercises any of its files")
    elif tested_share < 0.5:
        score -= 10; reasons.append(f"tests reach only {round(tested_share * 100)}% of its files")
    per_file = recent / max(1, files)
    if schema:
        pass
    elif unit == "module" and (recent >= 8 or commits >= 30) or unit == "component" and recent >= 12 and per_file >= 1.0:
        score -= 12; reasons.append(f"high churn ({commits} commits, {recent} in the last 90 days)")
    elif unit == "module" and (recent >= 4 or commits >= 15) or unit == "component" and recent >= 6 and per_file >= 0.5:
        score -= 6; reasons.append(f"frequent changes ({commits} commits, {recent} in the last 90 days)")
    if failures:
        score -= min(20, 10 * failures); reasons.append(f"{failures} recorded failure(s)")
    limit = 12 if unit == "module" else 8
    if fan_out > limit * 2:
        score -= 12; reasons.append(f"depends on {fan_out} {'modules' if unit == 'module' else 'components'}")
    elif fan_out > limit:
        score -= 6; reasons.append(f"depends on {fan_out} {'modules' if unit == 'module' else 'components'}")
    if in_cycle:
        score -= 10; reasons.append("in a dependency cycle")
    if duplicated:
        score -= 8; reasons.append("has a duplication suspect")
    score = max(0, score)
    grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D" if score >= 40 else "E"
    return {"grade": grade, "score": score, "reasons": reasons or ["no concerns found"]}


def _reach(start: str, adj: dict[str, Any]) -> set[str]:
    seen: set[str] = set()
    stack = list(adj.get(start, {}))
    while stack:
        cur = stack.pop()
        if cur in seen or cur == start:
            continue
        seen.add(cur)
        stack.extend(adj.get(cur, {}))
    return seen


def _cycles(adj: dict[str, set[str]], nodes: set[str]) -> list[list[str]]:
    """Strongly connected components with more than one member (Tarjan, iterative)."""
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = 0
    for start in sorted(nodes):
        if start in index_of:
            continue
        work = [(start, iter(sorted(adj.get(start, ()))))]
        index_of[start] = low[start] = counter; counter += 1
        stack.append(start); on_stack.add(start)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index_of:
                    index_of[w] = low[w] = counter; counter += 1
                    stack.append(w); on_stack.add(w)
                    work.append((w, iter(sorted(adj.get(w, ())))))
                    advanced = True
                    break
                if w in on_stack:
                    low[v] = min(low[v], index_of[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index_of[v]:
                scc = []
                while True:
                    w = stack.pop(); on_stack.discard(w); scc.append(w)
                    if w == v:
                        break
                if len(scc) > 1:
                    out.append(sorted(scc))
    return sorted(out, key=lambda c: (-len(c), c))


def _find_duplicates(root: Path, index: _Index, module_comp: dict[str, str], module_app: dict[str, str], copy_of: dict[str, str],
                     defs: dict[str, list[dict[str, Any]]], config: dict[str, Any], cache: dict[str, list[str] | None]) -> list[dict[str, Any]]:
    threshold = float(config.get("similarity_threshold") or 0.5)
    ignore = {n.lower() for n in config.get("duplicate_ignore_names") or []} | _GENERIC_NAMES
    real = [m for m in module_comp if m not in copy_of]
    found: dict[tuple[str, str], dict[str, Any]] = {}

    def text_of(mid: str, start: int | None = None, end: int | None = None) -> str:
        lines = _read_lines(root, index.modules[mid]["file"], cache) or []
        if start:
            lines = lines[start - 1:end or len(lines)]
        return "\n".join(lines)

    # 1. files with the same name in different components
    by_stem: dict[str, list[str]] = defaultdict(list)
    for mid in real:
        stem = PurePosixPath(index.modules[mid]["file"]).stem.lower()
        if stem not in ("index", "main", "__init__", "setup", "conftest", "mod", "lib"):
            by_stem[stem].append(mid)
    shingle_cache: dict[str, set[int]] = {}
    for stem, mids in by_stem.items():
        if len(mids) < 2 or len(mids) > 12:
            continue
        for i, a in enumerate(mids):
            for b in mids[i + 1:]:
                if module_comp[a] == module_comp[b]:
                    continue
                sa = shingle_cache.setdefault(a, _shingles(text_of(a)))
                sb = shingle_cache.setdefault(b, _shingles(text_of(b)))
                sim = _jaccard(sa, sb)
                cross = module_app[a] != module_app[b]
                if sim >= threshold or (cross and sim >= threshold * 0.5):
                    found[tuple(sorted((a, b)))] = {
                        "kind": "file", "name": PurePosixPath(index.modules[a]["file"]).name, "similarity": round(sim, 2),
                        "cross_app": cross, "modules": sorted((a, b)),
                    }

    # 2. the same top-level definition name in different components
    by_name: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for mid in real:
        for sym in defs.get(mid, []):
            if sym.get("kind") in ("function", "class", "interface") and len(sym["name"]) > 3 and sym["name"].lower() not in ignore:
                by_name[sym["name"]].append((mid, sym))
    for name, items in by_name.items():
        if len(items) < 2 or len(items) > 8:
            continue
        for i, (a, sa) in enumerate(items):
            for b, sb in items[i + 1:]:
                if a == b or module_comp[a] == module_comp[b]:
                    continue
                key = tuple(sorted((a, b)))
                if key in found:
                    found[key].setdefault("shared_names", []).append(name)
                    continue
                sim = _jaccard(_shingles(text_of(a, sa.get("start_line"), sa.get("end_line"))),
                               _shingles(text_of(b, sb.get("start_line"), sb.get("end_line"))))
                cross = module_app[a] != module_app[b]
                if sim >= threshold or (cross and sim >= 0.3):
                    found[key] = {"kind": "definition", "name": name, "similarity": round(sim, 2), "cross_app": cross, "modules": list(key)}

    out = []
    for n, (key, rec) in enumerate(sorted(found.items(), key=lambda kv: (-kv[1]["similarity"], kv[0])), 1):
        sim = rec["similarity"]
        rec["confidence"] = "high" if sim >= 0.8 else "medium" if sim >= threshold else "low"
        rec["id"] = f"DUP-{n:03d}"
        rec["components"] = [module_comp[m] for m in rec["modules"]]
        out.append(rec)
    return out


# --------------------------------------------------------------------------------------------------------------------
# graph layer (used by omni_graph.build_graph)
# --------------------------------------------------------------------------------------------------------------------


def layer_nodes_and_edges(component_map: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Nodes and edges for the components layer, plus per-module attrs to merge into the existing module nodes."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for app_id, app in component_map["applications"].items():
        nodes.append({"id": f"application:{app_id}", "kind": "application", "name": app_id, "summary":
                      f"{len(app['components'])} components, {app['modules']} modules, {app['loc']} lines ({', '.join(app['languages'])})",
                      "attrs": {"modules": app["modules"], "loc": app["loc"], "components": len(app["components"])}})
        for other, n in app["depends_on"].items():
            edges.append({"source": f"application:{app_id}", "target": f"application:{other}", "type": "depends_on", "detail": f"{n} dependencies"})
    for cid, comp in component_map["components"].items():
        attrs = {
            "application": comp["application"], "role": comp["role_label"], "modules": comp["module_count"], "loc": comp["loc"],
            "reuse_category": comp["reuse"]["category"], "reuse_score": comp["reuse"]["score"],
            "consumers": comp["reuse"]["consumers"], "consumer_apps": comp["reuse"]["consumer_apps"],
            "grade": comp["maintainability"]["grade"], "maintainability_score": comp["maintainability"]["score"],
            "maintainability_reasons": comp["maintainability"]["reasons"], "requirements": comp["requirements"][-12:],
            "downtrace": len(comp["downtrace"]), "impact": len(comp["impact"]), "in_cycle": comp["in_cycle"],
            "reusable_modules": [component_map["modules"][m]["name"] for m in comp["reuse"]["reusable_modules"][:10]],
        }
        if comp.get("annotation"):
            attrs["annotation"] = comp["annotation"]
        summary = comp.get("description") or f"{comp['role_label']}: {comp['module_count']} files, {comp['loc']} lines"
        nodes.append({"id": f"component:{cid}", "kind": "component", "name": comp["name"], "file": comp["path"], "summary": summary, "attrs": attrs})
        edges.append({"source": f"application:{comp['application']}", "target": f"component:{cid}", "type": "groups", "detail": ""})
        for m in comp["modules"]:
            edges.append({"source": f"component:{cid}", "target": m, "type": "composes", "detail": ""})
        for other, n in comp["depends_on"].items():
            edges.append({"source": f"component:{cid}", "target": f"component:{other}", "type": "depends_on", "detail": f"{n} module link(s)"})
    module_attrs = {
        mid: {"reuse_category": m["reuse"]["category"], "reuse_score": m["reuse"]["score"], "grade": m["maintainability"]["grade"],
              "maintainability_reasons": m["maintainability"]["reasons"], "component": m["component"]}
        for mid, m in component_map["modules"].items()
    }
    for copy, source in component_map.get("generated_copies", {}).items():
        module_attrs[copy] = {"generated_copy_of": source, "component": component_map["modules"].get(source, {}).get("component")}
    return nodes, edges, module_attrs


# --------------------------------------------------------------------------------------------------------------------
# queries (omni components show / trace / reuse / dupes)
# --------------------------------------------------------------------------------------------------------------------


def find(component_map: dict[str, Any], query: str) -> tuple[str, str] | None:
    """('component'|'module', id) for an exact id, path, or unambiguous name fragment."""
    q = query.strip().rstrip("/")
    if q in component_map["components"]:
        return "component", q
    if q in component_map["modules"]:
        return "module", q
    low = q.lower()
    comps = [c for c, r in component_map["components"].items() if low in c.lower() or low in r["name"].lower()]
    if len(comps) == 1:
        return "component", comps[0]
    mods = [m for m, r in component_map["modules"].items() if low == r["name"].lower() or m.lower().endswith(low)]
    if len(mods) == 1:
        return "module", mods[0]
    exact = [c for c in comps if c.lower().endswith(low) or component_map["components"][c]["name"].lower().endswith(low)]
    if len(exact) == 1:
        return "component", exact[0]
    return None


def search_reuse(component_map: dict[str, Any], words: list[str], limit: int = 25) -> list[dict[str, Any]]:
    words = [w.lower() for w in words if w.strip()]
    hits = []
    for mid, m in component_map["modules"].items():
        if m["reuse"]["category"] not in ("cross-app", "shared", "reused", "single-use"):
            continue
        hay = " ".join([mid, m["name"], " ".join(m["exports"]), m["component"]]).lower()
        if all(w in hay for w in words):
            hits.append(m)
    hits.sort(key=lambda m: (_REUSE_ORDER.index(m["reuse"]["category"]), -m["reuse"]["score"], m["id"]))
    return hits[:limit]


def trace(component_map: dict[str, Any], cid: str, up: bool = False, depth: int = 99) -> list[tuple[int, str, int]]:
    """Breadth-first (depth, component, link count) down the dependencies, or up the dependents with up=True."""
    comps = component_map["components"]
    key = "used_by" if up else "depends_on"
    seen = {cid}
    frontier = [cid]
    out: list[tuple[int, str, int]] = []
    level = 0
    while frontier and level < depth:
        level += 1
        nxt = []
        for c in frontier:
            for other, n in comps[c][key].items():
                if other not in seen:
                    seen.add(other)
                    out.append((level, other, n))
                    nxt.append(other)
        frontier = nxt
    return out


# --------------------------------------------------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------------------------------------------------


def _short_list(items: list[str], limit: int = 8) -> str:
    if not items:
        return "—"
    shown = ", ".join(f"`{i}`" for i in items[:limit])
    return shown + (f" (+{len(items) - limit} more)" if len(items) > limit else "")


def _mermaid_id(text: str) -> str:
    return "c_" + re.sub(r"[^A-Za-z0-9]", "_", text)


def render_markdown(component_map: dict[str, Any], banner: str | None = None) -> str:
    comps = component_map["components"]
    mods = component_map["modules"]
    apps = component_map["applications"]
    L: list[str] = []
    L.append("# Component Map")
    L.append("")
    if banner:
        L.append(f"**Classification:** {banner}")
        L.append("")
    L.append(f"Generated by `omni components build` on {component_map['generated_at'][:10]} from the project graph "
             f"({str(component_map.get('graph_generated_at') or '')[:10]}; history through {component_map.get('history_through') or 'n/a'}). "
             "Do not edit by hand: rerun the command. Add human knowledge (owners, \"platform-specific by design\", deprecations) to "
             f"`{ANNOTATIONS_PATH}` and it is merged in.")
    L.append("")
    L.append("**How to use this.** Before building something, search the [reuse catalog](#2-reuse-catalog) (or run "
             "`omni components reuse <words>`). Before changing a component, read its **change impact** (what depends on it) and "
             "**downtrace** (what it pulls in). `omni components show <name>` prints one entry; `omni components trace <name> [--up]` "
             "walks the dependencies; the graph viewer's **Components** view draws all of it.")
    L.append("")
    L.append("**Reading the annotations.** Every rating is computed from the project graph (imports, calls, tests, commits, "
             "requirements, failures), so it can be checked.")
    L.append("")
    L.append("Component categories:")
    L.append("")
    for k, text in COMPONENT_HELP.items():
        L.append(f"- **{k}**: {text}")
    L.append("")
    L.append("File (sub-component) categories:")
    L.append("")
    for k in _REUSE_ORDER:
        L.append(f"- **{k}**: {REUSE_HELP[k]}")
    L.append("")
    L.append("Maintainability grades: " + "; ".join(f"**{g}** {t}" for g, t in GRADE_HELP.items()))
    L.append("")

    # 1. applications
    L.append("## 1. Applications")
    L.append("")
    L.append("| Application | Components | Files | Lines | Languages | Depends on |")
    L.append("|---|---:|---:|---:|---|---|")
    for aid, a in apps.items():
        L.append(f"| **{aid}** | {len(a['components'])} | {a['modules']} | {a['loc']:,} | {', '.join(a['languages'])} | "
                 f"{', '.join(f'{k} ({v})' for k, v in a['depends_on'].items()) or '—'} |")
    L.append("")
    L.append("| Component | App | Role | Files | Lines | Reuse | Score | Maint. | Used by | Depends on |")
    L.append("|---|---|---|---:|---:|---|---:|:-:|---:|---:|")
    for cid, c in sorted(comps.items(), key=lambda kv: (kv[1]["application"], kv[0])):
        L.append(f"| [{c['name']}](#{_anchor(c['name'])}) | {c['application']} | {c['role_label']} | {c['module_count']} | {c['loc']:,} | "
                 f"{c['reuse']['category']} | {c['reuse']['score']} | {c['maintainability']['grade']} | {len(c['used_by'])} | {len(c['depends_on'])} |")
    L.append("")

    # 2. reuse catalog
    L.append("## 2. Reuse catalog")
    L.append("")
    L.append("Files that other code already depends on, grouped by what they are. **Check here before writing something new**: "
             "if a file below does what you need, use it (or extend it) instead of writing a second copy.")
    L.append("")
    by_role: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in mods.values():
        if m["reuse"]["category"] in ("cross-app", "shared", "reused"):
            by_role[comps[m["component"]]["role_label"]].append(m)
    for role in sorted(by_role):
        items = sorted(by_role[role], key=lambda m: (_REUSE_ORDER.index(m["reuse"]["category"]), -m["reuse"]["score"], m["id"]))
        L.append(f"### {role}")
        L.append("")
        L.append("| File | Provides | Reuse | Used by | Maint. |")
        L.append("|---|---|---|---:|:-:|")
        for m in items:
            provides = ", ".join(f"`{e}`" for e in m["exports"][:6]) or "—"
            extra = f" ({', '.join(m['reuse']['consumer_apps'])})" if m["reuse"]["consumer_apps"] else ""
            L.append(f"| `{m['id']}` | {provides} | {m['reuse']['category']}{extra} | {m['reuse']['consumers']} | {m['maintainability']['grade']} |")
        L.append("")

    # 3. duplication
    L.append("## 3. Duplication suspects")
    L.append("")
    dups = component_map["duplicates"]
    if not dups:
        L.append("None found.")
    else:
        L.append("Same-named definitions or near-identical files in different components. Similarity is token overlap of the "
                 "source (comments ignored): 1.0 is a copy. Cross-application pairs are the usual sign of the same logic written twice; "
                 "consider moving it to one shared place. Generated copies (files headed GENERATED from <source>) are not listed.")
        L.append("")
        L.append("| ID | What | Similarity | Confidence | Across apps | Files |")
        L.append("|---|---|---:|---|:-:|---|")
        for d in dups:
            L.append(f"| {d['id']} | {d['kind']} `{d['name']}` | {d['similarity']} | {d['confidence']} | {'yes' if d['cross_app'] else ''} | "
                     f"{'<br>'.join(f'`{m}`' for m in d['modules'])} |")
    L.append("")

    # 4. watchlist
    L.append("## 4. Maintainability watchlist")
    L.append("")
    watch = sorted((m for m in mods.values() if m["maintainability"]["grade"] in ("D", "E")),
                   key=lambda m: (m["maintainability"]["score"], m["id"]))
    if not watch:
        L.append("No file grades D or E.")
    else:
        L.append("| File | Grade | Why | Lines | Commits | Tests |")
        L.append("|---|:-:|---|---:|---:|---:|")
        for m in watch:
            L.append(f"| `{m['id']}` | {m['maintainability']['grade']} | {'; '.join(m['maintainability']['reasons'])} | {m['loc']} | "
                     f"{m['commits']} | {len(m['tests'])} |")
    L.append("")
    if component_map["cycles"]:
        L.append("**Dependency cycles between components** (each set depends on itself through the others; a change to one can "
                 "loop back): " + "; ".join(" ↔ ".join(f"`{c}`" for c in cyc) for cyc in component_map["cycles"]))
        L.append("")

    # 5. dependency diagram
    L.append("## 5. Dependency map")
    L.append("")
    L.append("Arrows point from a component to what it depends on; the number is how many file-to-file links back it. "
             "Links of 1 are left out of the diagram to keep it readable (they are in each component's entry below).")
    L.append("")
    for aid in apps:
        app_comps = [cid for cid in comps if comps[cid]["application"] == aid]
        L.append(f"### {aid}")
        L.append("")
        L.append("```mermaid")
        L.append("flowchart LR")
        shown = set()
        lines = []
        for cid in app_comps:
            for other, n in comps[cid]["depends_on"].items():
                if n < 2 and comps[other]["application"] == aid:
                    continue
                shown.update((cid, other))
                lines.append(f"  {_mermaid_id(cid)} -->|{n}| {_mermaid_id(other)}")
        for cid in sorted(shown | set(app_comps)):
            c = comps[cid]
            label = c["name"].replace('"', "'")
            lines.insert(0, f"  {_mermaid_id(cid)}[\"{label}<br/>{c['reuse']['category']} · {c['maintainability']['grade']}\"]")
        L.extend(lines)
        L.append("```")
        L.append("")

    # 6. components
    L.append("## 6. Components")
    L.append("")
    for cid, c in sorted(comps.items(), key=lambda kv: (kv[1]["application"], kv[0])):
        L.extend(component_section(component_map, cid))
    if component_map["problems"]:
        L.append("## Problems found while building the map")
        L.append("")
        for p in component_map["problems"]:
            L.append(f"- {p}")
        L.append("")
    return "\n".join(L)


def component_section(component_map: dict[str, Any], cid: str, heading: str = "###") -> list[str]:
    """The markdown entry for one component (also what `omni components show` prints)."""
    comps = component_map["components"]
    mods = component_map["modules"]
    c = comps[cid]
    L: list[str] = []
    L.append(f"{heading} {c['name']}")
    L.append("")
    L.append(f"`{cid}` · {c['role_label']} · {c['module_count']} files · {c['loc']:,} lines · {', '.join(c['languages'])}")
    L.append("")
    if c.get("description"):
        L.append(c["description"])
        L.append("")
    note = c.get("annotation")
    if note:
        for k in ("summary", "owner", "status"):
            if note.get(k):
                L.append(f"- **{k.capitalize()} (annotation):** {note[k]}")
        for n in note.get("notes") or []:
            L.append(f"- **Note:** {n}")
        L.append("")
    r = c["reuse"]
    L.append(f"- **Reusability:** {r['category']} (score {r['score']}/100): {'; '.join(r['why'])}."
             + (f" Used from: {_short_list(r['consumer_components'], 6)}." if r["consumer_components"] else ""))
    mt = c["maintainability"]
    L.append(f"- **Maintainability:** {mt['grade']} ({mt['score']}/100): {'; '.join(mt['reasons'])}. "
             f"Tests reach {c['tested_modules']}/{c['module_count']} files; {c['commits']} commits ({c['recent_commits']} in the last 90 days).")
    L.append(f"- **Depends on:** " + (", ".join(f"`{k}` ({v})" for k, v in c["depends_on"].items()) or "nothing else in the project") + ".")
    L.append(f"- **Downtrace** (everything it pulls in, transitively): {len(c['downtrace'])} component(s)"
             + (f": {_short_list(c['downtrace'], 10)}" if c["downtrace"] else "") + ".")
    L.append(f"- **Used by:** " + (", ".join(f"`{k}` ({v})" for k, v in c["used_by"].items()) or "nothing (top of the tree)") + ".")
    L.append(f"- **Change impact** (everything that depends on it, transitively): {len(c['impact'])} component(s)"
             + (f": {_short_list(c['impact'], 10)}" if c["impact"] else "") + ".")
    if c["requirements"]:
        L.append(f"- **Requirements:** {len(c['requirements'])}, most recent {', '.join(c['requirements'][-6:])}.")
    if c["failures"]:
        L.append(f"- **Recorded failures:** {', '.join(c['failures'])}.")
    if c["external_packages"]:
        L.append(f"- **External packages:** {', '.join(f'`{k}`' for k in list(c['external_packages'])[:10])}.")
    if c["in_cycle"]:
        L.append("- **In a dependency cycle** with other components (see section 4).")
    L.append("")
    L.append("| Sub-component | Lines | Reuse | Used by | Maint. | Tests | Uses (in project) |")
    L.append("|---|---:|---|---:|:-:|---:|---|")
    for mid in sorted(c["modules"], key=lambda m: (-mods[m]["reuse"]["consumers"], m)):
        m = mods[mid]
        uses = [mods[d]["name"] if d in mods else d for d in m["depends_on"]]
        name = f"`{PurePosixPath(m['file']).name}`"
        if m["copies"]:
            name += f" (copied to {len(m['copies'])})"
        if m["duplicates"]:
            name += " (duplicate: " + ", ".join(m["duplicates"]) + ")"
        L.append(f"| {name} | {m['loc']} | {m['reuse']['category']} | {m['reuse']['consumers']} | {m['maintainability']['grade']} | "
                 f"{len(m['tests'])} | {', '.join(uses[:6]) + (f' +{len(uses) - 6}' if len(uses) > 6 else '') if uses else '—'} |")
    L.append("")
    return L


def module_section(component_map: dict[str, Any], mid: str) -> list[str]:
    """A plain-text entry for one file (sub-component)."""
    m = component_map["modules"][mid]
    r, mt = m["reuse"], m["maintainability"]
    L = [f"{m['file']}  ({m['loc']} lines, {m.get('language') or '?'})",
         f"  component:        {m['component']}  ({m['application']})",
         f"  provides:         {', '.join(m['exports']) or '-'}",
         f"  reusability:      {r['category']} (score {r['score']}/100): {'; '.join(r['why'])}",
         f"  maintainability:  {mt['grade']} ({mt['score']}/100): {'; '.join(mt['reasons'])}",
         f"  uses ({len(m['depends_on'])}):        {', '.join(m['depends_on']) or '-'}",
         f"  used by ({len(m['used_by'])}):     {', '.join(m['used_by']) or '-'}",
         f"  tests ({len(m['tests'])}):        {', '.join(m['tests']) or '-'}",
         f"  commits:          {m['commits']} ({m['recent_commits']} in the last 90 days)",
         f"  requirements:     {', '.join(m['requirements']) or '-'}"]
    if m["failures"]:
        L.append(f"  failures:         {', '.join(m['failures'])}")
    if m["copies"]:
        L.append(f"  generated copies: {', '.join(m['copies'])}")
    for d in component_map["duplicates"]:
        if mid in d["modules"]:
            other = [x for x in d["modules"] if x != mid][0]
            L.append(f"  duplicate {d['id']}: {d['kind']} {d['name']} ~ {other} (similarity {d['similarity']}, {d['confidence']})")
    if m.get("annotation"):
        L.append(f"  annotation:       {json.dumps(m['annotation'])}")
    return L


def _anchor(text: str) -> str:
    return re.sub(r"[^a-z0-9 _-]", "", text.lower()).strip().replace(" ", "-")


def summary_lines(component_map: dict[str, Any]) -> list[str]:
    comps = component_map["components"]
    mods = component_map["modules"]
    cats: dict[str, int] = defaultdict(int)
    grades: dict[str, int] = defaultdict(int)
    for m in mods.values():
        cats[m["reuse"]["category"]] += 1
        grades[m["maintainability"]["grade"]] += 1
    return [
        f"{len(component_map['applications'])} applications, {len(comps)} components, {len(mods)} files",
        "reuse: " + ", ".join(f"{k} {cats[k]}" for k in _REUSE_ORDER if cats.get(k)),
        "maintainability: " + ", ".join(f"{g} {grades[g]}" for g in "ABCDE" if grades.get(g)),
        f"duplication suspects: {len(component_map['duplicates'])} "
        f"({sum(1 for d in component_map['duplicates'] if d['cross_app'])} across applications); "
        f"dependency cycles: {len(component_map['cycles'])}",
    ]


# --------------------------------------------------------------------------------------------------------------------
# html explorer (omni components view)
# --------------------------------------------------------------------------------------------------------------------


def _html_payload(component_map: dict[str, Any]) -> dict[str, Any]:
    """The subset of the map the explorer needs, kept small enough to embed."""
    comps = {}
    for cid, c in component_map["components"].items():
        comps[cid] = {k: c[k] for k in ("name", "application", "role_label", "description", "module_count", "loc", "languages", "modules",
                                         "reuse", "maintainability", "depends_on", "used_by", "downtrace", "impact", "in_cycle",
                                         "tested_modules", "commits", "recent_commits", "requirements", "failures", "external_packages")}
        if c.get("annotation"):
            comps[cid]["annotation"] = c["annotation"]
    mods = {}
    for mid, m in component_map["modules"].items():
        mods[mid] = {"name": PurePosixPath(m["file"]).name, "component": m["component"], "loc": m["loc"], "exports": m["exports"][:12],
                     "reuse": {k: m["reuse"][k] for k in ("category", "score", "consumers", "consumer_apps")},
                     "grade": m["maintainability"]["grade"], "reasons": m["maintainability"]["reasons"],
                     "tests": len(m["tests"]), "commits": m["commits"], "depends_on": m["depends_on"], "used_by": m["used_by"],
                     "duplicates": m["duplicates"], "copies": m["copies"]}
    return {
        "generated_at": component_map["generated_at"][:10],
        "history_through": component_map.get("history_through"),
        "applications": component_map["applications"],
        "components": comps,
        "modules": mods,
        "duplicates": component_map["duplicates"],
        "cycles": component_map["cycles"],
        "legend": component_map["legend"],
    }


def render_html(component_map: dict[str, Any], banner: str | None = None, title: str = "Component Map") -> str:
    data = json.dumps(_html_payload(component_map), separators=(",", ":"), ensure_ascii=False)
    data = data.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    banner_html = f'<div class="banner">{_esc(banner)}</div>' if banner else ""
    return _HTML_TEMPLATE.replace("__TITLE__", _esc(title)).replace("__BANNER__", banner_html).replace("__DATA__", data)


def _esc(text: Any) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--text:#1d2129;--dim:#5b6472;--line:#dde1e7;--accent:#2f6fd0;--chip:#eef1f5;
  --A:#1f9d55;--B:#65a30d;--C:#b7791f;--D:#d9480f;--E:#c92a2a;
  --xapp:#c2185b;--lib:#7048e8;--feat:#1c7ed6;--top:#5c677d;--un:#c92a2a;--single:#a07400}
@media (prefers-color-scheme:dark){:root{--bg:#121318;--panel:#1b1d24;--text:#e6e8ec;--dim:#9aa3b2;--line:#2c2f39;--accent:#6ea8fe;--chip:#262935;
  --A:#51cf66;--B:#a9e34b;--C:#ffd43b;--D:#ff922b;--E:#ff6b6b;--xapp:#f783ac;--lib:#b197fc;--feat:#74c0fc;--top:#adb5bd;--un:#ff6b6b;--single:#ffd43b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.banner{background:#502b85;color:#fff;text-align:center;font-weight:600;padding:4px 16px;font-size:12px;letter-spacing:.04em}
header{padding:16px;border-bottom:1px solid var(--line);background:var(--panel)}
h1{margin:0 0 4px;font-size:20px}
.sub{color:var(--dim);font-size:13px}
.stats{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.stat{background:var(--chip);border-radius:8px;padding:6px 10px}
.stat b{font-size:16px;display:block}
nav{display:flex;gap:4px;padding:0 16px;background:var(--panel);border-bottom:1px solid var(--line);overflow-x:auto}
nav button{border:0;background:none;color:var(--dim);padding:10px 12px;font:inherit;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap}
nav button.on{color:var(--text);border-bottom-color:var(--accent);font-weight:600}
.bar{display:flex;flex-wrap:wrap;gap:8px;padding:12px 16px}
.bar input,.bar select{font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--text)}
.bar input{flex:1 1 220px;min-width:0}
main{padding:0 16px 32px}
.layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.25fr);gap:16px}
@media (max-width:900px){.layout{grid-template-columns:minmax(0,1fr)}}
.list{display:flex;flex-direction:column;gap:6px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px;cursor:pointer;text-align:left;font:inherit;color:inherit;width:100%}
.card:hover,.card.on{border-color:var(--accent)}
.card .top{display:flex;gap:8px;align-items:center;justify-content:space-between}
.card .nm{font-weight:600;overflow-wrap:anywhere}
.card .meta{color:var(--dim);font-size:12px;margin-top:2px}
.badges{display:flex;gap:4px;flex-shrink:0}
.badge{display:inline-block;font-size:11px;font-weight:700;border-radius:999px;padding:2px 8px;color:#fff;white-space:nowrap}
.g{width:24px;text-align:center;padding:2px 0}
.detail{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px;position:sticky;top:12px;max-height:calc(100vh - 24px);overflow:auto}
.detail h2{margin:0 0 4px;font-size:18px;overflow-wrap:anywhere}
.detail h3{margin:16px 0 6px;font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--dim)}
.detail p{margin:4px 0}
.note{background:var(--chip);border-left:3px solid var(--accent);padding:6px 10px;border-radius:4px;margin:6px 0}
.links{display:flex;flex-wrap:wrap;gap:4px}
.link{border:1px solid var(--line);background:var(--chip);color:var(--text);border-radius:6px;padding:2px 8px;font:inherit;font-size:12px;cursor:pointer}
.link:hover{border-color:var(--accent)}
.dim{color:var(--dim)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:600}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.wrap{overflow-x:auto}
code{font-size:12px;overflow-wrap:anywhere}
.empty{color:var(--dim);padding:24px;text-align:center}
</style>
</head>
<body>
__BANNER__
<header>
  <h1>__TITLE__</h1>
  <div class="sub" id="sub"></div>
  <div class="stats" id="stats"></div>
</header>
<nav id="tabs"></nav>
<div class="bar" id="bar">
  <input id="q" type="search" placeholder="Search components, files, exports, requirements…" aria-label="Search">
  <select id="app" aria-label="Application"></select>
  <select id="cat" aria-label="Reuse category"></select>
  <select id="grade" aria-label="Maintainability grade"></select>
</div>
<main id="main"></main>
<script>
(function () {
  "use strict";
  var D = __DATA__;
  var C = D.components, M = D.modules;
  var CAT_COLOR = {"cross-app":"var(--xapp)","library":"var(--lib)","shared":"var(--lib)","reused":"var(--feat)","feature":"var(--feat)",
    "single-use":"var(--single)","top-level":"var(--top)","entry-point":"var(--top)","framework-wired":"var(--top)","test-only":"var(--single)","unreferenced":"var(--un)"};
  var state = {tab: "components", q: "", app: "", cat: "", grade: "", sel: null};
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function badge(text, color, extra) { var b = el("span", "badge" + (extra ? " " + extra : ""), text); b.style.background = color; return b; }
  function gradeBadge(g) { return badge(g, "var(--" + g + ")", "g"); }
  function catBadge(c) { return badge(c, CAT_COLOR[c] || "var(--top)"); }
  function fmt(n) { return Number(n).toLocaleString(); }
  function words() { return state.q.toLowerCase().split(/\s+/).filter(Boolean); }
  function hay(cid) {
    var c = C[cid], parts = [cid, c.name, c.role_label, c.description, (c.requirements || []).join(" ")];
    if (c.annotation) parts.push(JSON.stringify(c.annotation));
    c.modules.forEach(function (m) { parts.push(m, (M[m] && M[m].exports || []).join(" ")); });
    return parts.join(" ").toLowerCase();
  }
  var HAY = {}; Object.keys(C).forEach(function (cid) { HAY[cid] = hay(cid); });

  // ---- header ------------------------------------------------------------------------------------
  $("sub").textContent = "Generated " + D.generated_at + (D.history_through ? " · history through " + D.history_through : "") +
    " · every rating is computed from the code graph (imports, tests, commits, requirements, failures).";
  var cats = {}, grades = {};
  Object.keys(M).forEach(function (m) { cats[M[m].reuse.category] = (cats[M[m].reuse.category] || 0) + 1; grades[M[m].grade] = (grades[M[m].grade] || 0) + 1; });
  [["Applications", Object.keys(D.applications).length], ["Components", Object.keys(C).length], ["Files", Object.keys(M).length],
   ["Duplication suspects", D.duplicates.length], ["Cross-app files", cats["cross-app"] || 0], ["Files graded D/E", (grades.D || 0) + (grades.E || 0)],
   ["Dependency cycles", D.cycles.length]].forEach(function (s) {
    var d = el("div", "stat"); d.appendChild(el("b", null, fmt(s[1]))); d.appendChild(document.createTextNode(s[0])); $("stats").appendChild(d);
  });

  // ---- filters -----------------------------------------------------------------------------------
  function opts(sel, first, values) { sel.appendChild(new Option(first, "")); values.forEach(function (v) { sel.appendChild(new Option(v, v)); }); }
  opts($("app"), "All applications", Object.keys(D.applications));
  opts($("cat"), "Any reuse", ["cross-app", "library", "feature", "top-level", "unreferenced"]);
  opts($("grade"), "Any grade", ["A", "B", "C", "D", "E"]);
  $("q").addEventListener("input", function (e) { state.q = e.target.value; render(); });
  ["app", "cat", "grade"].forEach(function (k) { $(k).addEventListener("change", function (e) { state[k] = e.target.value; render(); }); });

  // ---- tabs --------------------------------------------------------------------------------------
  var TABS = [["components", "Components"], ["reuse", "Reuse catalog"], ["dupes", "Duplicates"], ["watch", "Watchlist"], ["legend", "How to read this"]];
  TABS.forEach(function (t) {
    var b = el("button", null, t[1]); b.type = "button"; b.setAttribute("data-tab", t[0]);
    b.addEventListener("click", function () { state.tab = t[0]; render(); }); $("tabs").appendChild(b);
  });

  function goComponent(cid) { state.tab = "components"; state.sel = cid; render(); var d = document.querySelector(".detail"); if (d) d.scrollTop = 0; }
  function goModule(mid) { if (M[mid]) goComponent(M[mid].component); }
  function linkTo(cid, label) { var b = el("button", "link", label || (C[cid] ? C[cid].name : cid)); b.type = "button"; b.addEventListener("click", function () { goComponent(cid); }); return b; }

  // ---- components tab ----------------------------------------------------------------------------
  function filtered() {
    var w = words();
    return Object.keys(C).filter(function (cid) {
      var c = C[cid];
      if (state.app && c.application !== state.app) return false;
      if (state.cat && c.reuse.category !== state.cat) return false;
      if (state.grade && c.maintainability.grade !== state.grade) return false;
      return w.every(function (x) { return HAY[cid].indexOf(x) > -1; });
    }).sort(function (a, b) { return C[a].application.localeCompare(C[b].application) || a.localeCompare(b); });
  }
  function renderComponents(main) {
    var ids = filtered();
    if (!state.sel || ids.indexOf(state.sel) < 0) state.sel = ids[0] || null;
    var lay = el("div", "layout"), list = el("div", "list");
    if (!ids.length) list.appendChild(el("div", "empty", "No component matches."));
    ids.forEach(function (cid) {
      var c = C[cid], card = el("button", "card" + (cid === state.sel ? " on" : "")); card.type = "button";
      var top = el("div", "top"); top.appendChild(el("span", "nm", c.name));
      var bs = el("span", "badges"); bs.appendChild(catBadge(c.reuse.category)); bs.appendChild(gradeBadge(c.maintainability.grade)); top.appendChild(bs);
      card.appendChild(top);
      card.appendChild(el("div", "meta", c.role_label + " · " + c.module_count + " files · " + fmt(c.loc) + " lines · used by " + Object.keys(c.used_by).length + " · uses " + Object.keys(c.depends_on).length));
      card.addEventListener("click", function () { state.sel = cid; render(); });
      list.appendChild(card);
    });
    lay.appendChild(list);
    var det = el("div", "detail"); if (state.sel) fillDetail(det, state.sel); lay.appendChild(det);
    main.appendChild(lay);
  }
  function linkList(box, obj, empty) {
    var keys = Array.isArray(obj) ? obj : Object.keys(obj);
    if (!keys.length) { box.appendChild(el("p", "dim", empty)); return; }
    var l = el("div", "links");
    keys.forEach(function (k) { l.appendChild(linkTo(k, (C[k] ? C[k].name : k) + (Array.isArray(obj) ? "" : " (" + obj[k] + ")"))); });
    box.appendChild(l);
  }
  function fillDetail(d, cid) {
    var c = C[cid], r = c.reuse, mt = c.maintainability;
    d.appendChild(el("h2", null, c.name));
    d.appendChild(el("div", "dim", cid + " · " + c.role_label + " · " + (c.languages || []).join(", ")));
    var bs = el("div", "badges"); bs.style.margin = "8px 0"; bs.appendChild(catBadge(r.category)); bs.appendChild(gradeBadge(mt.grade)); d.appendChild(bs);
    if (c.description) d.appendChild(el("p", null, c.description));
    var a = c.annotation;
    if (a) {
      ["summary", "owner", "status"].forEach(function (k) { if (a[k]) d.appendChild(el("div", "note", (k === "summary" ? "" : k + ": ") + a[k])); });
      (a.notes || []).forEach(function (n) { d.appendChild(el("div", "note", n)); });
    }
    d.appendChild(el("h3", null, "Reusability · " + r.score + "/100"));
    d.appendChild(el("p", null, (D.legend.component_reuse[r.category] || "") + " " + r.why.join("; ") + "."));
    d.appendChild(el("h3", null, "Maintainability · " + mt.grade + " (" + mt.score + "/100)"));
    d.appendChild(el("p", null, mt.reasons.join("; ") + ". Tests reach " + c.tested_modules + "/" + c.module_count + " files; " + c.commits + " commits (" + c.recent_commits + " in the last 90 days)."));
    if (c.in_cycle) d.appendChild(el("p", "dim", "In a dependency cycle with other components."));
    d.appendChild(el("h3", null, "Depends on")); linkList(d, c.depends_on, "Nothing else in the project.");
    d.appendChild(el("h3", null, "Used by")); linkList(d, c.used_by, "Nothing: top of the tree.");
    d.appendChild(el("h3", null, "Downtrace · pulls in " + c.downtrace.length)); linkList(d, c.downtrace, "Nothing.");
    d.appendChild(el("h3", null, "Change impact · " + c.impact.length + " depend on it")); linkList(d, c.impact, "Nothing depends on it.");
    if (c.requirements.length) { d.appendChild(el("h3", null, "Requirements · " + c.requirements.length)); d.appendChild(el("p", null, c.requirements.slice(-15).join(", ") + (c.requirements.length > 15 ? " …" : ""))); }
    if (c.failures.length) { d.appendChild(el("h3", null, "Recorded failures")); d.appendChild(el("p", null, c.failures.join(", "))); }
    var ext = Object.keys(c.external_packages || {}); if (ext.length) { d.appendChild(el("h3", null, "External packages")); d.appendChild(el("p", null, ext.join(", "))); }
    d.appendChild(el("h3", null, "Sub-components (files)"));
    var w = el("div", "wrap"), t = el("table"), hr = el("tr");
    ["File", "Lines", "Reuse", "Used by", "Grade", "Tests", "Uses"].forEach(function (h) { hr.appendChild(el("th", null, h)); }); t.appendChild(hr);
    c.modules.slice().sort(function (x, y) { return M[y].reuse.consumers - M[x].reuse.consumers; }).forEach(function (mid) {
      var m = M[mid], tr = el("tr"), f = el("td");
      f.appendChild(el("code", null, m.name));
      if (m.exports.length) f.appendChild(el("div", "dim", m.exports.slice(0, 6).join(", ")));
      if (m.copies.length) f.appendChild(el("div", "dim", "copied into " + m.copies.length + " app(s)"));
      if (m.duplicates.length) f.appendChild(el("div", "dim", "⚠ duplicate: " + m.duplicates.join(", ")));
      if (m.grade === "D" || m.grade === "E") f.appendChild(el("div", "dim", m.reasons.join("; ")));
      tr.appendChild(f); tr.appendChild(el("td", "n", fmt(m.loc)));
      var rc = el("td"); rc.appendChild(catBadge(m.reuse.category)); if (m.reuse.consumer_apps.length) rc.appendChild(el("div", "dim", "+ " + m.reuse.consumer_apps.join(", "))); tr.appendChild(rc);
      tr.appendChild(el("td", "n", m.reuse.consumers)); var gc = el("td"); gc.appendChild(gradeBadge(m.grade)); tr.appendChild(gc);
      tr.appendChild(el("td", "n", m.tests));
      tr.appendChild(el("td", "dim", m.depends_on.map(function (x) { return M[x] ? M[x].name : x; }).slice(0, 5).join(", ") + (m.depends_on.length > 5 ? " +" + (m.depends_on.length - 5) : "")));
      t.appendChild(tr);
    });
    w.appendChild(t); d.appendChild(w);
  }

  // ---- other tabs ---------------------------------------------------------------------------------
  function table(main, heads, rows, empty) {
    if (!rows.length) { main.appendChild(el("div", "empty", empty)); return; }
    var w = el("div", "wrap"), t = el("table"), hr = el("tr");
    heads.forEach(function (h) { hr.appendChild(el("th", null, h)); }); t.appendChild(hr);
    rows.forEach(function (cells) { var tr = el("tr"); cells.forEach(function (c) { var td = el("td"); if (c && c.nodeType) td.appendChild(c); else td.textContent = c == null ? "" : c; tr.appendChild(td); }); t.appendChild(tr); });
    w.appendChild(t); main.appendChild(w);
  }
  function moduleOk(mid) {
    var m = M[mid], c = C[m.component], w = words();
    if (state.app && c.application !== state.app) return false;
    if (state.grade && m.grade !== state.grade) return false;
    var h = (mid + " " + m.exports.join(" ") + " " + m.component).toLowerCase();
    return w.every(function (x) { return h.indexOf(x) > -1; });
  }
  function fileCell(mid) { var b = el("button", "link", mid); b.type = "button"; b.addEventListener("click", function () { goModule(mid); }); return b; }
  function renderReuse(main) {
    main.appendChild(el("p", "dim", "Files other code already depends on. Check here before writing something new: if one does what you need, use or extend it instead of writing a second copy."));
    var order = ["cross-app", "shared", "reused"];
    var rows = Object.keys(M).filter(function (mid) { return order.indexOf(M[mid].reuse.category) > -1 && moduleOk(mid); })
      .sort(function (a, b) { return order.indexOf(M[a].reuse.category) - order.indexOf(M[b].reuse.category) || M[b].reuse.score - M[a].reuse.score; })
      .map(function (mid) { var m = M[mid]; return [fileCell(mid), m.exports.slice(0, 6).join(", "), catBadge(m.reuse.category), m.reuse.consumers + (m.reuse.consumer_apps.length ? " (+ " + m.reuse.consumer_apps.join(", ") + ")" : ""), gradeBadge(m.grade)]; });
    table(main, ["File", "Provides", "Reuse", "Used by", "Grade"], rows, "Nothing matches.");
  }
  function renderDupes(main) {
    main.appendChild(el("p", "dim", "The same logic in two places: same-named definitions or near-identical files in different components. Similarity is token overlap with comments ignored; 1.0 is a copy. Across applications this usually means it was written twice and belongs in one shared place."));
    var w = words();
    var rows = D.duplicates.filter(function (x) {
      if (state.app && !x.modules.some(function (m) { return M[m] && C[M[m].component].application === state.app; })) return false;
      var h = (x.name + " " + x.modules.join(" ")).toLowerCase(); return w.every(function (q) { return h.indexOf(q) > -1; });
    }).map(function (x) {
      var files = el("div"); x.modules.forEach(function (m) { files.appendChild(fileCell(m)); files.appendChild(document.createTextNode(" ")); });
      return [x.id, x.kind + " " + x.name, x.similarity.toFixed(2), x.confidence + (x.cross_app ? " · across apps" : ""), files];
    });
    table(main, ["ID", "What", "Similarity", "Confidence", "Files"], rows, "No duplication suspects match.");
  }
  function renderWatch(main) {
    main.appendChild(el("p", "dim", "Files graded C, D or E, worst first, with the reasons. Plan work before extending these."));
    var rows = Object.keys(M).filter(function (mid) { return "CDE".indexOf(M[mid].grade) > -1 && moduleOk(mid); })
      .sort(function (a, b) { return M[b].grade.localeCompare(M[a].grade) || M[b].loc - M[a].loc; })
      .map(function (mid) { var m = M[mid]; return [fileCell(mid), gradeBadge(m.grade), m.reasons.join("; "), fmt(m.loc), m.commits, m.tests]; });
    table(main, ["File", "Grade", "Why", "Lines", "Commits", "Tests"], rows, "No file grades C or worse.");
    if (D.cycles.length) {
      main.appendChild(el("h3", null, "Dependency cycles"));
      D.cycles.forEach(function (cyc) { var p = el("div", "links"); p.style.margin = "6px 0"; cyc.forEach(function (cid) { p.appendChild(linkTo(cid)); }); main.appendChild(p); });
    }
  }
  function renderLegend(main) {
    function section(title, obj) { main.appendChild(el("h3", null, title)); Object.keys(obj).forEach(function (k) { var p = el("p"); p.appendChild(catBadge(k)); p.appendChild(document.createTextNode(" " + obj[k])); main.appendChild(p); }); }
    section("Component reuse", D.legend.component_reuse);
    section("File reuse", D.legend.reuse);
    main.appendChild(el("h3", null, "Maintainability grades"));
    Object.keys(D.legend.grades).forEach(function (g) { var p = el("p"); p.appendChild(gradeBadge(g)); p.appendChild(document.createTextNode(" " + D.legend.grades[g])); main.appendChild(p); });
    main.appendChild(el("p", "dim", "Downtrace: every component this one pulls in, transitively. Change impact: every component that depends on this one, transitively. Regenerate with `omni graph build` then `omni components build`; add owners and notes in .ai/component-annotations.json."));
  }

  function render() {
    Array.prototype.forEach.call(document.querySelectorAll("#tabs button"), function (b) { b.classList.toggle("on", b.getAttribute("data-tab") === state.tab); });
    $("cat").style.display = state.tab === "components" ? "" : "none";
    $("bar").style.display = state.tab === "legend" ? "none" : "";
    var main = $("main"); main.textContent = "";
    ({components: renderComponents, reuse: renderReuse, dupes: renderDupes, watch: renderWatch, legend: renderLegend})[state.tab](main);
  }
  render();
})();
</script>
</body>
</html>
"""
