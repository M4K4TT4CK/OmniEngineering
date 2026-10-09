"""Persistent per-file result cache for file-local probes.

## Why this exists

Reading was never the bottleneck -- the in-process read cache in probes.py
proved that -- but analysis is, and every scan paid for all of it again.
The pre-commit gate runs over a tree in which, between one commit and the
next, a handful of files changed. A probe whose answer for a file depends on
that file alone has nothing new to say about the other ten thousand.

## What is cached

One entry per (probe, file): the list of findings that probe produced for
that file, exactly as the probe produced them, BEFORE calibration, severity
overrides, the baseline and suppressions. Those passes run on everything on
every scan, so a verdict recorded yesterday, or a suppression added this
morning, applies to a cached finding the same way it applies to a fresh one.
An empty list is stored too: a file the probe found clean is a hit next time,
which is the common case and the one worth having.

## What the key covers

    sha256( digest(file bytes) ‖ rules hash ‖ repo id ‖ relative path )

where the rules hash is

    sha256( config as sorted JSON ‖ probe.version ‖ Arbiter's version
            ‖ a digest of Arbiter's own source ‖ the probe's context digest )

The config, because a house rule or a threshold is part of the question. The
probe and Arbiter versions, because the question changes between releases.
The source digest, because a developer editing a probe does not bump its
version before rerunning the scan, and a cache that served yesterday's answer
to today's rule would be worse than no cache. The repository id and the path,
because two identical files at different paths are two different findings
and because probes read the path (a credential under `tests/` is reported
differently from one under `src/`). The context digest is whatever a probe
declares its per-file answer depends on OUTSIDE the file -- the manifests,
for a probe that decides whether an import is declared. A probe that depends
on nothing outside the file declares nothing.

## What is never cached

A probe that is not declared `cacheable`. The declaration is a claim that
the probe is file-local, and `tools/integrity.py` (CI-13) tests the claim
by running every cacheable probe on a file alone and beside another file,
and requiring the same answer. Findings that carry no path cannot be
attributed to a file and are never stored. The output of any post-processing
pass is never stored. And CI runs with `--no-cache`: a pull-request gate must
measure the tree in front of it, not remember a previous one.

## The adapter memo

An external analyzer is not file-local -- none has been measured to be (see
`SCOPE_REASON` in adapters.py) -- so it cannot be cached per file. It can be
memoised whole (ARB-047): one entry per adapter, keyed on

    sha256( adapter name ‖ tool version ‖ rules hash
            ‖ digest of every inventory file's (path, bytes digest)
            ‖ digest of the analyzer configuration at each repository root )

replays the adapter's findings when nothing it could have read has changed.
All-or-nothing by design: one changed byte in one inventory file re-runs the
whole adapter, because the tool reads the tree as a whole and nobody has
shown which files its answer for another file depends on. The configuration
files are the ones the shipped analyzers read from the root
(`ADAPTER_CONFIG_FILES`); they are hashed whether or not the inventory kept
them. `--verify-cache` re-runs one memoised adapter per scan and reports a
divergence exactly as it does for a file entry. `--no-cache` disables it.

A missing or corrupt cache file is an empty cache, never an error; a tree
that cannot be written to simply gets no cache. The file is written whole,
to a temporary name and then renamed, so a reader never sees half of one.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__ as ARBITER_VERSION
from .core import Finding, Location

if TYPE_CHECKING:  # pragma: no cover
    from .inventory import FileInfo, Inventory
    from .probes import Probe, ProbeContext

SCHEMA_VERSION = 1
MAX_KEYS = 50_000
DEFAULT_RELATIVE_PATH = Path(".arbiter") / "cache.json"
DIVERGENCE_RULE = "arbiter/assurance.cache-divergence"

_SEP = "\x1f"


def _sha(*parts: str) -> str:
    return hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()




def file_digest(abspath: str) -> str:
    """sha256 of the file's bytes; empty when the file cannot be read.

    Read from disk every time, never memoised on (path, mtime, size): a
    same-size edit inside one mtime tick is invisible to that key, and on
    Windows the tick is coarse enough that the test suite hit it (FAIL-041).
    The bytes are what the cache promises to key on, so the bytes are read.
    """
    try:
        return hashlib.sha256(Path(abspath).read_bytes()).hexdigest()
    except OSError:
        return ""


_CODE_DIGEST: dict[str, str] = {}


def code_digest() -> str:
    """A digest of Arbiter's own source and rule packs, computed once per
    process. Editing a probe invalidates its entries without anybody having
    to remember to bump a version."""
    if "value" in _CODE_DIGEST:
        return _CODE_DIGEST["value"]
    h = hashlib.sha256()
    pkg = Path(__file__).resolve().parent
    for p in sorted(pkg.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        if p.suffix not in (".py", ".yaml", ".yml", ".json", ".toml"):
            continue
        try:
            h.update(str(p.relative_to(pkg)).replace(os.sep, "/").encode("utf-8"))
            h.update(p.read_bytes())
        except OSError:
            continue
    _CODE_DIGEST["value"] = h.hexdigest()
    return _CODE_DIGEST["value"]


def rules_hash(config: dict, probe: Any, context: str = "") -> str:
    """Everything that decides a probe's answer apart from the file itself."""
    cfg = json.dumps(config or {}, sort_keys=True, default=str)
    return _sha(cfg, getattr(probe, "version", ""), ARBITER_VERSION, code_digest(), context)


