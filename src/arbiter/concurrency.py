"""Shared state touched by concurrent workers without a lock (ARB-053, ARB-054).

## Why this is its own probe

The most common defect in model-drafted worker code is not a wrong algorithm,
it is a race: a queue that checks it has an item and then takes it, a counter
bumped from several threads. The code reads correctly, the single-threaded test
passes, and two workers eventually take the same task. None of the wrapped
analyzers look for it.

## What is checked (Python only)

1. CHECK-THEN-ACT WITHOUT A LOCK (`concurrency.check-then-act-unlocked`).
   An `if` whose test reads a shared collection and whose body changes that
   same collection, with no lock held: `if self.tasks: return self.tasks.pop(0)`,
   `if key not in CACHE: CACHE[key] = ...`, or `job = JOBS[id]` followed by
   `if job["state"] == "queued": job["state"] = "running"`. Shared means an
   attribute of `self` or a collection bound at module level; a local variable
   is not shared. Reported only in a file that starts threads or uses an
   executor (sync code), or in an `async def` when an `await` sits between the
   check and the change -- without one, asyncio cannot interleave there. In a
   file that also runs an event loop (asyncio, anyio, trio), a plain function
   is reported only when the file starts it as a thread.

2. UNSYNCHRONISED COUNTER IN A WORKER (`concurrency.unsynchronised-counter`).
   An augmented assignment (`+=`, `-=`, ...) to an attribute of `self` or a
   `global`, inside a function the same file starts as a thread or hands to an
   executor, with no lock held. `x += 1` is a read, an add and a write; two
   threads interleave between them and lose updates.

"No lock held" means the statement is not inside a `with` block whose context
is a lock, and the function does not call `.acquire()`. A lock is a name the
file binds to `Lock()`, `RLock()`, `Condition()`, `Semaphore()` or
`BoundedSemaphore()`, or any name containing lock, mutex, cond or sem.

## What this is not

Not a race detector. It cannot see a lock taken by the caller, and it does not
follow calls into other functions or files. It is tuned to the shapes that
model-drafted worker code actually produces, so it reports at medium
confidence and never gates on its own.
"""
from __future__ import annotations

import ast
import re

from .core import Finding, Location
from .probes import ProbeContext, _read

_ASYNC_RUNTIMES = {"asyncio", "anyio", "trio", "curio"}
_CONCURRENT_MODULES = {"threading", "concurrent", "concurrent.futures", "multiprocessing",
                       "multiprocessing.dummy", "_thread"}
_LOCK_FACTORIES = {"Lock", "RLock", "Condition", "Semaphore", "BoundedSemaphore"}
_LOCKISH = re.compile(r"lock|mutex|cond|sem", re.IGNORECASE)
# Methods that change a list, dict, set or deque in place.
_MUTATORS = {"pop", "popleft", "popitem", "remove", "append", "appendleft", "add",
             "discard", "clear", "insert", "extend", "update", "setdefault"}
_COLLECTION_CALLS = {"dict", "list", "set", "deque", "defaultdict", "OrderedDict", "Counter"}
_SUBMITTERS = {"submit", "map", "run_in_executor", "apply_async", "map_async", "imap",
               "imap_unordered", "start_new_thread"}


