"""REQ-060: `omni docs build` -- standard engineering documents and Mermaid diagrams, generated without a model.

Every document is derived from data the workspace already keeps: the code graph (`.ai/project-graph.json`),
the requirement registry, the failure ledger, the test-suite registry, git history (as recorded in the graph's
history layer), Python docstrings and signatures read with `ast`, and deployment descriptors found on disk.
A section with no data behind it says "Not derived: ..." instead of inventing content.

Output is deterministic: everything is sorted, no timestamps are written, and the only volatile value is the
commit SHA on the "Generated from commit" line of index.md (`check_docs` ignores that line). Stdlib only.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DOCS_DEFAULT_OUTPUT = "docs/generated"
GRAPH_DEFAULT_PATH = ".ai/project-graph.json"
SHA_LINE_PREFIX = "Generated from commit "

# Readability caps. A diagram past these sizes stops being read; what was cut is always stated under it.
MAX_COMPONENT_NODES = 24
MAX_DIAGRAM_EDGES = 40
MAX_CLASSES = 25
MAX_PACKAGE_CLASSES = 15
MAX_SYMBOLS = 40
MAX_METHODS = 12
MAX_CONTEXT_DEPS = 10
MAX_FLOW_NODES = 20
MAX_RECENT = 10
MAX_GLOSSARY = 30
MIN_FLOW_CALLEES = 3

# (file name, title, one-line purpose) in index order.
DOCUMENTS: list[tuple[str, str, str]] = [
    ("architecture.md", "Software Architecture Document (SAD)", "arc42 / ISO/IEC/IEEE 42010 views: context, building blocks, runtime, deployment, risks."),
    ("design.md", "Software Design Document (SDD)", "IEEE 1016-style per-module design: purpose, interfaces, data structures, dependencies, rationale."),
    ("requirements.md", "Software Requirements Specification (SRS)", "Every registered requirement with description, acceptance criteria, priority and status."),
    ("requirements-traceability.md", "Requirements Traceability Matrix", "Requirement -> files in scope -> tests -> status, plus failures linked to requirements."),
    ("test-plan.md", "Test Plan and Coverage Summary", "Registered suites, validation each requirement asks for, and requirements with no linked test."),
    ("modules.md", "Module Reference", "One row per module: purpose, public symbol count, inbound and outbound dependencies."),
    ("class-diagram.md", "Class Diagram", "The main classes across the project: inheritance and the instantiations the graph resolved."),
    ("CHANGES-SUMMARY.md", "Changes Summary", "Completed, in-progress and blocked requirements, open failures, and required docs no commit touched."),
]

# Assistant skills (OmniEngineering's own and the interview IDE's) that a document hands off to. A skill is only
# named when `.claude/skills/<name>/SKILL.md` exists in the documented repository.
WORKFLOW_SKILLS: dict[str, list[tuple[str, str]]] = {
    "architecture.md": [
        ("run-plan", "plan the change against these views"),
        ("review-ai-code", "review a change against the building blocks"),
        ("sre-readiness", "check the deployment view and risks before release"),
    ],
    "design.md": [("run-plan", "plan against the module interfaces"), ("review-ai-code", "review a change against this design")],
    "requirements.md": [("clarify", "capture new requirements before they land here")],
    "requirements-traceability.md": [("clarify", "close gaps in scope or acceptance criteria"), ("test-debug", "add the missing tests")],
    "test-plan.md": [("test-debug", "write or repair the tests this plan lists as missing")],
    "CHANGES-SUMMARY.md": [("sre-readiness", "review open failures and risks before release")],
    "index.md": [("swe-docs", "when to rebuild and how to read these documents")],
}

# OmniEngineering's own CLI files when they are embedded in another project (the graph marks most of them already).
_TOOLING_FILES = {"make_ai.py", "omni_graph.py", "omni_mcp.py", "omni_docs.py"}
_TEST_DIR_NAMES = {"test", "tests", "__tests__", "spec", "specs", "e2e", "testing"}
_SOURCE_ROOTS = {"src", "lib"}
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", "arbiter-out", ".arbiter"}
# Top-level directories searched for deployment descriptors even when the graph config excludes them from code parsing.
_DEPLOY_DIRS = {"deploy", "deployment", "deployments", "infra", "infrastructure", "k8s", "kubernetes", "helm", "charts", "ops", "ci"}
_QUALITY_PATTERN = re.compile(
    r"performance|latency|security|secur|reliab|availab|usabil|accessib|maintainab|portab|privacy|scal|resilien|observab|non-functional",
    re.IGNORECASE,
)
_CONFIDENT_CALL_DETAILS = ("same-module definition", "resolved on the enclosing class", "resolved via import")
_TERMINAL = {"completed", "withdrawn"}
_STATUS_ORDER = ["pending", "needs_review", "blocked", "proposed", "completed", "withdrawn"]


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------


@dataclass
class PyFunc:
    name: str
    signature: str
    doc: str
    params: list[str] = field(default_factory=list)


@dataclass
class PyClass:
    name: str
    bases: list[str]
    doc: str
    methods: list[PyFunc]


@dataclass
class PyInfo:
    doc: str = ""
    rationale: str = ""
    functions: list[PyFunc] = field(default_factory=list)
    classes: list[PyClass] = field(default_factory=list)
    has_main: bool = False
    guarded_imports: list[tuple[str, int]] = field(default_factory=list)  # (module name, relative level)


@dataclass
class Module:
    path: str
    container: str
    package: str
    test: bool
    tooling: bool
    language: str
    py: PyInfo | None
    symbols: list[tuple[str, str]]  # (kind, name) of top-level definitions from the graph


@dataclass
class Model:
    root: Path
    out_dir: Path
    project: str
    sha: str | None
    graph_head: str | None
    has_graph: bool
    has_history: bool
    nodes: dict[str, dict[str, Any]]
    edges: list[dict[str, Any]]
    modules: list[Module]
    requirements: list[dict[str, Any]]
    failures: list[dict[str, Any]]
    suites: list[dict[str, Any]]
    config: dict[str, Any]
    readme: str
    notes: list[str]

    def by_path(self) -> dict[str, Module]:
        cached = self.__dict__.get("_by_path")
        if cached is None:
            cached = {m.path: m for m in self.modules}
            self.__dict__["_by_path"] = cached
        return cached


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _read_json(path: Path) -> Any:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _one_line(text: str | None, limit: int = 200) -> str:
    """First paragraph of a docstring, collapsed to one line."""
    if not text:
        return ""
    para = re.split(r"\n\s*\n", text.strip(), maxsplit=1)[0]
    line = " ".join(para.split())
    return line if len(line) <= limit else line[: limit - 3].rstrip() + "..."


def _rationale(text: str | None, limit: int = 400) -> str:
    """The docstring's second paragraph, when there is one: usually the why."""
    if not text:
        return ""
    parts = re.split(r"\n\s*\n", text.strip())
    if len(parts) < 2:
        return ""
    line = " ".join(parts[1].split())
    return line if len(line) <= limit else line[: limit - 3].rstrip() + "..."


def _signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        args = ast.unparse(fn.args)
        returns = f" -> {ast.unparse(fn.returns)}" if fn.returns is not None else ""
    except Exception:  # noqa: BLE001 -- a signature we cannot print is shown bare
        args, returns = "...", ""
    prefix = "async " if isinstance(fn, ast.AsyncFunctionDef) else ""
    sig = " ".join(f"{prefix}{fn.name}({args}){returns}".split())
    return sig if len(sig) <= 160 else sig[:157] + "..."


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    a = fn.args
    names = [x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs]]
    if a.vararg:
        names.append("*" + a.vararg.arg)
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    return [n for n in names if n not in ("self", "cls")]


def parse_python(path: Path) -> PyInfo | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    doc = ast.get_docstring(tree)
    info = PyInfo(doc=_one_line(doc), rationale=_rationale(doc))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            info.functions.append(PyFunc(node.name, _signature(node), _one_line(ast.get_docstring(node)), _params(node)))
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            methods = [
                PyFunc(item.name, _signature(item), _one_line(ast.get_docstring(item)), _params(item))
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and (not item.name.startswith("_") or item.name == "__init__")
            ]
            bases = []
            for base in node.bases:
                try:
                    bases.append(ast.unparse(base))
                except Exception:  # noqa: BLE001
                    continue
            info.classes.append(PyClass(node.name, bases, _one_line(ast.get_docstring(node)), methods))
        elif isinstance(node, ast.If):
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
                and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators)
            ):
                info.has_main = True
    info.guarded_imports = _guarded_imports(tree)
    return info


