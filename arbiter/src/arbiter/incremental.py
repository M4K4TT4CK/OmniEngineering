"""Scanning only what changed, and saying so.

## Why this exists

A full scan of Traefik — 452,000 lines — takes about two minutes. That is fine
once a night and useless on every pull request, where the answer is wanted
before the author has switched windows. The obvious fix is to make the scan
faster. It was tried: caching file reads bought twenty per cent, which was the
measurement that mattered, because it proved reading was never the bottleneck.
The analysis is. Optimising analysis that has to look at half a million lines
only ever moves the constant.

So look at less. A pull request that touches four Go files does not need the
other 2,289 read at all — and the checks that genuinely cannot answer from four
files are not made cheap by trying, they are made wrong.

## The rule that keeps this honest

Every probe declares a `scope`:

  * `"file"`  — every finding depends only on the file it was found in. A
                hardcoded secret is a secret whether or not the rest of the
                repository was read.
  * `"repo"`  — the answer depends on relationships between files. Whether a
                documented endpoint still exists, whether a dependency is used
                anywhere, whether the OpenAPI spec matches the routes: none of
                these can be answered from a subset, and a subset-based answer
                is not a weaker answer, it is a false one.

  * `"change"` — the probe reads a small, fixed set of files it names itself
                (the failure ledger, the requirement registry) and the set of
                changed paths. A partial scan is its natural habitat; a full
                scan is answered with the empty change. It runs in both modes.

In a partial scan, file-scoped and change-scoped probes run against the
selected files and repo-scoped probes are recorded as **skipped, with the
partial scan named as the reason**. They are not run against a subset and they are not quietly
dropped: they land in the coverage denominator as not-assessed, which is
exactly what they are.

This is why incremental scanning fits the coverage model rather than fighting
it. A faster scanner that answered the same questions from less evidence would
be a worse scanner pretending to be a better one. A scanner that answers fewer
questions and says which ones it skipped is the same scanner, run cheaply.

## Context files are always read

"Changed files only" is not quite right even for file-scoped probes. A probe
that decides whether an import is declared needs the manifest, and the manifest
is usually not in the diff. A Terraform rule that has to know whether a
resource is referenced by another needs the whole stack, not the one file the
author edited.

So the selection is: the changed files, plus every file cheap to parse and
needed for context — manifests, lockfiles, infrastructure, CI and config.
Those are a small fraction of a large repository's lines (in Traefik, under
four per cent) and omitting them would trade speed for false positives, which
is the worst trade available.

## What a partial scan may not claim

A partial scan carries an abstention naming the files it did not read. That
abstention flows into the gate claim, the grade, and every dimension, so:

  * a passing gate from a partial scan is scoped PARTIAL, never COMPLETE;
  * "this probe ran and found nothing" becomes "found nothing in the files it
    was given", which is a different and true statement;
  * the grade is withheld exactly as it would be for any other coverage loss.

A green partial scan therefore cannot be quoted as a green repository, which is
the whole point.

## Which requirement a finding belongs to

The commits since the base cite requirement ids ("(REQ-038)" in a subject).
One `git log --name-only` over the range gives, per path, the ids cited by
the commits that touched it (`requirement_ids_by_path`), and
`attribute_requirements` tags every finding in the change with `req:<ID>`
for exactly the ids its own file's commits cite, marked `req-scope:commits`.
A file no commit cites falls back to every id the range cites, marked
`req-scope:open` so a reader knows those tags are context rather than
attribution; a full scan has no range and carries only that marker
(ARB-052). The prefix comes from the registry's `requirement_id_prefix` when
the repository keeps one, and an old id in `id_aliases` resolves to the
current one.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from .inventory import FileInfo, Inventory

# Files always kept in a partial scan regardless of whether they changed.
# These are what the file-scoped probes need in order not to lie: a manifest
# they did not read looks exactly like a manifest that does not exist.
#
# The first version of this list was `role in {iac, ci, config}`, which sounds
# reasonable and is wrong: "config" is every .yaml and .json in the tree, and
# in Traefik that is 880 files and 55% of the lines. The scan got four times
# faster instead of twenty, for files no probe needed.
#
# What is actually required is narrower. A file-scoped probe by definition
# answers from the file it found something in, so an unchanged Kubernetes
# manifest is not context -- it is a different file with its own pre-existing
# findings, which this scan is not being asked about. The exceptions are the
# two places where a probe reasons about a file it is NOT reporting on:
#
#   * dependency manifests and lockfiles: whether an import is declared, or a
#     dependency pinned, is a question about a file the finding is not in;
#   * Terraform: resources reference variables, locals and modules defined in
#     sibling .tf files, so a lone changed .tf resolves to nothing.
#
# CI workflow files are kept because they are few and the supply-chain rules
# about unpinned actions read them as a set.
CONTEXT_ROLES = {"ci"}

CONTEXT_NAMES = {
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "pnpm-workspace.yaml", "lerna.json", "nx.json", "turbo.json",
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py",
    "setup.cfg", "Pipfile", "Pipfile.lock", "poetry.lock", "uv.lock",
    "go.mod", "go.sum", "go.work", "Gemfile", "Gemfile.lock",
    "composer.json", "composer.lock", "Cargo.toml", "Cargo.lock",
    "pom.xml", "build.gradle", "build.gradle.kts", "gradle.lockfile",
    "Dockerfile", "Containerfile", "docker-compose.yml", "docker-compose.yaml",
    "Makefile", "Jenkinsfile", "cdk.json",
}

CONTEXT_SUFFIXES = (".tf", ".tfvars", ".hcl")


def is_context(f: FileInfo) -> bool:
    """Cheap to read, and needed to answer honestly about the files that changed."""
    if f.binary:
        return False
    if f.role in CONTEXT_ROLES:
        return True
    name = os.path.basename(f.path)
    return name in CONTEXT_NAMES or name.startswith("Dockerfile") or \
        f.path.lower().endswith(CONTEXT_SUFFIXES)


def git_changed(repo_path: str, ref: str) -> tuple[set[str], str]:
    """Paths that differ from `ref`, including uncommitted work.

    Returns (paths, note). An empty note means the comparison succeeded. A
    non-empty note means it did not, and the caller must not treat the empty
    set as 'nothing changed' — see run_scan, which refuses to scan on a
    failed comparison rather than reporting a clean partial scan.
    """
    def run(*args: str) -> tuple[int, str]:
        try:
            r = subprocess.run(["git", "-C", repo_path, *args],
                               capture_output=True, text=True, timeout=120)
        except Exception as exc:  # noqa: BLE001 - no git, or a hung one: the caller treats both as a failed comparison
            return 1, str(exc)
        return r.returncode, r.stdout

    if not (Path(repo_path) / ".git").exists():
        return set(), f"{repo_path} is not a git repository"

    code, out = run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if code != 0 or not out.strip():
        return set(), f"ref '{ref}' is not a commit in this repository"

    paths: set[str] = set()
    # Three-dot: what this branch changed since it diverged, not what the
    # other branch did meanwhile. A file someone else touched on main is not
    # this pull request's problem and scanning it would blame the wrong author.
    code, out = run("diff", "--name-only", "--diff-filter=d", f"{ref}...HEAD")
    if code != 0:
        code, out = run("diff", "--name-only", "--diff-filter=d", ref)
        if code != 0:
            return set(), f"could not diff against '{ref}'"
    paths.update(p.strip() for p in out.split("\n") if p.strip())

    # Uncommitted work counts: a developer running this before committing
    # wants the answer about the code in front of them.
    for args in (("diff", "--name-only", "--diff-filter=d"),
                 ("diff", "--name-only", "--diff-filter=d", "--cached"),
                 ("ls-files", "--others", "--exclude-standard")):
        code, out = run(*args)
        if code == 0:
            paths.update(p.strip() for p in out.split("\n") if p.strip())
    return paths, ""


REGISTRY_PATH = ".ai/requirements/requirements.json"

# How a finding's `req:` tags were chosen (ARB-052). `commits`: from the
# commits since the base that touched the finding's own file, so the tags are
# attribution. `open`: no such commit cited a requirement, so the tags are the
# requirements the range cites as a whole (or nothing, in a full scan) --
# context a reader may use, never a claim about which requirement introduced
# the finding.
REQ_SCOPE_COMMITS = "req-scope:commits"
REQ_SCOPE_OPEN = "req-scope:open"

# An id as the registries write them: a prefix of capitals, a dash, a number.
_ID_TOKEN = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")

# Record and field separators for the one git log the attribution runs.
# Commit bodies are free text, so a line that looks like a path or a blank
# line that looks like the end of the message cannot be trusted; two control
# characters no commit message contains can.
_RS, _FS = "\x1e", "\x1f"


def requirement_registry(repo_path: str, default: str = "REQ") -> tuple[str, dict[str, str]]:
    """The id prefix and the `id_aliases` the repository's registry declares.

    Reads only the two top-level keys; the registry itself is never loaded
    into a finding. Without a registry the prefix is the default and there
    are no aliases.
    """
    try:
        data = json.loads((Path(repo_path) / REGISTRY_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default, {}
    if not isinstance(data, dict):
        return default, {}
    prefix = data.get("requirement_id_prefix")
    raw = data.get("id_aliases")
    aliases = {str(k): str(v) for k, v in raw.items() if k and v} if isinstance(raw, dict) else {}
    return (str(prefix) if prefix else default), aliases


def requirement_prefix(repo_path: str, default: str = "REQ") -> str:
    """The id prefix the repository's requirement registry declares, or the default."""
    return requirement_registry(repo_path, default)[0]