def _dotted(node: ast.AST) -> str | None:
    """`self.tasks` -> "self.tasks", `JOBS` -> "JOBS"; anything else -> None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _call_name(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


class _FileFacts:
    """What one module tells us about its own concurrency."""

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.threaded = False
        self.async_runtime = False
        self.locks: set[str] = set()          # dotted names bound to a lock
        self.shared_globals: set[str] = set()  # module-level collections
        self.worker_names: set[str] = set()    # function or method names started as workers
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""])
                roots = {n.split(".")[0] for n in names}
                if roots & {m.split(".")[0] for m in _CONCURRENT_MODULES}:
                    self.threaded = True
                if roots & _ASYNC_RUNTIMES:
                    self.async_runtime = True
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if isinstance(value, ast.Call) and _call_name(value) in _LOCK_FACTORIES:
                    for t in targets:
                        name = _dotted(t)
                        if name:
                            self.locks.add(name)
            elif isinstance(node, ast.Call):
                self._note_worker(node)
            elif isinstance(node, ast.ClassDef) and any(
                    _dotted(b) in ("Thread", "threading.Thread") for b in node.bases):
                self.worker_names.add("run")
                self.threaded = True
        for node in tree.body:
            if (isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
                    and _is_collection(node.value)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        self.shared_globals.add(t.id)

    def _note_worker(self, call: ast.Call) -> None:
        name = _call_name(call)
        target: ast.AST | None = None
        if name in ("Thread", "Timer", "Process"):
            for kw in call.keywords:
                if kw.arg in ("target", "function"):
                    target = kw.value
            if target is None and name == "Timer" and len(call.args) >= 2:
                target = call.args[1]
        elif name in _SUBMITTERS and call.args:
            # run_in_executor(executor, fn, ...) takes the function second.
            target = call.args[1] if name == "run_in_executor" and len(call.args) > 1 else call.args[0]
        if target is None:
            return
        dotted = _dotted(target)
        if dotted:
            self.worker_names.add(dotted.split(".")[-1])

    def is_lock(self, expr: ast.AST) -> bool:
        if isinstance(expr, ast.Call):  # `with lock_for(key):` / `with self._lock.acquire_timeout():`
            expr = expr.func
        name = _dotted(expr)
        if name is None:
            return False
        if name in self.locks:
            return True
        return bool(_LOCKISH.search(name.split(".")[-1]))


def _is_collection(value: ast.AST) -> bool:
    if isinstance(value, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    return isinstance(value, ast.Call) and _call_name(value) in _COLLECTION_CALLS


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    out: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def _held_lock(node: ast.AST, parents: dict, facts: _FileFacts, func: ast.AST) -> bool:
    cur = parents.get(node)
    while cur is not None and cur is not func:
        if isinstance(cur, (ast.With, ast.AsyncWith)) and any(
                facts.is_lock(item.context_expr) for item in cur.items):
            return True
        cur = parents.get(cur)
    for n in ast.walk(func):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "acquire":
            return True
    return False


def _shared_in(func: ast.AST, facts: _FileFacts) -> tuple[set[str], dict[str, str]]:
    """Shared names visible in `func`, and local aliases of shared items."""
    params = {a.arg for a in getattr(func.args, "args", [])}
    shared = {g for g in facts.shared_globals if g not in params}
    aliases: dict[str, str] = {}
    for n in ast.walk(func):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            v = n.value
            src = None
            if isinstance(v, ast.Subscript):
                src = _dotted(v.value)
            elif isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr == "get":
                src = _dotted(v.func.value)
            if src and _is_shared(src, shared):
                aliases[n.targets[0].id] = src
    return shared, aliases


def _is_shared(name: str, shared: set[str]) -> bool:
    return name.startswith("self.") or name in shared


def _reads(test: ast.AST) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(test):
        d = _dotted(n) if isinstance(n, (ast.Name, ast.Attribute)) else None
        if d:
            out.add(d)
    return out


def _mutation_of(stmt: ast.AST) -> list[tuple[str, ast.AST]]:
    """(dotted target, node) for every in-place change inside `stmt`."""
    out: list[tuple[str, ast.AST]] = []
    for n in ast.walk(stmt):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in _MUTATORS:
            d = _dotted(n.func.value)
            if d:
                out.append((d, n))
        elif isinstance(n, (ast.Assign, ast.AugAssign, ast.Delete)):
            targets = n.targets if isinstance(n, (ast.Assign, ast.Delete)) else [n.target]
            for t in targets:
                if isinstance(t, ast.Subscript):
                    d = _dotted(t.value)
                    if d:
                        out.append((d, n))
    return out


def _await_between(func: ast.AST, start: int, end: int) -> bool:
    return any(isinstance(n, ast.Await) and start <= n.lineno <= end for n in ast.walk(func))


def _functions(tree: ast.AST):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield n


def _check_then_act(f, facts: _FileFacts, parents: dict) -> list[Finding]:
    out: list[Finding] = []
    for func in _functions(facts.tree):
        is_async = isinstance(func, ast.AsyncFunctionDef)
        # A plain function runs on threads when the file starts threads -- unless
        # the file is built on an event loop, where a plain function runs on the
        # loop's one thread and only a function started as a thread is at risk.
        if not is_async and not (facts.threaded and (not facts.async_runtime
                                                     or func.name in facts.worker_names)):
            continue
        shared, aliases = _shared_in(func, facts)
        for node in ast.walk(func):
            if not isinstance(node, ast.If):
                continue
            reads = _reads(node.test)
            for target, mut in (m for stmt in node.body for m in _mutation_of(stmt)):
                origin = aliases.get(target, target)
                if not _is_shared(origin, shared):
                    continue
                if target not in reads and origin not in reads:
                    continue
                if is_async and not _await_between(func, node.lineno, mut.lineno):
                    continue
                if _held_lock(node, parents, facts, func):
                    continue
                out.append(Finding(
                    rule_id="arbiter/concurrency.check-then-act-unlocked",
                    title=f"`{func.name}` checks `{origin}` and then changes it without a lock",
                    dimension="quality", severity="medium", confidence="medium",
                    repo_id=f.repo_id, probe="concurrency",
                    location=Location(path=f.path, start_line=node.lineno, repo_id=f.repo_id),
                    description=(
                        f"Line {node.lineno} tests `{origin}` and line {mut.lineno} changes it, "
                        "with no lock held between them. Another worker can change it in "
                        "between: two workers take the same item, or one pops from an empty "
                        "collection."
                    ),
                    remediation=("Hold one lock across the check and the change, or use an "
                                 "operation that does both at once (queue.Queue.get, dict.setdefault, "
                                 "a database UPDATE ... WHERE state = 'queued')."),
                    evidence=f"check-then-act:{func.name}:{origin}",
                    controls=["NIST-800-218:PW.5"],
                    tags=["concurrency", "race"],
                ))
                break  # one finding per if
    return out


def _counters(f, facts: _FileFacts, parents: dict) -> list[Finding]:
    out: list[Finding] = []
    for func in _functions(facts.tree):
        if func.name not in facts.worker_names or isinstance(func, ast.AsyncFunctionDef):
            # asyncio runs one coroutine at a time; `x += 1` with no await
            # inside it cannot be interleaved, so it is not this defect.
            continue
        declared_global = {name for n in ast.walk(func) if isinstance(n, ast.Global) for name in n.names}
        for node in ast.walk(func):
            if not isinstance(node, ast.AugAssign):
                continue
            target = _dotted(node.target)
            if not target or not (target.startswith("self.") or target in declared_global):
                continue
            if _held_lock(node, parents, facts, func):
                continue
            out.append(Finding(
                rule_id="arbiter/concurrency.unsynchronised-counter",
                title=f"`{target}` is updated from worker `{func.name}` without a lock",
                dimension="quality", severity="medium", confidence="medium",
                repo_id=f.repo_id, probe="concurrency",
                location=Location(path=f.path, start_line=node.lineno, repo_id=f.repo_id),
                description=(
                    f"`{func.name}` runs as a thread or executor job, and `{target}` "
                    f"{type(node.op).__name__.lower()}-assigns shared state on line {node.lineno}. "
                    "That is a read, an update and a write; two workers interleave between "
                    "them and updates are lost."
                ),
                remediation=("Take a lock around the update, use itertools.count() for an id "
                             "counter, or have workers report through a queue.Queue."),
                evidence=f"counter:{func.name}:{target}",
                controls=["NIST-800-218:PW.5"],
                tags=["concurrency", "race"],
            ))
    return out


def probe_concurrency(ctx: ProbeContext) -> list[Finding]:
    out: list[Finding] = []
    for f in ctx.inventory.text_files():
        if f.language != "python" or f.role in ("test", "docs", "generated", "data"):
            continue
        text = _read(f)
        if not text:
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        facts = _FileFacts(tree)
        parents = _parents(tree)
        out.extend(_check_then_act(f, facts, parents))
        out.extend(_counters(f, facts, parents))
    return out


def register_concurrency() -> None:
    from .probes import Probe, register
    register(Probe(
        name="concurrency",
        # Every finding depends only on the file it is in.
        scope="file",
        dimensions=["quality"],
        checks=2,
        stacks=["python"],
        run=probe_concurrency,
        cacheable=True,
    ))