def key(abspath: str, rules: str, path: str = "", repo_id: str = "") -> str:
    """One file under one set of rules, at one place in one repository."""
    return _sha(file_digest(abspath), rules, repo_id, path)


class ResultCache:
    def __init__(self, path: Path | None = None) -> None:
        self.path: Path | None = Path(path) if path else None
        self.entries: dict[str, dict[str, list[dict]]] = {}
        self.hits = 0
        self.misses = 0
        self.verified = 0
        self.divergent = 0
        self.loaded_from_disk = False

    # -- persistence --------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> ResultCache:
        """A missing or corrupt file is an empty cache, never an error."""
        cache = cls(path)
        try:
            raw = Path(path).read_text(encoding="utf-8")
            doc = json.loads(raw)
        except (OSError, ValueError):
            return cache
        if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
            return cache
        if doc.get("arbiter_version") != ARBITER_VERSION:
            # Every key would miss anyway (the version is in the rules hash);
            # dropping the entries keeps a release from carrying dead weight.
            return cache
        entries = doc.get("entries")
        if not isinstance(entries, dict):
            return cache
        for probe, by_key in entries.items():
            if not isinstance(probe, str) or not isinstance(by_key, dict):
                continue
            good = {k: v for k, v in by_key.items()
                    if isinstance(k, str) and isinstance(v, list)
                    and all(isinstance(d, dict) for d in v)}
            if good:
                cache.entries[probe] = good
        cache.loaded_from_disk = True
        return cache

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "arbiter_version": ARBITER_VERSION,
            "entries": self.entries,
        }

    def _evict(self) -> None:
        """Drop the oldest entries once the cache passes MAX_KEYS. Hits are
        re-inserted on access, so insertion order is recency order."""
        total = sum(len(v) for v in self.entries.values())
        while total > MAX_KEYS:
            for by_key in self.entries.values():
                if by_key and total > MAX_KEYS:
                    by_key.pop(next(iter(by_key)))
                    total -= 1

    def save(self) -> bool:
        """Write the whole file to a temporary name, then rename it into
        place. A reader sees the old file or the new one, never a torn one.
        Returns False, and raises nothing, when the tree is not writable."""
        if self.path is None:
            return False
        self._evict()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".cache-", suffix=".tmp", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                    json.dump(self.to_dict(), fh, sort_keys=True, separators=(",", ":"))
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        except OSError:
            return False
        return True

    # -- entries ------------------------------------------------------------

    def get(self, probe_name: str, k: str) -> list[dict] | None:
        by_key = self.entries.get(probe_name)
        if by_key is None or k not in by_key:
            self.misses += 1
            return None
        self.hits += 1
        found = by_key.pop(k)      # re-insert so recency is insertion order
        by_key[k] = found
        return [dict(d) for d in found]

    def put(self, probe_name: str, k: str, findings: list[dict]) -> None:
        by_key = self.entries.setdefault(probe_name, {})
        by_key.pop(k, None)
        by_key[k] = [dict(d) for d in findings]

    def stats(self) -> dict:
        return {"hits": self.hits, "misses": self.misses, "verified": self.verified,
                "divergent": self.divergent}


# ---------------------------------------------------------------------------
# running a probe through the cache
# ---------------------------------------------------------------------------

def _is_context(f: FileInfo) -> bool:
    """A file kept in the narrowed inventory whether or not it missed, so a
    probe that reads a manifest to answer about a source file still can."""
    from .incremental import is_context
    if is_context(f):
        return True
    from .authored import _MANIFESTS
    return Path(f.path).name in _MANIFESTS