def _guarded_imports(tree: ast.Module) -> list[tuple[str, int]]:
    """Module-level imports nested in `try`/`if` blocks. The code graph records only top-level import
    statements, so an optional dependency (`try: import x / except ImportError`) would otherwise vanish."""
    found: set[tuple[str, int]] = set()

    def walk(body: list[ast.stmt], nested: bool) -> None:
        for node in body:
            if isinstance(node, ast.Import) and nested:
                found.update((alias.name, 0) for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and nested:
                base = node.module or ""
                found.add((base, node.level))
                found.update((f"{base}.{alias.name}" if base else alias.name, node.level) for alias in node.names)
            elif isinstance(node, ast.Try):
                walk(node.body, True)
                walk(node.orelse, True)
                walk(node.finalbody, True)
                for handler in node.handlers:
                    walk(handler.body, True)
            elif isinstance(node, ast.If):
                walk(node.body, True)
                walk(node.orelse, True)

    walk(tree.body, False)
    return sorted(found)


def _resolve_module(name: str, level: int, importer: str, paths: set[str]) -> str | None:
    """A project module path for an import name, or None. Tries the name as a path from the importer's package
    (relative imports), from the repository root, and below a src/ or lib/ root."""
    if not name and not level:
        return None
    parts = name.split(".") if name else []
    bases: list[list[str]] = []
    if level:
        pkg = importer.split("/")[:-1]
        if level > 1:
            pkg = pkg[: len(pkg) - (level - 1)] if len(pkg) >= level - 1 else []
        bases.append(pkg)
    else:
        bases += [[], *[[root] for root in sorted(_SOURCE_ROOTS)]]
    for base in bases:
        stem = "/".join([*base, *parts])
        for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
            if stem and candidate in paths:
                return candidate
    return None


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def _is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (
        any(p in _TEST_DIR_NAMES for p in parts[:-1])
        or name.startswith("test_")
        or re.search(r"[._-](test|spec)\.[a-z]+$", name) is not None
        or name == "conftest.py"
    )


def _tooling_is_project(root: Path) -> bool:
    """True in OmniEngineering's own repository, where the CLI files are the product rather than embedded tooling."""
    text = _read_text(root / "pyproject.toml") or ""
    return bool(re.search(r"""["']make_ai["']|=\s*["']make_ai:""", text))


def _container_of(path: str) -> str:
    parts = path.split("/")
    if parts and parts[0] in _SOURCE_ROOTS and len(parts) > 2:
        parts = parts[1:]
    return parts[0] if len(parts) > 1 else "(root)"


def _project_name(root: Path, readme: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", readme, re.M)
    if match:
        return match.group(1).strip()
    pyproject = _read_text(root / "pyproject.toml") or ""
    match = re.search(r'^name\s*=\s*"([^"]+)"', pyproject, re.M)
    if match:
        return match.group(1)
    package = _read_json(root / "package.json")
    if isinstance(package, dict) and isinstance(package.get("name"), str):
        return package["name"]
    return root.name


def load_model(root: Path, graph_path: Path | None, out_dir: Path) -> Model:
    root = root.resolve()
    graph_path = graph_path if graph_path is not None else root / GRAPH_DEFAULT_PATH
    graph = _read_json(graph_path) if graph_path.is_file() else None
    notes: list[str] = []
    if not isinstance(graph, dict):
        graph = {}
        notes.append(f"No code graph at {GRAPH_DEFAULT_PATH}; code-derived sections are marked not derived (run `omni graph build`).")
    nodes = {n["id"]: n for n in graph.get("nodes", []) if isinstance(n, dict) and "id" in n}
    edges = sorted(
        (e for e in graph.get("edges", []) if isinstance(e, dict)),
        key=lambda e: (str(e.get("type")), str(e.get("source")), str(e.get("target")), str(e.get("detail", ""))),
    )
    tooling_project = _tooling_is_project(root)
    symbols: dict[str, list[tuple[str, str]]] = {}
    for edge in edges:
        if edge.get("type") == "defines":
            target = nodes.get(str(edge.get("target")))
            source = nodes.get(str(edge.get("source")))
            if target and source and source.get("kind") == "module" and target.get("kind") in ("class", "function", "interface"):
                symbols.setdefault(str(source["id"]), []).append((str(target["kind"]), str(target["name"])))
    modules: list[Module] = []
    for node in sorted(nodes.values(), key=lambda n: str(n["id"])):
        if node.get("kind") != "module" or not node.get("file"):
            continue
        path = str(node["file"]).replace("\\", "/")
        attrs = node.get("attrs") or {}
        test = bool(attrs.get("test")) or _is_test_path(path)
        tooling = (bool(attrs.get("tooling")) or path in _TOOLING_FILES) and not tooling_project
        py = parse_python(root / path) if path.endswith(".py") else None
        container = "tests" if test else ("OmniEngineering tooling" if tooling else _container_of(path))
        package = path.rsplit("/", 1)[0] if "/" in path else "(root)"
        modules.append(
            Module(path, container, package, test, tooling, str(node.get("language") or ""), py, sorted(symbols.get(str(node["id"]), [])))
        )
    requirements: list[dict[str, Any]] = []
    for rel, archived in ((".ai/requirements/requirements.json", False), (".ai/requirements/requirements-archive.json", True)):
        data = _read_json(root / rel)
        items = data.get("requirements") if isinstance(data, dict) else None
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and item.get("id"):
                requirements.append({**item, "_archived": archived})
    ledger = _read_json(root / ".ai/failures/failure-ledger.json")
    failures = [f for f in (ledger.get("failures") if isinstance(ledger, dict) else None) or [] if isinstance(f, dict) and f.get("id")]
    suites_data = _read_json(root / ".ai/test-suites.json")
    suites = [s for s in (suites_data.get("suites") if isinstance(suites_data, dict) else None) or [] if isinstance(s, dict)]
    ruleset = _read_json(root / ".ai/rules/universal-engineering-ruleset.json")
    config = ruleset.get("configuration") if isinstance(ruleset, dict) and isinstance(ruleset.get("configuration"), dict) else {}
    readme = _read_text(root / "README.md") or ""
    inputs = graph.get("inputs") if isinstance(graph.get("inputs"), dict) else {}
    return Model(
        root=root,
        out_dir=out_dir.resolve(),
        project=_project_name(root, readme),
        sha=_git_head(root),
        graph_head=inputs.get("head") if isinstance(inputs.get("head"), str) else None,
        has_graph=bool(nodes),
        has_history=any(n.get("kind") == "commit" for n in nodes.values()),
        nodes=nodes,
        edges=edges,
        modules=modules,
        requirements=requirements,
        failures=sorted(failures, key=lambda f: str(f["id"])),
        suites=sorted(suites, key=lambda s: str(s.get("id", ""))),
        config=config,
        readme=readme,
        notes=notes,
    )


# --------------------------------------------------------------------------
# Markdown and Mermaid helpers
# --------------------------------------------------------------------------


def cell(value: Any) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text.replace("|", "\\|") or "-"


def code(value: Any) -> str:
    text = " ".join(str(value).split()).replace("`", "'")
    return f"`{text}`"


def mlabel(value: Any) -> str:
    """A Mermaid label safe inside double quotes."""
    text = " ".join(str(value).split())
    return text.replace("&", "#amp;").replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;").replace("`", "'")


class Ids:
    """Stable, collision-free Mermaid identifiers: [A-Za-z][A-Za-z0-9_]*, never a keyword."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.ids: dict[str, str] = {}
        self.used: set[str] = set()

    def __call__(self, key: str) -> str:
        if key in self.ids:
            return self.ids[key]
        base = self.prefix + (re.sub(r"[^A-Za-z0-9]+", "_", key).strip("_") or "x")
        candidate, n = base, 2
        while candidate in self.used:
            candidate, n = f"{base}_{n}", n + 1
        self.ids[key] = candidate
        self.used.add(candidate)
        return candidate


def mermaid(lines: list[str]) -> list[str]:
    return ["```mermaid", *lines, "```"]


def not_derived(reason: str) -> str:
    return f"_Not derived: {reason}_"


def link(model: Model, relpath: str, text: str | None = None) -> str:
    target = model.root / relpath
    label = text or relpath
    if not relpath or not target.exists():
        return code(label)
    try:
        rel = os.path.relpath(target, model.out_dir).replace("\\", "/")
    except ValueError:
        return code(label)
    return f"[{label.replace('[', '(').replace(']', ')')}]({rel})"


def header(model: Model, filename: str, title: str) -> list[str]:
    lines = [
        f"# {title}",
        "",
        f"<!-- Generated by `omni docs build` (REQ-060) for {mlabel(model.project)}. Do not edit; regenerate. -->",
        "",
    ]
    present = [(name, why) for name, why in WORKFLOW_SKILLS.get(filename, []) if (model.root / ".claude/skills" / name / "SKILL.md").is_file()]
    if present:
        refs = "; ".join(f"{link(model, f'.claude/skills/{name}/SKILL.md', name)} to {why}" for name, why in present)
        lines += [f"Related assistant workflow: {refs}.", ""]
    return lines


def finish(lines: list[str]) -> str:
    text = "\n".join(lines).rstrip() + "\n"
    return re.sub(r"\n{3,}", "\n\n", text)


# --------------------------------------------------------------------------
# Graph queries
# --------------------------------------------------------------------------


def code_modules(model: Model) -> list[Module]:
    return [m for m in model.modules if not m.test and not m.tooling]


def internal_imports(model: Model) -> set[tuple[str, str]]:
    """(importer, imported) module paths inside the project: the graph's import edges plus imports guarded by
    `try`/`if`, which the graph does not record."""
    cached = model.__dict__.get("_imports")
    if cached is None:
        cached = _internal_imports(model)
        model.__dict__["_imports"] = cached
    return cached


def _internal_imports(model: Model) -> set[tuple[str, str]]:
    paths = model.by_path()
    pairs = set()
    for edge in model.edges:
        if edge.get("type") != "imports":
            continue
        source, target = model.nodes.get(str(edge["source"])), model.nodes.get(str(edge["target"]))
        if not source or not target or source.get("kind") != "module" or target.get("kind") != "module":
            continue
        a, b = str(source.get("file")), str(target.get("file"))
        if a != b and a in paths and b in paths:
            pairs.add((a, b))
    known = set(paths)
    for module in model.modules:
        for name, level in module.py.guarded_imports if module.py else []:
            target = _resolve_module(name, level, module.path, known)
            if target and target != module.path:
                pairs.add((module.path, target))
    return pairs


def external_imports(model: Model) -> dict[str, set[str]]:
    """module path -> names of external modules it imports."""
    found: dict[str, set[str]] = {}
    for edge in model.edges:
        if edge.get("type") == "imports" and str(edge.get("target", "")).startswith("external:"):
            source = model.nodes.get(str(edge["source"]))
            if source and source.get("kind") == "module":
                found.setdefault(str(source.get("file")), set()).add(str(edge["target"])[len("external:") :].split(".")[0])
    return found


def module_of_node(model: Model, node_id: str) -> str | None:
    node = model.nodes.get(node_id)
    return str(node.get("file")) if node and node.get("file") else None


def requirements_for(model: Model, path: str) -> list[dict[str, Any]]:
    hits = []
    for req in model.requirements:
        for scope in req.get("minimum_access_scope") or []:
            scope = str(scope).rstrip("/")
            if scope and (scope == path or path.startswith(scope + "/")):
                hits.append(req)
                break
    return sorted(hits, key=lambda r: str(r["id"]))


def declared_dependencies(root: Path) -> list[str]:
    """Package names declared in pyproject.toml, requirements*.txt and package.json (names only)."""
    names: set[str] = set()
    pyproject = _read_text(root / "pyproject.toml") or ""
    section = ""
    collecting = False
    for raw in pyproject.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("[") and not collecting:
            section = line
            continue
        if not collecting:
            key, sep, rest = line.partition("=")
            wanted = (section == "[project]" and key.strip() == "dependencies") or section == "[project.optional-dependencies]"
            if not (sep and wanted and rest.strip().startswith("[")):
                continue
            collecting, line = True, rest
        for spec in re.findall(r'"([^"]+)"', line):
            match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", spec)
            if match:
                names.add(match.group(0).lower())
        if "]" in line:
            collecting = False
    for path in sorted(root.glob("requirements*.txt")):
        for raw in (_read_text(path) or "").splitlines():
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", raw)
            if match and not raw.strip().startswith(("#", "-")):
                names.add(match.group(1).lower())
    package = _read_json(root / "package.json")
    if isinstance(package, dict):
        for key in ("dependencies", "devDependencies"):
            if isinstance(package.get(key), dict):
                names.update(str(k) for k in package[key])
    return sorted(names)


def deployment_descriptors(root: Path) -> list[str]:
    exclude = []
    gconf = _read_json(root / ".ai/graph-config.json")
    if isinstance(gconf, dict) and isinstance(gconf.get("exclude_code_globs"), list):
        exclude = [str(g).rstrip("/*") for g in gconf["exclude_code_globs"] if isinstance(g, str)]
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        depth = 0 if not rel_dir else rel_dir.count("/") + 1
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in _SKIP_DIRS and (f"{rel_dir}/{d}".lstrip("/") not in exclude or (not rel_dir and d in _DEPLOY_DIRS)) and depth < 3 and not (d.startswith(".") and d not in (".github",))
        )
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}".lstrip("/")
            lower = name.lower()
            if (
                lower.startswith("dockerfile") or lower.endswith(".dockerfile")
                or re.fullmatch(r"(docker-)?compose(\.[a-z0-9_-]+)?\.ya?ml", lower)
                or (rel_dir == ".github/workflows" and lower.endswith((".yml", ".yaml")))
                or lower in (".gitlab-ci.yml", "jenkinsfile", "procfile", "caddyfile", "fly.toml", "render.yaml", "vercel.json", "app.yaml", "serverless.yml")
                or lower.endswith(".tf")
                or (any(p in ("k8s", "kubernetes", "helm", "charts") for p in rel_dir.split("/")) and lower.endswith((".yml", ".yaml")))
            ):
                found.append(rel)
    return sorted(found)


def compose_services(text: str) -> list[str]:
    services, inside = [], False
    for line in text.splitlines():
        if re.match(r"^services:\s*$", line):
            inside = True
            continue
        if inside and re.match(r"^\S", line):
            inside = False
        if inside:
            match = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
            if match:
                services.append(match.group(1))
    return sorted(services)


def entry_points(model: Model) -> list[tuple[str, str]]:
    """(label, function node id or "") for console scripts, then `if __name__ == "__main__"` modules of the project
    (embedded tooling excluded). One entry per function: a console script wins over the module it points at."""
    found: dict[str, str] = {}
    pyproject = _read_text(model.root / "pyproject.toml") or ""
    scripts = re.search(r"^\[project\.scripts\]\s*$(.*?)(?=^\[|\Z)", pyproject, re.M | re.S)
    if scripts:
        for name, target in re.findall(r'^\s*([A-Za-z0-9_.-]+)\s*=\s*"([^"]+)"', scripts.group(1), re.M):
            module, _, func = target.partition(":")
            for m in model.modules:
                stem = m.path[:-3].replace("/", ".") if m.path.endswith(".py") else ""
                if stem and (stem == module or stem.endswith("." + module)):
                    found[f"console script `{name}` -> {target}"] = f"{m.path}::{func}" if func else ""
                    break
            else:
                found[f"console script `{name}` -> {target}"] = ""
    claimed = {v for v in found.values() if v}
    for m in code_modules(model):
        if m.py and m.py.has_main:
            eid = f"{m.path}::main" if any(f.name == "main" for f in m.py.functions) else ""
            if eid not in claimed or not eid:
                found.setdefault(f"`python {m.path}`", eid)
    return sorted(found.items(), key=lambda kv: (not kv[0].startswith("console"), kv[0]))


def summarize_entries(entries: list[tuple[str, str]], limit: int = 8) -> list[str]:
    scripts = [label for label, _ in entries if label.startswith("console")]
    mains = [label for label, _ in entries if not label.startswith("console")]
    out = [f"- {label}" for label in scripts]
    if len(mains) <= limit:
        out += [f"- {label}" for label in mains]
    else:
        by_dir: dict[str, int] = {}
        for label in mains:
            path = label.strip("`").split(" ", 1)[1]
            folder = path.rsplit("/", 1)[0] + "/" if "/" in path else "(root)"
            by_dir[folder] = by_dir.get(folder, 0) + 1
        out.append(f"- {len(mains)} runnable `__main__` scripts: " + ", ".join(f"{n} in `{d}`" for d, n in sorted(by_dir.items())))
    return out


# --------------------------------------------------------------------------
# Diagrams
# --------------------------------------------------------------------------


@dataclass
class Diagram:
    lines: list[str]
    notes: list[str]


def _aggregate(pairs: set[tuple[str, str]], unit: dict[str, str]) -> tuple[list[tuple[str, str, int]], int]:
    weights: dict[tuple[str, str], int] = {}
    for a, b in pairs:
        ua, ub = unit.get(a), unit.get(b)
        if ua and ub and ua != ub:
            weights[(ua, ub)] = weights.get((ua, ub), 0) + 1
    ranked = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(a, b, w) for (a, b), w in ranked[:MAX_DIAGRAM_EDGES]], max(0, len(ranked) - MAX_DIAGRAM_EDGES)


def _edge_line(ids: Ids, a: str, b: str, weight: int) -> str:
    return f"    {ids(a)} -->|{weight}| {ids(b)}" if weight > 1 else f"    {ids(a)} --> {ids(b)}"


def container_diagram(model: Model) -> Diagram | None:
    mods = [m for m in model.modules if not m.test]
    if not mods:
        return None
    unit = {m.path: m.container for m in mods}
    edges, dropped = _aggregate(internal_imports(model), unit)
    ids = Ids("c_")
    counts: dict[str, int] = {}
    for m in mods:
        counts[m.container] = counts.get(m.container, 0) + 1
    lines = ["flowchart LR"]
    for container in sorted(counts):
        lines.append(f'    {ids(container)}["{mlabel(container)}<br/>{counts[container]} module(s)"]')
    lines += [_edge_line(ids, a, b, w) for a, b, w in edges]
    notes = ["Edge labels count module-level imports between containers."]
    if dropped:
        notes.append(f"{dropped} weaker container edge(s) omitted.")
    return Diagram(lines, notes)


def component_diagram(model: Model) -> Diagram | None:
    mods = sorted((m for m in model.modules if not m.test), key=lambda m: m.path)
    if not mods:
        return None
    pairs = internal_imports(model)
    notes: list[str] = []
    if len(mods) <= MAX_COMPONENT_NODES:
        unit = {m.path: m.path for m in mods}
    else:
        live = {m.path for m in mods}
        degree: dict[str, int] = {}
        for a, b in pairs:
            if a in live and b in live:
                degree[a] = degree.get(a, 0) + 1
                degree[b] = degree.get(b, 0) + 1
        ranked = sorted(mods, key=lambda m: (-degree.get(m.path, 0), m.path))
        keep: set[str] = set()
        for m in ranked:
            trial = keep | {m.path}
            leftover = {(x.container, x.package) for x in mods if x.path not in trial}
            if len(trial) + len(leftover) > MAX_COMPONENT_NODES:
                break
            keep = trial
        unit = {m.path: (m.path if m.path in keep else f"pkg:{m.container}:{m.package}") for m in mods}
        collapsed: dict[str, int] = {}
        for m in mods:
            if m.path not in keep:
                collapsed[m.package] = collapsed.get(m.package, 0) + 1
        notes.append(
            f"{len(mods)} modules exceed the {MAX_COMPONENT_NODES}-node cap: the {len(keep)} most connected are shown; "
            + ", ".join(f"{n} in `{pkg}`" for pkg, n in sorted(collapsed.items()))
            + " are collapsed into one node per package."
        )
    edges, dropped = _aggregate(pairs, unit)
    ids = Ids("m_")
    groups: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    for m in mods:
        u = unit[m.path]
        if u not in labels:
            if u.startswith("pkg:"):
                n = sum(1 for x in mods if unit[x.path] == u)
                labels[u] = f"{m.package}/ ({n} module{'' if n == 1 else 's'}, collapsed)"
            else:
                labels[u] = m.path
            groups.setdefault(m.container, []).append(u)
    gids = Ids("g_")
    lines = ["flowchart LR"]
    for container in sorted(groups):
        lines.append(f'    subgraph {gids(container)} ["{mlabel(container)}"]')
        for u in sorted(groups[container]):
            lines.append(f'        {ids(u)}["{mlabel(labels[u])}"]')
        lines.append("    end")
    lines += [_edge_line(ids, a, b, w) for a, b, w in edges]
    notes.append("Arrows point from the importing module to the imported one; labels count imports when more than one.")
    if dropped:
        notes.append(f"{dropped} weaker edge(s) omitted to stay under {MAX_DIAGRAM_EDGES} edges.")
    tests = [m for m in model.modules if m.test]
    if tests:
        notes.append(f"{len(tests)} test module(s) are left out; see the test plan.")
    return Diagram(lines, notes)


def context_diagram(model: Model) -> Diagram:
    ids = Ids("x_")
    system = ids("system")
    lines = ["flowchart LR", f'    {system}["{mlabel(model.project)}"]']
    notes: list[str] = []
    entries = entry_points(model)
    if entries:
        lines.append(f'    {ids("user")}["User / developer<br/>{len(entries)} entry point(s)"]')
        lines.append(f"    {ids('user')} -->|runs| {system}")
    deps = declared_dependencies(model.root)
    for dep in deps[:MAX_CONTEXT_DEPS]:
        lines.append(f'    {ids("dep:" + dep)}["{mlabel(dep)}"]')
        lines.append(f"    {system} -->|uses| {ids('dep:' + dep)}")
    if len(deps) > MAX_CONTEXT_DEPS:
        lines.append(f'    {ids("dep:more")}["{len(deps) - MAX_CONTEXT_DEPS} more declared dependencies"]')
        lines.append(f"    {system} -->|uses| {ids('dep:more')}")
    workflows = [d for d in deployment_descriptors(model.root) if d.startswith(".github/workflows/")]
    if workflows:
        lines.append(f'    {ids("ci")}["CI: {len(workflows)} GitHub Actions workflow(s)"]')
        lines.append(f"    {ids('ci')} -->|builds and checks| {system}")
    if not entries:
        notes.append("No console script or `__main__` entry point was found, so no user actor is drawn.")
    if not deps:
        notes.append("No dependency manifest (pyproject dependencies, requirements*.txt, package.json) declares external packages.")
    notes.append("Actors and external systems come only from entry points, declared dependencies and CI files.")
    return Diagram(lines, notes)


def class_relations(model: Model, class_ids: set[str] | None = None) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """(internal inherits, external inherits as (class id, base name), creates) among non-test, non-tooling classes."""
    allowed = {m.path for m in code_modules(model)}

    def ok(node_id: str) -> bool:
        node = model.nodes.get(node_id)
        return bool(node and node.get("kind") == "class" and str(node.get("file")) in allowed and (class_ids is None or node_id in class_ids))

    inherits, external, creates = set(), set(), set()
    for edge in model.edges:
        source, target = str(edge.get("source")), str(edge.get("target"))
        if edge.get("type") == "inherits" and ok(source):
            if ok(target):
                inherits.add((source, target))
            elif target.startswith("external:"):
                external.add((source, target[len("external:") :]))
        elif edge.get("type") == "calls" and ok(target):
            owner = source.rsplit("::", 1)[0]
            snode = model.nodes.get(source)
            if snode and snode.get("kind") == "method" and ok(owner) and owner != target:
                creates.add((owner, target))
    return sorted(inherits), sorted(external), sorted(creates)


def _class_info(model: Model, class_id: str) -> PyClass | None:
    node = model.nodes.get(class_id) or {}
    mod = model.by_path().get(str(node.get("file")))
    if mod and mod.py:
        for cls in mod.py.classes:
            if cls.name == node.get("name"):
                return cls
    return None


def class_diagram(model: Model, candidates: list[str], cap: int) -> Diagram | None:
    if not candidates:
        return None
    inherits, external, creates = class_relations(model, set(candidates))
    degree: dict[str, int] = {}
    for a, b in inherits + creates:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    for a, _ in external:
        degree[a] = degree.get(a, 0) + 1
    ranked = sorted(candidates, key=lambda c: (-degree.get(c, 0), c))
    shown = set(ranked[:cap])
    notes = []
    if len(candidates) > cap:
        notes.append(f"{len(candidates)} classes exceed the {cap}-class cap; the {cap} with the most relations are shown.")
    ids = Ids("C_")
    lines = ["classDiagram"]
    for cid in sorted(shown):
        node = model.nodes[cid]
        info = _class_info(model, cid)
        methods = [f for f in (info.methods if info else []) if not f.name.startswith("_")][:MAX_METHODS]
        lines.append(f'    class {ids(cid)}["{mlabel(node.get("name"))}"] {{')
        for method in methods:
            params = [p.lstrip("*") for p in method.params]
            lines.append(f"        +{method.name}({', '.join(params)})")
        lines.append("    }")
    bases_drawn: set[str] = set()
    for a, b in inherits:
        if a in shown and b in shown:
            lines.append(f"    {ids(b)} <|-- {ids(a)}")
    for a, base in external:
        if a in shown:
            bid = ids("ext:" + base)
            if bid not in bases_drawn:
                lines.append(f'    class {bid}["{mlabel(base)}"]')
                lines.append(f"    <<external>> {bid}")
                bases_drawn.add(bid)
            lines.append(f"    {bid} <|-- {ids(a)}")
    for a, b in creates:
        if a in shown and b in shown:
            lines.append(f"    {ids(a)} ..> {ids(b)} : creates")
    notes.append("`<|--` is inheritance; `..> creates` means a method of the first class instantiates the second (resolved call edge). Attribute composition is not in the graph and is not drawn.")
    return Diagram(lines, notes)


def runtime_flow(model: Model, entry_id: str) -> tuple[Diagram | None, str]:
    """A depth-2 static call flow from an entry function, using only confidently resolved call edges."""
    if entry_id not in model.nodes:
        return None, "the entry function is not in the code graph"
    calls: dict[str, set[str]] = {}
    for edge in model.edges:
        if edge.get("type") != "calls":
            continue
        target = model.nodes.get(str(edge.get("target")))
        detail = str(edge.get("detail", ""))
        if not target or target.get("kind") not in ("function", "method"):
            continue
        if edge.get("provenance") == "INFERRED" and not detail.startswith(_CONFIDENT_CALL_DETAILS):
            continue
        calls.setdefault(str(edge["source"]), set()).add(str(edge["target"]))
    first = sorted(calls.get(entry_id, set()))
    if len(first) < MIN_FLOW_CALLEES:
        return None, (
            f"confidently resolved call edges from `{model.nodes[entry_id].get('name')}` reach only {len(first)} function(s); "
            "dispatch through tables, callbacks or dynamic lookup is invisible to the static graph"
        )
    ids = Ids("f_")
    lines = ["flowchart TD", f'    {ids(entry_id)}["{mlabel(model.nodes[entry_id].get("name"))}"]']
    shown = {entry_id}
    edges: list[tuple[str, str]] = []
    omitted = 0
    for callee in first:
        if len(shown) >= MAX_FLOW_NODES:
            omitted += 1
            continue
        shown.add(callee)
        edges.append((entry_id, callee))
    for callee in sorted(c for c in shown if c != entry_id):
        for second in sorted(calls.get(callee, set())):
            if second in shown:
                edges.append((callee, second))
            elif len(shown) < MAX_FLOW_NODES:
                shown.add(second)
                edges.append((callee, second))
            else:
                omitted += 1
    for node_id in sorted(shown - {entry_id}):
        lines.append(f'    {ids(node_id)}["{mlabel(model.nodes[node_id].get("name"))}"]')
    for a, b in sorted(set(edges)):
        if a != b:
            lines.append(f"    {ids(a)} --> {ids(b)}")
    notes = ["Static direct calls only (same-module, enclosing-class or import-resolved); call order and conditions are not shown."]
    if omitted:
        notes.append(f"{omitted} further call(s) omitted at the {MAX_FLOW_NODES}-node cap.")
    return Diagram(lines, notes), ""


def emit(diagram: Diagram) -> list[str]:
    return [*mermaid(diagram.lines), "", *[f"- {note}" for note in diagram.notes], ""]


# --------------------------------------------------------------------------
# Shared derived data
# --------------------------------------------------------------------------


def _req_sort_key(req: dict[str, Any]) -> tuple[str, int, str]:
    match = re.match(r"([A-Za-z]+)-(\d+)", str(req["id"]))
    return (match.group(1), int(match.group(2)), "") if match else (str(req["id"]), 0, "")


def active_requirements(model: Model) -> list[dict[str, Any]]:
    return sorted((r for r in model.requirements if not r["_archived"]), key=_req_sort_key)


def failures_for(model: Model, req_id: str) -> list[dict[str, Any]]:
    return [f for f in model.failures if str(f.get("requirement", "")) == req_id]


def linked_tests(model: Model, req: dict[str, Any]) -> list[str]:
    """Tests tied to a requirement by real data: test paths in its scope, test files changed by the commits that
    deliver it, regression tests of its failures, and test modules the graph says verify a module in its scope."""
    tests: set[str] = set()
    scope = [str(s).rstrip("/") for s in req.get("minimum_access_scope") or []]
    for s in scope:
        if _is_test_path(s):
            tests.add(s)
    for failure in failures_for(model, str(req["id"])):
        for t in failure.get("regression_tests") or []:
            tests.add(str(t).split("::", 1)[0])
    _, touched = commit_files_for(model, str(req["id"]))
    tests.update(f for f in touched if _is_test_path(f) and (model.root / f).is_file())
    in_scope = {m.path for m in model.modules if any(m.path == s or m.path.startswith(s + "/") for s in scope) and not m.test}
    for edge in model.edges:
        if edge.get("type") == "verifies":
            target = module_of_node(model, str(edge.get("target")))
            source = model.nodes.get(str(edge.get("source")))
            if target in in_scope and source and source.get("kind") == "module":
                tests.add(str(source.get("file")))
    return sorted(tests)


def commit_files_for(model: Model, req_id: str) -> tuple[int, set[str]]:
    node_id = f"req:{req_id}"
    commits = {str(e["source"]) for e in model.edges if e.get("type") == "delivers" and e.get("target") == node_id}
    files: set[str] = set()
    for edge in model.edges:
        if edge.get("type") == "modifies" and edge.get("source") in commits:
            target = model.nodes.get(str(edge["target"]))
            if target and target.get("file"):
                files.add(str(target["file"]))
            else:
                files.add(str(edge["target"]).removeprefix("file:"))
    return len(commits), files


def resolve_doc(model: Model, entry: str) -> str | None:
    entry = entry.strip().rstrip("/")
    if not entry:
        return None
    if (model.root / entry).exists():
        return entry
    if "/" not in entry and " " not in entry:
        for child in sorted(model.root.iterdir(), key=lambda p: p.name):
            if child.is_file() and child.stem.lower() == entry.lower():
                return child.name
    return None


def section(title: str, level: int = 2) -> list[str]:
    return ["#" * level + " " + title, ""]


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------


def render_architecture(model: Model) -> str:
    lines = header(model, "architecture.md", "Software Architecture Document (SAD)")
    lines += [
        "Structured after arc42 and ISO/IEC/IEEE 42010. Every view is derived from the code graph, the requirement "
        "registry, the failure ledger and files on disk; a view with no data behind it says so.",
        "",
    ]
    reqs = active_requirements(model)
    # 1. Introduction and goals
    lines += section("1. Introduction and goals")
    intro = _readme_intro(model.readme)
    lines += [f"**System:** {model.project}", ""]
    lines += ([intro, ""] if intro else [not_derived("README.md has no introductory paragraph."), ""])
    goals = [r for r in reqs if r.get("priority") in ("critical", "high") and r.get("status") not in _TERMINAL]
    if goals:
        lines += ["Open high-priority requirements (the current goals):", ""]
        lines += [f"- {r['id']} ({r.get('status')}): {cell(r.get('title'))}" for r in goals[:MAX_RECENT]]
        if len(goals) > MAX_RECENT:
            lines.append(f"- ... and {len(goals) - MAX_RECENT} more; see the SRS.")
        lines.append("")
    elif reqs:
        lines += [f"All {len(reqs)} registered requirements are completed or withdrawn; no open high-priority goal is recorded.", ""]
    else:
        lines += [not_derived("the requirement registry is empty."), ""]
    # 2. Constraints
    lines += section("2. Constraints")
    constraints = _constraints(model)
    lines += ([*[f"- {c}" for c in constraints], ""] if constraints else [not_derived("no language, interpreter or project-policy constraints are recorded."), ""])
    # 3. Context
    lines += section("3. System context")
    lines += emit(context_diagram(model))
    entries = entry_points(model)
    if entries:
        lines += ["Entry points:", "", *summarize_entries(entries), ""]
    # 4. Building blocks
    lines += section("4. Building-block view")
    if not model.has_graph:
        lines += [not_derived("no code graph."), ""]
    else:
        lines += section("4.1 Containers (level 1)", 3)
        cont = container_diagram(model)
        lines += emit(cont) if cont else [not_derived("the graph holds no source modules."), ""]
        lines += section("4.2 Components (level 2)", 3)
        comp = component_diagram(model)
        lines += emit(comp) if comp else [not_derived("the graph holds no source modules."), ""]
        lines += [f"Per-module detail is in the {_doc_link('design.md')} and the {_doc_link('modules.md')}.", ""]
    # 5. Runtime
    lines += section("5. Runtime view")
    lines += _runtime_section(model, entries)
    # 6. Deployment
    lines += section("6. Deployment view")
    lines += _deployment_section(model)
    # 7. Cross-cutting concepts
    lines += section("7. Cross-cutting concepts")
    lines += _crosscutting_section(model)
    # 8. Decisions
    lines += section("8. Architecture decisions")
    lines += _decisions_section(model)
    # 9. Quality requirements
    lines += section("9. Quality requirements")
    quality = [r for r in reqs if _QUALITY_PATTERN.search(f"{r.get('category', '')} {r.get('title', '')}")]
    if quality:
        lines += ["| ID | Status | Category | Requirement |", "| --- | --- | --- | --- |"]
        lines += [f"| {r['id']} | {cell(r.get('status'))} | {cell(r.get('category'))} | {cell(r.get('title'))} |" for r in quality]
        lines += ["", "Selected by category or title naming a quality attribute (performance, security, reliability, usability, ...).", ""]
    else:
        lines += [not_derived("no registered requirement names a quality attribute in its category or title."), ""]
    # 10. Risks and technical debt
    lines += section("10. Risks and technical debt")
    lines += _risks_section(model, reqs)
    # 11. Glossary
    lines += section("11. Glossary")
    lines += _glossary_section(model)
    return finish(lines)


def _doc_link(name: str) -> str:
    title = next((t for f, t, _ in DOCUMENTS if f == name), name)
    return f"[{title}]({name})"


def _readme_intro(readme: str) -> str:
    in_code = False
    for block in re.split(r"\n\s*\n", readme):
        text = block.strip()
        if text.startswith("```"):
            in_code = not in_code if text.count("```") % 2 else in_code
            continue
        if in_code or not text or text.startswith(("#", "!", "[!", "<", "|", "-", "*", ">")) or text.startswith("[!["):
            continue
        line = " ".join(text.split())
        return line if len(line) <= 600 else line[:597].rstrip() + "..."
    return ""


def _constraints(model: Model) -> list[str]:
    out = []
    pyproject = _read_text(model.root / "pyproject.toml") or ""
    match = re.search(r'^requires-python\s*=\s*"([^"]+)"', pyproject, re.M)
    if match:
        out.append(f"Python {match.group(1)} (pyproject.toml `requires-python`).")
    languages: dict[str, int] = {}
    for m in model.modules:
        if m.language:
            languages[m.language] = languages.get(m.language, 0) + 1
    if languages:
        out.append("Languages in the code graph: " + ", ".join(f"{k} ({v} modules)" for k, v in sorted(languages.items())) + ".")
    for key in ("allow_broad_refactor", "allow_dependency_changes", "allow_schema_changes", "allow_destructive_data_changes"):
        if model.config.get(key) is False:
            out.append(f"Project policy `{key}` is false: such changes need explicit approval.")
    stack = model.config.get("primary_language_or_stack")
    if isinstance(stack, str) and stack and not stack.startswith("<"):
        out.append(f"Declared stack: {stack}.")
    return out


def _runtime_section(model: Model, entries: list[tuple[str, str]]) -> list[str]:
    if not model.has_graph:
        return [not_derived("no code graph."), ""]
    targets = [(label, eid) for label, eid in entries if eid and label.startswith("console")]
    if not targets:
        targets = [(label, eid) for label, eid in entries if eid][:3]
    if not targets:
        return [not_derived("no entry function was found to trace from."), ""]
    lines: list[str] = []
    seen: set[str] = set()
    for label, eid in targets:
        if eid in seen:
            continue
        seen.add(eid)
        diagram, reason = runtime_flow(model, eid)
        lines += [f"**{label}**", ""]
        lines += emit(diagram) if diagram else [not_derived(reason + "."), ""]
    return lines


def _deployment_section(model: Model) -> list[str]:
    descriptors = deployment_descriptors(model.root)
    if not descriptors:
        return [not_derived("no deployment descriptors (Dockerfile, compose file, CI workflow, Terraform, Kubernetes manifests) were found."), ""]
    ids = Ids("d_")
    gids = Ids("dg_")
    lines = ["flowchart LR"]
    groups: dict[str, list[tuple[str, str]]] = {}
    for rel in descriptors:
        name = rel.rsplit("/", 1)[-1].lower()
        if rel.startswith(".github/workflows/") or name in (".gitlab-ci.yml", "jenkinsfile"):
            text = _read_text(model.root / rel) or ""
            match = re.search(r"^name:\s*['\"]?(.+?)['\"]?\s*$", text, re.M)
            groups.setdefault("CI pipelines", []).append((rel, f"{match.group(1) if match else rel.rsplit('/', 1)[-1]} ({rel})"))
        elif re.fullmatch(r"(docker-)?compose(\.[a-z0-9_-]+)?\.ya?ml", name):
            services = compose_services(_read_text(model.root / rel) or "")
            for svc in services:
                groups.setdefault(f"Services in {rel}", []).append((f"{rel}#{svc}", f"service {svc}"))
            if not services:
                groups.setdefault("Runtime", []).append((rel, rel))
        elif name.startswith("dockerfile") or name.endswith(".dockerfile"):
            groups.setdefault("Container images", []).append((rel, f"image built from {rel}"))
        elif name.endswith(".tf"):
            groups.setdefault("Infrastructure as code", []).append((rel, rel))
        else:
            groups.setdefault("Runtime", []).append((rel, rel))
    for group in sorted(groups):
        lines.append(f'    subgraph {gids(group)} ["{mlabel(group)}"]')
        for key, label in sorted(groups[group]):
            lines.append(f'        {ids(key)}["{mlabel(label)}"]')
        lines.append("    end")
    out = [*mermaid(lines), "", "Descriptors found:", ""]
    out += [f"- {link(model, rel)}" for rel in descriptors]
    out += ["", "- Nodes are grouped by descriptor type; how they connect at run time is not derived (no edges are drawn).", ""]
    return out


def _crosscutting_section(model: Model) -> list[str]:
    lines: list[str] = []
    pairs = internal_imports(model)
    inbound: dict[str, int] = {}
    for a, b in pairs:
        if not model.by_path()[a].test:
            inbound[b] = inbound.get(b, 0) + 1
    hubs = sorted(((n, p) for p, n in inbound.items() if n >= 3 and not model.by_path()[p].test), key=lambda t: (-t[0], t[1]))
    if hubs:
        lines += ["Shared modules (imported by three or more non-test modules) carry concerns used across the system:", ""]
        for n, p in hubs[:10]:
            mod = model.by_path()[p]
            purpose = mod.py.doc if mod.py and mod.py.doc else "no module docstring"
            lines.append(f"- {code(p)}: imported by {n}; {cell(purpose)}")
        lines.append("")
    externals: dict[str, int] = {}
    for path, names in external_imports(model).items():
        mod = model.by_path().get(path)
        if mod and not mod.test:
            for name in names:
                externals[name] = externals.get(name, 0) + 1
    common = sorted(((n, name) for name, n in externals.items() if n >= 3), key=lambda t: (-t[0], t[1]))
    if common:
        lines += ["External modules imported by three or more source modules: " + ", ".join(f"{code(name)} ({n})" for n, name in common[:15]) + ".", ""]
    rulepacks = [n for n in model.nodes.values() if n.get("kind") == "rulepack"]
    if rulepacks:
        lines += ["Engineering rulepacks that apply across the codebase (`.ai/rules`):", ""]
        for rp in sorted(rulepacks, key=lambda n: str(n["id"])):
            lines.append(f"- {code(rp.get('name'))}: {cell(rp.get('summary'))}")
        lines.append("")
    return lines or [not_derived("no shared modules, common imports or rulepacks were found."), ""]


def _decisions_section(model: Model) -> list[str]:
    found: list[str] = []
    for rel in (".ai/project-context.md", "docs/architecture.md", "ARCHITECTURE.md", "docs/design.md"):
        if (model.root / rel).is_file():
            found.append(rel)
    for pattern in ("docs/adr/*.md", "docs/adrs/*.md", "docs/decisions/*.md", "adr/*.md", "doc/adr/*.md", "design/*.md"):
        found += sorted(p.relative_to(model.root).as_posix() for p in model.root.glob(pattern))
    found = sorted(set(found))
    if not found:
        return [not_derived("no ADR directory, `.ai/project-context.md` or architecture/design document was found."), ""]
    lines = ["Decision records and design documents in the repository:", ""]
    for rel in found:
        title = re.search(r"^#\s+(.+)$", _read_text(model.root / rel) or "", re.M)
        lines.append(f"- {link(model, rel)}" + (f": {cell(title.group(1))}" if title else ""))
    return [*lines, ""]


def _risks_section(model: Model, reqs: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    open_failures = [f for f in model.failures if f.get("status") == "open"]
    if open_failures:
        lines += ["Open failures in the failure ledger:", "", "| ID | Severity | Requirement | Title |", "| --- | --- | --- | --- |"]
        lines += [f"| {f['id']} | {cell(f.get('severity'))} | {cell(f.get('requirement'))} | {cell(f.get('title'))} |" for f in open_failures]
        lines.append("")
    else:
        lines += [f"No open failures ({len(model.failures)} recorded in the ledger, none open).", ""]
    recurring = [f for f in model.failures if f.get("recurrence_of")]
    if recurring:
        lines += ["Recurring failures (technical debt that came back): " + ", ".join(f"{f['id']} repeats {f['recurrence_of']}" for f in recurring) + ".", ""]
    risky = [(r, n) for r in reqs if r.get("status") not in _TERMINAL for n in (r.get("risk_notes") or []) if str(n).strip()]
    if risky:
        lines += ["Risk notes on open requirements:", ""]
        lines += [f"- {r['id']}: {cell(n)}" for r, n in risky]
        lines.append("")
    else:
        lines += ["No open requirement carries a risk note.", ""]
    return lines


def _glossary_section(model: Model) -> list[str]:
    terms: list[tuple[str, str, str]] = []
    for m in code_modules(model):
        for cls in m.py.classes if m.py else []:
            if cls.doc:
                terms.append((cls.name, cls.doc, m.path))
    terms.sort(key=lambda t: (t[0].lower(), t[2]))
    if not terms:
        return [not_derived("no documented public classes to draw terms from, and no glossary file is generated from prose."), ""]
    lines = ["Terms defined in code (public classes with a docstring):", "", "| Term | Meaning | Defined in |", "| --- | --- | --- |"]
    lines += [f"| {cell(n)} | {cell(d)} | {code(p)} |" for n, d, p in terms[:MAX_GLOSSARY]]
    if len(terms) > MAX_GLOSSARY:
        lines.append(f"\n{len(terms) - MAX_GLOSSARY} more documented classes are listed in the SDD.")
    return [*lines, ""]


def render_design(model: Model) -> str:
    lines = header(model, "design.md", "Software Design Document (SDD)")
    lines += [
        "Organised after IEEE 1016: for each module, its purpose, interfaces, data structures, dependencies and the "
        "design rationale a docstring or requirement records. Interfaces are read from the source with Python's `ast`; "
        "dependencies come from the code graph.",
        "",
    ]
    mods = code_modules(model)
    if not mods:
        return finish(lines + [not_derived("no source modules in the code graph (run `omni graph build`)."), ""])
    tooling = [m for m in model.modules if m.tooling]
    if tooling:
        lines += [f"{len(tooling)} embedded OmniEngineering tooling module(s) ({', '.join(code(m.path) for m in tooling)}) are not documented here.", ""]
    imports = internal_imports(model)
    ext = external_imports(model)
    by_package: dict[str, list[Module]] = {}
    for m in mods:
        by_package.setdefault(m.package, []).append(m)
    class_nodes: dict[str, list[str]] = {}
    for node in model.nodes.values():
        if node.get("kind") == "class":
            mod = model.by_path().get(str(node.get("file")))
            if mod and mod in mods and not str(node.get("name")).startswith("_"):
                class_nodes.setdefault(mod.package, []).append(str(node["id"]))
    for package in sorted(by_package):
        lines += section(f"Package {package}")
        diagram = class_diagram(model, sorted(class_nodes.get(package, [])), MAX_PACKAGE_CLASSES)
        if diagram:
            lines += ["Classes in this package:", "", *emit(diagram)]
        for m in sorted(by_package[package], key=lambda x: x.path):
            lines += section(f"Module {m.path}", 3)
            lines += [f"Source: {link(model, m.path)}", ""]
            lines += ["**Purpose.** " + (m.py.doc if m.py and m.py.doc else "_Not derived: no module docstring._"), ""]
            lines += ["**Interfaces.**", ""]
            if m.py:
                funcs = m.py.functions
                if funcs:
                    for f in funcs[:MAX_SYMBOLS]:
                        lines.append(f"- {code(f.signature)}" + (f": {cell(f.doc)}" if f.doc else ""))
                    if len(funcs) > MAX_SYMBOLS:
                        lines.append(f"- ... and {len(funcs) - MAX_SYMBOLS} more public functions.")
                else:
                    lines.append("- No public module-level functions.")
            elif m.symbols:
                fns = [n for k, n in m.symbols if k == "function"]
                lines += [f"- {code(n)}" for n in fns[:MAX_SYMBOLS]] or ["- No public functions in the graph."]
            else:
                lines.append("- " + not_derived("no symbols parsed for this module."))
            lines.append("")
            lines += ["**Data structures.**", ""]
            classes = m.py.classes if m.py else [PyClass(n, [], "", []) for k, n in m.symbols if k == "class"]
            if classes:
                for cls in classes:
                    bases = f"({', '.join(cls.bases)})" if cls.bases else ""
                    lines.append(f"- {code('class ' + cls.name + bases)}" + (f": {cell(cls.doc)}" if cls.doc else ""))
                    for meth in cls.methods[:MAX_METHODS]:
                        lines.append(f"  - {code(meth.signature)}" + (f": {cell(meth.doc)}" if meth.doc else ""))
                    if len(cls.methods) > MAX_METHODS:
                        lines.append(f"  - ... and {len(cls.methods) - MAX_METHODS} more methods.")
            else:
                lines.append("- No public classes.")
            lines.append("")
            outbound = sorted(b for a, b in imports if a == m.path)
            inbound = sorted(a for a, b in imports if b == m.path and not model.by_path()[a].test)
            lines += ["**Dependencies.**", ""]
            lines.append("- Imports: " + (", ".join(code(p) for p in outbound) if outbound else "no project modules."))
            lines.append("- Imported by: " + (", ".join(code(p) for p in inbound) if inbound else "no project modules."))
            if ext.get(m.path):
                lines.append("- External: " + ", ".join(code(n) for n in sorted(ext[m.path])))
            lines.append("")
            reqs = requirements_for(model, m.path)
            rationale = m.py.rationale if m.py else ""
            lines += ["**Design rationale.**", ""]
            if rationale:
                lines += [rationale, ""]
            if reqs:
                lines += [f"Requirements scoped to this module: " + ", ".join(f"{r['id']} ({cell(r.get('title'))})" for r in reqs[:10])
                          + (f", and {len(reqs) - 10} more" if len(reqs) > 10 else "") + ".", ""]
            if not rationale and not reqs:
                lines += [not_derived("no docstring rationale and no requirement names this module in its scope."), ""]
    return finish(lines)


def render_modules(model: Model) -> str:
    lines = header(model, "modules.md", "Module Reference")
    mods = [m for m in model.modules if not m.test]
    if not mods:
        return finish(lines + [not_derived("no source modules in the code graph."), ""])
    imports = internal_imports(model)
    lines += [f"{len(mods)} source module(s); full interfaces are in the {_doc_link('design.md')}.", ""]
    lines += ["| Module | Container | Purpose | Public symbols | Imports | Imported by |", "| --- | --- | --- | --- | --- | --- |"]
    for m in sorted(mods, key=lambda x: x.path):
        count = (len(m.py.functions) + len(m.py.classes)) if m.py else len([s for s in m.symbols if not s[1].startswith("_")])
        out = sorted(b for a, b in imports if a == m.path)
        inn = sorted(a for a, b in imports if b == m.path)
        purpose = m.py.doc if m.py and m.py.doc else "-"
        lines.append(f"| {link(model, m.path)} | {cell(m.container)} | {cell(purpose)} | {count} | {cell(', '.join(out))} | {cell(', '.join(inn))} |")
    tests = [m for m in model.modules if m.test]
    if tests:
        lines += ["", f"{len(tests)} test module(s) are listed in the {_doc_link('test-plan.md')}."]
    return finish(lines)


def render_class_diagram(model: Model) -> str:
    lines = header(model, "class-diagram.md", "Class Diagram")
    allowed = {m.path for m in code_modules(model)}
    classes = sorted(
        str(n["id"]) for n in model.nodes.values()
        if n.get("kind") == "class" and str(n.get("file")) in allowed and not str(n.get("name")).startswith("_")
    )
    diagram = class_diagram(model, classes, MAX_CLASSES)
    if not diagram:
        return finish(lines + [not_derived("the code graph has no classes outside tests and embedded tooling."), ""])
    lines += [f"{len(classes)} class(es) in the code graph; per-package diagrams are in the {_doc_link('design.md')}.", ""]
    return finish(lines + emit(diagram))


def render_requirements(model: Model) -> str:
    lines = header(model, "requirements.md", "Software Requirements Specification (SRS)")
    reqs = active_requirements(model)
    archived = sorted((r for r in model.requirements if r["_archived"]), key=_req_sort_key)
    lines += section("1. Introduction")
    lines += [
        f"This specification lists every requirement in `.ai/requirements/requirements.json` for {model.project}, "
        "generated from the registry. Change requirements with `omni requirement add|update|complete`, never here.",
        "",
    ]
    if not reqs and not archived:
        return finish(lines + [not_derived("the requirement registry is empty."), ""])
    lines += section("2. Overall description")
    counts: dict[str, int] = {}
    for r in reqs:
        counts[str(r.get("status"))] = counts.get(str(r.get("status")), 0) + 1
    lines += ["| Status | Requirements |", "| --- | --- |"]
    lines += [f"| {s} | {counts[s]} |" for s in sorted(counts, key=lambda s: (_STATUS_ORDER.index(s) if s in _STATUS_ORDER else 99, s))]
    categories: dict[str, list[dict[str, Any]]] = {}
    for r in reqs:
        categories.setdefault(str(r.get("category") or "Uncategorised"), []).append(r)
    lines += ["", f"{len(reqs)} active requirement(s) in {len(categories)} categor{'y' if len(categories) == 1 else 'ies'}" + (f"; {len(archived)} archived." if archived else "."), ""]
    functional = {c: rs for c, rs in categories.items() if not _QUALITY_PATTERN.search(c)}
    quality = {c: rs for c, rs in categories.items() if _QUALITY_PATTERN.search(c)}
    lines += section("3. Functional requirements")
    lines += _req_blocks(functional) if functional else [not_derived("every category names a quality attribute."), ""]
    lines += section("4. Non-functional requirements")
    lines += _req_blocks(quality) if quality else [not_derived("no requirement category names a quality attribute (performance, security, reliability, ...)."), ""]
    if archived:
        lines += section("5. Archived requirements")
        lines += ["| ID | Status | Title |", "| --- | --- | --- |"]
        lines += [f"| {r['id']} | {cell(r.get('status'))} | {cell(r.get('title'))} |" for r in archived]
        lines.append("")
    return finish(lines)


def _req_blocks(categories: dict[str, list[dict[str, Any]]]) -> list[str]:
    lines: list[str] = []
    for category in sorted(categories, key=str.lower):
        lines += section(category, 3)
        for r in categories[category]:
            lines += [f"#### {r['id']}: {cell(r.get('title'))}", ""]
            lines += [f"- **Priority:** {cell(r.get('priority'))}  **Status:** {cell(r.get('status'))}", ""]
            if r.get("description"):
                lines += [" ".join(str(r["description"]).split()), ""]
            acceptance = [str(a) for a in r.get("acceptance_criteria") or [] if str(a).strip()]
            if acceptance:
                lines += ["Acceptance criteria:", "", *[f"{i}. {cell(a)}" for i, a in enumerate(acceptance, 1)], ""]
            else:
                lines += ["Acceptance criteria: _none recorded._", ""]
            validation = [str(v) for v in r.get("validation_required") or [] if str(v).strip()]
            if validation:
                lines += ["Validation: " + "; ".join(code(v) for v in validation), ""]
    return lines


def render_traceability(model: Model) -> str:
    lines = header(model, "requirements-traceability.md", "Requirements Traceability Matrix")
    reqs = active_requirements(model)
    if not reqs:
        return finish(lines + [not_derived("the requirement registry is empty."), ""])
    lines += [
        "Tests are linked by data, not by name matching: test paths in the requirement's scope, test files changed by "
        "the commits that deliver it, regression tests of its ledger failures, and test modules the code graph says "
        "verify a module in its scope.",
        "",
        "| Requirement | Status | Files in scope | Tests | Failures |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in reqs:
        scope = [str(s) for s in r.get("minimum_access_scope") or []]
        scope_text = ", ".join(scope[:8]) + (f", +{len(scope) - 8} more" if len(scope) > 8 else "")
        tests = linked_tests(model, r)
        tests_text = ", ".join(tests[:6]) + (f", +{len(tests) - 6} more" if len(tests) > 6 else "")
        fails = failures_for(model, str(r["id"]))
        fails_text = ", ".join(f"{f['id']} ({f.get('status')})" for f in fails)
        lines.append(f"| {r['id']}: {cell(r.get('title'))} | {cell(r.get('status'))} | {cell(scope_text)} | {cell(tests_text)} | {cell(fails_text)} |")
    lines.append("")
    lines += section("Open failures")
    open_failures = [f for f in model.failures if f.get("status") == "open"]
    if open_failures:
        lines += ["| Failure | Severity | Requirement | Title | Affected |", "| --- | --- | --- | --- | --- |"]
        lines += [
            f"| {f['id']} | {cell(f.get('severity'))} | {cell(f.get('requirement'))} | {cell(f.get('title'))} | {cell(', '.join(map(str, f.get('affected') or [])))} |"
            for f in open_failures
        ]
    else:
        lines.append(f"None open ({len(model.failures)} recorded).")
    return finish(lines)


def render_test_plan(model: Model) -> str:
    lines = header(model, "test-plan.md", "Test Plan and Coverage Summary")
    reqs = [r for r in active_requirements(model) if r.get("status") != "withdrawn"]
    lines += section("1. Test suites")
    if model.suites:
        lines += ["| Suite | Kind | Framework | Command | Paths | Covers |", "| --- | --- | --- | --- | --- | --- |"]
        for s in model.suites:
            lines.append(
                f"| {cell(s.get('id'))} | {cell(s.get('kind'))} | {cell(s.get('framework'))} | {code(s.get('command', '-'))} | "
                f"{cell(', '.join(map(str, s.get('paths') or [])))} | {cell(', '.join(map(str, s.get('covers') or [])))} |"
            )
        lines.append("")
    else:
        lines += [not_derived("no suites registered in `.ai/test-suites.json` (`omni test detect --write` registers them)."), ""]
    tests = [m for m in model.modules if m.test]
    lines += section("2. Test modules")
    if tests:
        verifies: dict[str, set[str]] = {}
        for edge in model.edges:
            if edge.get("type") == "verifies":
                src = model.nodes.get(str(edge.get("source")))
                target = module_of_node(model, str(edge.get("target")))
                if src and src.get("kind") == "module" and target:
                    verifies.setdefault(str(src.get("file")), set()).add(target)
        lines += [f"{len(tests)} test module(s) in the code graph.", "", "| Test module | Verifies (per the graph) |", "| --- | --- |"]
        for m in sorted(tests, key=lambda x: x.path):
            lines.append(f"| {code(m.path)} | {cell(', '.join(sorted(verifies.get(m.path, set()))))} |")
        lines.append("")
    else:
        lines += [not_derived("the code graph holds no test modules."), ""]
    lines += section("3. Validation required by requirements")
    with_validation = [r for r in reqs if any(str(v).strip() for v in r.get("validation_required") or [])]
    if with_validation:
        lines += ["| Requirement | Status | Validation |", "| --- | --- | --- |"]
        lines += [f"| {r['id']} | {cell(r.get('status'))} | {cell('; '.join(map(str, r.get('validation_required') or [])))} |" for r in with_validation]
        lines.append("")
    else:
        lines += [not_derived("no requirement records `validation_required`."), ""]
    lines += section("4. Requirements with no linked test")
    untested = [r for r in reqs if not linked_tests(model, r)]
    if untested:
        lines += [f"{len(untested)} of {len(reqs)} requirement(s) have no test linked by scope, commits, ledger or graph:", ""]
        lines += [f"- {r['id']} ({r.get('status')}): {cell(r.get('title'))}" for r in untested]
        lines.append("")
    elif reqs:
        lines += [f"Every one of the {len(reqs)} requirement(s) has at least one linked test.", ""]
    else:
        lines += [not_derived("the requirement registry is empty."), ""]
    lines += section("5. Regression tests from the failure ledger")
    guarded = [f for f in model.failures if f.get("regression_tests")]
    unguarded = [f for f in model.failures if not f.get("regression_tests")]
    if model.failures:
        lines += [f"{len(guarded)} of {len(model.failures)} recorded failure(s) name a regression test."]
        if unguarded:
            lines += ["", "Without one: " + ", ".join(f"{f['id']}" + (f" ({cell(f.get('no_test_reason'))})" if f.get("no_test_reason") else "") for f in unguarded) + "."]
        lines.append("")
    else:
        lines += [not_derived("the failure ledger is empty."), ""]
    lines += section("6. Test results")
    lines += [not_derived("omni keeps no stored test-run results; run `omni test run` or `omni gate` for a current verdict."), ""]
    return finish(lines)


def render_changes(model: Model) -> str:
    lines = header(model, "CHANGES-SUMMARY.md", "Changes Summary")
    reqs = active_requirements(model)
    if not reqs and not model.failures:
        return finish(lines + [not_derived("no requirements and no failures are recorded."), ""])
    registry_order = [r for r in model.requirements if not r["_archived"]]
    completed = [r for r in registry_order if r.get("status") == "completed"][-MAX_RECENT:]
    groups = [
        ("Recently completed", list(reversed(completed)), f"the last {MAX_RECENT} completed, newest registry entry first"),
        ("In progress", [r for r in reqs if r.get("status") in ("pending", "needs_review")], ""),
        ("Blocked", [r for r in reqs if r.get("status") == "blocked"], ""),
        ("Proposed", [r for r in reqs if r.get("status") == "proposed"], ""),
    ]
    for title, items, note in groups:
        lines += section(title)
        if note:
            lines += [f"_{note.capitalize()}._", ""]
        lines += [f"- {r['id']}: {cell(r.get('title'))}" + (f" ({r.get('status')})" if title == "In progress" else "") for r in items] or ["None."]
        lines.append("")
    lines += section("Open failures")
    open_failures = [f for f in model.failures if f.get("status") == "open"]
    lines += [f"- {f['id']} ({cell(f.get('severity'))}, {cell(f.get('requirement'))}): {cell(f.get('title'))}" for f in open_failures] or [f"None ({len(model.failures)} recorded)."]
    lines.append("")
    lines += section("Required documentation not touched")
    if not model.has_history:
        lines += [not_derived("the code graph has no history layer (no git history, or built with `--layers` excluding history)."), ""]
        return finish(lines)
    rows: list[str] = []
    no_commits: list[str] = []
    prose: list[str] = []
    for r in reqs:
        docs = [str(d) for d in r.get("documentation_required") or [] if str(d).strip()]
        if not docs or r.get("status") == "withdrawn":
            continue
        commits, files = commit_files_for(model, str(r["id"]))
        if not commits:
            no_commits.append(str(r["id"]))
            continue
        for d in docs:
            path = resolve_doc(model, d)
            if path is None:
                prose.append(f"{r['id']}: {d}")
            elif not any(f == path or f.startswith(path.rstrip("/") + "/") for f in files):
                rows.append(f"| {r['id']} | {cell(r.get('status'))} | {code(path)} | {commits} |")
    if rows:
        lines += ["Documents a requirement lists in `documentation_required` that none of its commits changed:", "", "| Requirement | Status | Document | Commits checked |", "| --- | --- | --- | --- |", *rows, ""]
    else:
        lines += ["Every resolvable required document was changed by at least one of its requirement's commits.", ""]
    if no_commits:
        lines += [f"Not checked, no commit names them in the scanned history: {', '.join(no_commits)}.", ""]
    if prose:
        lines += [f"Not checked, entry is not a file path: {'; '.join(cell(p) for p in prose[:20])}" + (f"; and {len(prose) - 20} more" if len(prose) > 20 else "") + ".", ""]
    return finish(lines)


def render_index(model: Model) -> str:
    lines = header(model, "index.md", f"{model.project}: generated engineering documentation")
    lines += [
        f"{SHA_LINE_PREFIX}`{model.sha}`." if model.sha else f"{SHA_LINE_PREFIX}(not a git checkout).",
        "",
        "Regenerate with `omni docs build`; `omni requirement add|update|complete` regenerate it automatically. "
        "Nothing here is written by a model: every line comes from the code graph, the requirement registry, the "
        "failure ledger, the test-suite registry, git history and source docstrings.",
        "",
        "| Document | What it covers |",
        "| --- | --- |",
    ]
    lines += [f"| [{title}]({name}) | {desc} |" for name, title, desc in DOCUMENTS]
    lines += ["", *section("Inputs")]
    code_count = len([m for m in model.modules if not m.test])
    lines += [
        f"- Code graph: {code_count} source module(s), {len([m for m in model.modules if m.test])} test module(s)"
        + (f", built at commit `{model.graph_head[:12]}`" if model.graph_head else "") + ".",
        f"- Requirements: {len([r for r in model.requirements if not r['_archived']])} active, {len([r for r in model.requirements if r['_archived']])} archived.",
        f"- Failure ledger: {len(model.failures)} entr{'y' if len(model.failures) == 1 else 'ies'}.",
        f"- Test suites: {len(model.suites)} registered.",
    ]
    if model.sha and model.graph_head and model.graph_head != model.sha:
        lines.append("- The code graph was built at an older commit than HEAD; run `omni graph build` (or `omni docs build`, which rebuilds a stale graph) for current code views.")
    for note in model.notes:
        lines.append(f"- Note: {note}")
    return finish(lines)


RENDERERS = {
    "index.md": render_index,
    "architecture.md": render_architecture,
    "design.md": render_design,
    "requirements.md": render_requirements,
    "requirements-traceability.md": render_traceability,
    "test-plan.md": render_test_plan,
    "modules.md": render_modules,
    "class-diagram.md": render_class_diagram,
    "CHANGES-SUMMARY.md": render_changes,
}


# --------------------------------------------------------------------------
# Build and check
# --------------------------------------------------------------------------


def render_all(root: Path, out_dir: Path, graph_path: Path | None = None) -> dict[str, str]:
    model = load_model(root, graph_path, out_dir)
    return {name: RENDERERS[name](model) for name in sorted(RENDERERS)}


def _comparable(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith(SHA_LINE_PREFIX))


def build_docs(root: Path, out_dir: Path, graph_path: Path | None = None) -> dict[str, list[str]]:
    """Write every document whose bytes changed; returns {"written": [...], "unchanged": [...]}."""
    docs = render_all(root, out_dir, graph_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, list[str]] = {"written": [], "unchanged": []}
    for name, text in docs.items():
        target = out_dir / name
        data = text.encode("utf-8")
        try:
            current = target.read_bytes()
        except OSError:
            current = None
        if current == data:
            result["unchanged"].append(name)
            continue
        target.write_bytes(data)  # bytes, so Windows never writes CRLF
        result["written"].append(name)
    return result


def check_docs(root: Path, out_dir: Path, graph_path: Path | None = None) -> list[str]:
    """Names of documents that are missing or differ from a fresh render (the commit-SHA line is ignored)."""
    stale = []
    for name, text in render_all(root, out_dir, graph_path).items():
        current = _read_text(out_dir / name)
        if current is None or _comparable(current.replace("\r\n", "\n")) != _comparable(text):
            stale.append(name)
    return stale