def cited_ids(text: str, prefix: str = "REQ", aliases: dict[str, str] | None = None) -> set[str]:
    """Requirement ids a commit message cites, resolved through the aliases.

    An id with the registry's prefix is kept as written; an id listed in
    `id_aliases` (an old `REQ-038` after a renumbering to `ARB`) becomes the
    id it maps to; anything else is some other project's number and is
    dropped, because a tag Arbiter cannot resolve is a tag it must not make.
    """
    aliases = aliases or {}
    out: set[str] = set()
    for tok in _ID_TOKEN.findall(text):
        if tok in aliases:
            out.add(aliases[tok])
        elif tok.startswith(prefix + "-"):
            out.add(tok)
    return out


def requirement_ids_by_path(repo_path: str, ref: str, prefix: str = "REQ",
                            aliases: dict[str, str] | None = None,
                            ) -> tuple[dict[str, set[str]], set[str]]:
    """Per path, the requirement ids cited by the commits in `ref..HEAD` that
    touched it; and every id the range cites at all.

    One git invocation for the whole range, however many files changed: a
    log per file was the first design and costs a subprocess per finding.
    Both results are empty when git is unavailable, the path is not a
    repository or the ref is unknown: an attribution the tool cannot make is
    left unmade, never guessed. Uncommitted work cites nothing, which is
    right -- it has no commit message yet.
    """
    by_path: dict[str, set[str]] = {}
    all_ids: set[str] = set()
    if not (Path(repo_path) / ".git").exists():
        return by_path, all_ids
    try:
        r = subprocess.run(["git", "-C", repo_path, "log", f"{ref}..HEAD", "--name-only",
                            f"--format={_RS}%H{_FS}%B{_FS}"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError):  # no git, or a hung one: both mean "unknown"
        return by_path, all_ids
    if r.returncode != 0:
        return by_path, all_ids
    for record in r.stdout.split(_RS):
        parts = record.split(_FS, 2)
        if len(parts) < 3:
            continue
        body, names = parts[1], parts[2]
        ids = cited_ids(body, prefix, aliases)
        all_ids.update(ids)
        if not ids:
            continue
        for line in names.split("\n"):
            path = line.strip().replace("\\", "/")
            if path:
                by_path.setdefault(path, set()).update(ids)
    return by_path, all_ids


def requirement_ids_since(repo_path: str, ref: str, prefix: str = "REQ",
                          aliases: dict[str, str] | None = None) -> list[str]:
    """Requirement ids cited by the commits in `ref..HEAD`, sorted and unique.

    The whole-range view of `requirement_ids_by_path`, kept for callers that
    want the round rather than the file.
    """
    return sorted(requirement_ids_by_path(repo_path, ref, prefix, aliases)[1])


def attribute_requirements(findings: list, repos: list, changed: dict[str, set[str]],
                           changed_since: str | None) -> None:
    """Tag each finding in the change with the requirements its file's
    commits cite, and say how the tags were chosen (ARB-052).

    `req:<ID>` for exactly the ids cited by the commits since the base that
    touched the finding's path, plus `req-scope:commits`. A path no such
    commit cited gets every id the range cites plus `req-scope:open`: the
    requirements open in this round are context for the reader, not a claim
    about which one introduced the finding. Findings in context files are
    not tagged at all -- they were not introduced under any of these ids.
    A full scan has no range: every finding carries `req-scope:open` and no
    `req:` tag, which is what the marker means.
    """
    if not changed_since:
        for f in findings:
            f.tags.append(REQ_SCOPE_OPEN)
        return
    for r in repos:
        prefix, aliases = requirement_registry(r.path)
        by_path, all_ids = requirement_ids_by_path(r.path, changed_since, prefix, aliases)
        in_change = changed.get(r.id, set())
        for f in findings:
            if f.repo_id != r.id or f.location.path not in in_change:
                continue
            cited = by_path.get(f.location.path)
            if cited:
                f.tags.extend(f"req:{i}" for i in sorted(cited))
                f.tags.append(REQ_SCOPE_COMMITS)
            else:
                f.tags.extend(f"req:{i}" for i in sorted(all_ids))
                f.tags.append(REQ_SCOPE_OPEN)


def narrow(inv: Inventory, selected: dict[str, set[str]]) -> tuple[Inventory, dict]:
    """Restrict an inventory to the selected paths plus context files.

    The stack set is carried over from the full inventory deliberately: which
    technologies a repository uses is a fact about the repository, not about
    the diff, and recomputing it from four files would make probes
    inapplicable that are perfectly applicable.
    """
    kept: list[FileInfo] = []
    changed_kept = 0
    context_kept = 0
    for f in inv.files:
        want = f.path in selected.get(f.repo_id, set())
        if want:
            changed_kept += 1
        elif is_context(f):
            context_kept += 1
            want = True
        if want:
            kept.append(f)

    out = Inventory(files=kept, stacks=set(inv.stacks), whole=inv.whole or inv)
    for f in kept:
        out.by_repo.setdefault(f.repo_id, []).append(f)
    for rid in inv.by_repo:
        out.by_repo.setdefault(rid, [])

    total_lines = sum(f.lines for f in inv.files)
    kept_lines = sum(f.lines for f in kept)
    stats = {
        "files_total": len(inv.files),
        "files_read": len(kept),
        "files_changed": changed_kept,
        "files_context": context_kept,
        "lines_total": total_lines,
        "lines_read": kept_lines,
        "fraction_read": round(kept_lines / total_lines, 4) if total_lines else 1.0,
    }
    return out, stats