def _narrowed(inv: Inventory, keep: list[FileInfo]) -> Inventory:
    from .inventory import Inventory
    wanted = {id(f) for f in keep}
    files = [f for f in inv.files if id(f) in wanted or _is_context(f)]
    out = Inventory(files=files, stacks=set(inv.stacks), whole=inv.whole or inv)
    for f in files:
        out.by_repo.setdefault(f.repo_id, []).append(f)
    for rid in inv.by_repo:
        out.by_repo.setdefault(rid, [])
    return out


def _canonical(findings: list[dict]) -> str:
    return json.dumps(sorted(findings, key=lambda d: json.dumps(d, sort_keys=True, default=str)),
                      sort_keys=True, default=str)


def _divergence(probe_name: str, f: FileInfo, cached: list[dict], fresh: list[dict]) -> Finding:
    return Finding(
        rule_id=DIVERGENCE_RULE,
        title=f"Cached result for `{f.path}` diverged from a fresh run of `{probe_name}`",
        dimension="assurance", severity="high", confidence="high",
        repo_id=f.repo_id, probe=probe_name,
        location=Location(path=f.path, repo_id=f.repo_id),
        description=(f"The result cache held {len(cached)} finding(s) for this file under "
                     f"probe `{probe_name}`; re-running the probe produced {len(fresh)}. "
                     "A cacheable probe must depend on the file alone, so a divergence means "
                     "either the probe reads something its cache key does not cover, or the "
                     "cache file was altered. The entry has been replaced with the fresh result."),
        remediation="Run with --no-cache and compare; if the probe is not file-local, remove "
                    "its `cacheable` declaration (tools/integrity.py CI-13 should have caught it).",
        evidence=f"{probe_name}:cached={len(cached)},fresh={len(fresh)}",
        tags=["cache", "assurance"],
    )


def run_with_cache(
    probe: Probe,
    ctx: ProbeContext,
    cache: ResultCache,
    rules: str,
    verify_fraction: float = 0.0,
    rng: random.Random | None = None,
) -> tuple[list[Finding], dict]:
    """Run `probe` over the files the cache cannot answer for, and assemble
    the rest from the cache.

    Returns the findings for every text file in the inventory -- cached and
    fresh, indistinguishable downstream -- plus a note of how many of each.
    With `verify_fraction` > 0, that fraction of the hit files (at least one)
    is re-run anyway and compared; a divergence becomes a finding and the
    stale entry is replaced.
    """
    inv = ctx.inventory
    files = inv.text_files()
    keys: dict[int, str] = {}
    hits: list[tuple[FileInfo, list[dict]]] = []
    misses: list[FileInfo] = []
    for f in files:
        k = key(f.abspath, rules, f.path, f.repo_id)
        keys[id(f)] = k
        cached = cache.get(probe.name, k)
        if cached is None:
            misses.append(f)
        else:
            hits.append((f, cached))

    # Verification sample: hit files re-run alongside the misses, their
    # cached answer kept aside for comparison.
    sample: dict[int, list[dict]] = {}
    if verify_fraction > 0 and hits:
        n = max(1, round(len(hits) * verify_fraction))
        n = min(n, len(hits))
        picked = (rng or random.SystemRandom()).sample(range(len(hits)), n)
        for i in picked:
            f, cached = hits[i]
            sample[id(f)] = cached
    to_run = misses + [f for f, _ in hits if id(f) in sample]

    out: list[Finding] = []
    fresh_by_file: dict[int, list[dict]] = {id(f): [] for f in to_run}
    if to_run:
        narrowed = _narrowed(inv, to_run)
        ctx.inventory = narrowed
        try:
            produced = probe.run(ctx) or []
        finally:
            ctx.inventory = inv
        by_path = {(f.repo_id, f.path): id(f) for f in to_run}
        for finding in produced:
            fid = by_path.get((finding.repo_id, finding.location.path))
            if fid is None:
                # A context file that was a hit (its cached findings are
                # added below), or a finding with no path. The first is a
                # duplicate; the second cannot be attributed to a file and is
                # kept exactly once, uncached.
                if not finding.location.path:
                    out.append(finding)
                continue
            fresh_by_file[fid].append(finding.to_dict())
            out.append(finding)

    for f, cached in hits:
        if id(f) in sample:
            fresh = fresh_by_file.get(id(f), [])
            cache.verified += 1
            if _canonical(fresh) != _canonical(cached):
                cache.divergent += 1
                out.append(_divergence(probe.name, f, cached, fresh))
            continue
        out.extend(Finding.from_dict(d) for d in cached)

    for f in to_run:
        cache.put(probe.name, keys[id(f)], fresh_by_file[id(f)])

    note = {"hits": len(hits), "misses": len(misses),
            "verified": len(sample)}
    return out, note


def default_path(repo_root: str) -> Path:
    return Path(repo_root) / DEFAULT_RELATIVE_PATH


# ---------------------------------------------------------------------------
# the adapter memo (ARB-047)
# ---------------------------------------------------------------------------

# What the shipped analyzers read from the repository root besides the
# files: ruff, bandit, semgrep, checkov and gitleaks, in that order. A
# directory (`.semgrep/`) is hashed file by file.
ADAPTER_CONFIG_FILES = (
    "pyproject.toml", "ruff.toml", ".ruff.toml", "setup.cfg",
    ".bandit", "bandit.yaml",
    ".semgrep.yml", ".semgrep.yaml", ".semgrep",
    ".checkov.yaml", ".checkov.yml",
    ".gitleaks.toml",
)
REPLAY_REASON = "replayed from cache (nothing the tool reads changed)"
VERIFIED_REASON = "cache entry verified against a fresh run"
DIVERGED_REASON = "cache entry diverged from a fresh run; replaced"


def adapter_inputs_digest(inv: Inventory, repos: list) -> str:
    """Everything an external analyzer could have read: every inventory
    file's path and bytes, and the analyzer configuration at each root."""
    h = hashlib.sha256()
    for f in sorted(inv.files, key=lambda x: (x.repo_id, x.path)):
        h.update(f"{f.repo_id}:{f.path}:{file_digest(f.abspath)}\n".encode("utf-8"))
    for r in sorted(repos, key=lambda x: x.id):
        root = Path(r.path)
        for name in ADAPTER_CONFIG_FILES:
            p = root / name
            if p.is_dir():
                for q in sorted(x for x in p.rglob("*") if x.is_file()):
                    rel = str(q.relative_to(root)).replace(os.sep, "/")
                    h.update(f"{r.id}:config:{rel}:{file_digest(str(q))}\n".encode("utf-8"))
            elif p.is_file():
                h.update(f"{r.id}:config:{name}:{file_digest(str(p))}\n".encode("utf-8"))
    return h.hexdigest()


def adapter_key(name: str, version: str, rules: str, inputs: str) -> str:
    """One adapter, one tool build, one set of rules, one tree."""
    return _sha("adapter", name, version, rules, inputs)


def adapter_divergence(probe_name: str, cached: list[dict], fresh: list[dict]) -> Finding:
    repo_id = next((str(d.get("repo_id") or "") for d in cached + fresh), "")
    return Finding(
        rule_id=DIVERGENCE_RULE,
        title=f"Cached result for `{probe_name}` diverged from a fresh run",
        dimension="assurance", severity="high", confidence="high",
        repo_id=repo_id, probe=probe_name,
        location=Location(path="", repo_id=repo_id),
        description=(f"The result cache held {len(cached)} finding(s) for adapter "
                     f"`{probe_name}`; re-running it over the same tree produced {len(fresh)}. "
                     "The memo is keyed on every inventory file and on the analyzer "
                     "configuration at the root, so a divergence means the tool reads "
                     "something else -- a rule set fetched at run time, a file outside the "
                     "tree -- or the cache file was altered. The entry has been replaced "
                     "with the fresh result."),
        remediation="Run with --no-cache and compare; if the tool reads outside the tree, "
                    "its results cannot be memoised and `probes.disable` or --no-cache is "
                    "the honest setting.",
        evidence=f"{probe_name}:cached={len(cached)},fresh={len(fresh)}",
        tags=["cache", "assurance"],
    )


def make_context_digest(names: set[str]) -> Callable[[ProbeContext], str]:
    """A context-digest function for a probe whose per-file answer depends on
    the manifests named here and on which paths exist in the repository."""
    def digest(ctx: ProbeContext) -> str:
        inv = ctx.inventory
        whole = getattr(inv, "whole", None) or inv
        h = hashlib.sha256()
        for f in sorted(whole.files, key=lambda x: (x.repo_id, x.path)):
            h.update(f"{f.repo_id}:{f.path}\n".encode("utf-8"))
            if Path(f.path).name in names and not f.binary:
                h.update(file_digest(f.abspath).encode("utf-8"))
        return h.hexdigest()
    return digest
