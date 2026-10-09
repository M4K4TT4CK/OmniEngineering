"""The air-gapped bundle: Arbiter as a directory that installs with no network.

A bundle is a directory holding the wheel of Arbiter itself, the wheels of its
runtime dependencies, the calibration knowledge file when the checkout carries
one, two install scripts and a manifest with a SHA-256 per file. Carried onto a
machine with no network, `install.sh` or `install.ps1` runs
`pip install --no-index --find-links wheels arbiter-eval` and nothing else.

## What is not in it, and why

The five external analyzers -- ruff, bandit, semgrep, checkov and gitleaks --
are not included. Shipping them is a redistribution, and the redistribution
review (L-6 in docs/licensing.md, tracked as ARB-005) has not been done;
semgrep's LGPL-2.1 terms in particular need a person, not a build step, to sign
off. A bundle of Arbiter alone conveys nothing but Arbiter, so it is not
blocked. The manifest says so in words, so a bundle found on a disk a year
from now explains its own gap. Operators install the analyzers from their own
mirror, and a scan that cannot find one reports the probe as *not assessed*
rather than passing it, exactly as it does everywhere else.

The rule packs are not copied either, because they are already inside the
wheel: `pyproject.toml` lists `packs/**` as package data.

## The one step that needs a network

`pip wheel` of the checkout itself can run offline when `setuptools` is
importable by the running interpreter (the build then runs without pip's
isolated build environment). Downloading the dependency wheels needs an index
-- PyPI, or a local mirror named through `PIP_INDEX_URL` / `PIP_FIND_LINKS`.
`include_deps=False` (`--no-deps-download`) skips it for a machine that already
holds PyYAML; the manifest records which was done.

A bundle is built for the Python and platform it was built on: PyYAML ships a
compiled wheel per interpreter version and platform, and `pip download` fetches
the one matching the builder. Build on a machine that matches the target.

## Verification

`verify` recomputes every hash and fails on a missing file, an altered file or
an extra file -- anything present that the manifest does not name, other than
the manifest itself. An extra file is a failure rather than a warning because
`pip install --find-links` installs whatever wheel it finds in `wheels/`, so a
file nobody listed is exactly the thing a verifier exists to notice.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .adapters import _toml_loads
from .learn import DEFAULT_PATH as KNOWLEDGE_DEFAULT_PATH

MANIFEST_NAME = "MANIFEST.json"
MANIFEST_FORMAT = 1
DIST_NAME = "arbiter-eval"

# Stated once, quoted by the manifest and the bundle's README verbatim.
ANALYZERS_NOTE = ("not included: redistribution review pending (ARB-005); install "
                  "ruff, bandit, semgrep, checkov and gitleaks from your own mirror")

INSTALL_SH = """#!/bin/sh
# Install Arbiter from this bundle with no network access.
# Set PYTHON to the interpreter to install into (default: python3).
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
py="${PYTHON:-python3}"
"$py" -m pip install --no-index --find-links "$here/wheels" arbiter-eval
"""

INSTALL_PS1 = """# Install Arbiter from this bundle with no network access.
# Set $env:PYTHON to the interpreter to install into (default: python).
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = if ($env:PYTHON) { $env:PYTHON } else { "python" }
& $py -m pip install --no-index --find-links (Join-Path $here "wheels") arbiter-eval
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
"""


class BundleError(Exception):
    """A bundle could not be built or read. The message says why."""


@dataclass
class VerifyResult:
    """What `verify` found. `ok` is true only when every list is empty."""

    checked: int = 0
    missing: list[str] = field(default_factory=list)
    altered: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.altered or self.extra)

    def lines(self) -> list[str]:
        """The verdict as text, one problem per line, verdict last."""
        out = [f"missing: {p}" for p in self.missing]
        out += [f"altered: {p}" for p in self.altered]
        out += [f"extra (not in manifest): {p}" for p in self.extra]
        if self.ok:
            out.append(f"bundle verified: {self.checked} file(s) match the manifest")
        else:
            out.append(f"bundle FAILED verification: {len(self.missing)} missing, "
                       f"{len(self.altered)} altered, {len(self.extra)} extra")
        return out


def package_root(start: Path | None = None) -> Path | None:
    """The checkout this module was imported from, or None when it is an
    installed copy. Found by the `pyproject.toml` naming this distribution,
    walking up from the module file."""
    here = (start or Path(__file__)).resolve()
    for candidate in [here, *here.parents]:
        pyproject = candidate / "pyproject.toml"
        if pyproject.is_file():
            try:
                project = _toml_loads(pyproject.read_text(encoding="utf-8")).get("project", {})
            except (OSError, ValueError):
                continue
            if project.get("name") == DIST_NAME:
                return candidate
    return None


def _project_table(source: Path) -> dict:
    pyproject = source / "pyproject.toml"
    if not pyproject.is_file():
        raise BundleError(f"{source} has no pyproject.toml; pass --source <checkout>")
    project = _toml_loads(pyproject.read_text(encoding="utf-8")).get("project", {})
    if project.get("name") != DIST_NAME:
        raise BundleError(f"{pyproject} does not describe {DIST_NAME}")
    return project


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files_under(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def _run(argv: list[str], runner=subprocess.run) -> None:
    """Run one pip step; a failure is a BundleError carrying pip's last lines."""
    proc = runner(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout or "").strip().splitlines()[-8:])
        raise BundleError(f"{' '.join(argv[:4])} ... failed (exit {proc.returncode}):\n{tail}")


def _setuptools_available(python: str) -> bool:
    """Whether `python` can build without pip's isolated environment, which is
    what lets `pip wheel` run with no network."""
    if python == sys.executable:
        try:
            import setuptools  # noqa: F401 - imported only to prove it is installed
            return True
        except ImportError:
            return False
    probe = subprocess.run([python, "-c", "import setuptools"],
                           capture_output=True, text=True)
    return probe.returncode == 0


def build(out_dir: str | Path, *, include_deps: bool = True,
          source: str | Path | None = None, python: str | None = None,
          runner=subprocess.run) -> dict:
    """Write a bundle into `out_dir` and return its manifest.

    `out_dir` must not exist or must be empty: a leftover file from an earlier
    build would be an extra file the manifest does not name, and `verify` would
    rightly refuse the result. `runner` is `subprocess.run` or a stand-in; it
    runs the two pip steps and nothing else.
    """
    out = Path(out_dir).expanduser().resolve()
    if out.exists() and any(out.iterdir()):
        raise BundleError(f"{out} exists and is not empty; build into a new directory")
    src = Path(source).expanduser().resolve() if source else package_root()
    if src is None:
        raise BundleError("this Arbiter is an installed copy, not a checkout; "
                          "pass --source <path to the arbiter checkout>")
    project = _project_table(src)
    python = python or sys.executable

    wheels = out / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)

    # Arbiter itself. `--no-deps` so this step pulls nothing; the dependencies
    # are the next step, which is the one that may need a network.
    argv = [python, "-m", "pip", "wheel", "--no-deps", "-w", str(wheels)]
    offline_build = _setuptools_available(python)
    if offline_build:
        # An importable setuptools is not always one that can build: a
        # distribution-patched copy (Debian's, with its `install_layout`
        # option) fails against a newer pip. When the offline attempt fails,
        # the isolated build is tried, which fetches the backend from an
        # index, and the manifest records that the wheel was not built offline.
        try:
            _run([*argv, "--no-build-isolation", str(src)], runner)
        except BundleError as exc:
            offline_build = False
            try:
                _run([*argv, str(src)], runner)
            except BundleError as retry:
                raise BundleError(f"{exc}\n\nisolated retry also failed:\n{retry}") from retry
    else:
        _run([*argv, str(src)], runner)
    built = [p.name for p in _files_under(wheels) if p.suffix == ".whl"]
    if not any(name.startswith("arbiter_eval-") for name in built):
        raise BundleError(f"pip wheel wrote no arbiter_eval wheel into {wheels}: {built}")

    dependencies = list(project.get("dependencies", []))
    if include_deps and dependencies:
        # pip evaluates the environment markers in these strings itself, so
        # `tomli>=2; python_version < '3.11'` is skipped on 3.11+ without this
        # module having to parse the marker.
        _run([python, "-m", "pip", "download", "--only-binary=:all:", "--no-deps",
              "-d", str(wheels), *dependencies], runner)

    # The knowledge file, when the checkout carries one at the default path.
    knowledge_src = src / KNOWLEDGE_DEFAULT_PATH
    if knowledge_src.is_file():
        (out / "knowledge.json").write_bytes(knowledge_src.read_bytes())
        knowledge = {"included": True, "file": "knowledge.json",
                     "from": KNOWLEDGE_DEFAULT_PATH}
    else:
        knowledge = {"included": False,
                     "reason": f"no {KNOWLEDGE_DEFAULT_PATH} in the source checkout"}

    (out / "install.sh").write_text(INSTALL_SH, encoding="utf-8", newline="\n")
    try:
        os.chmod(out / "install.sh", 0o755)
    except OSError:  # pragma: no cover - platform dependent
        pass
    (out / "install.ps1").write_text(INSTALL_PS1, encoding="utf-8", newline="\r\n")

    version = project.get("version", __version__)
    floor = project.get("requires-python", "")
    (out / "README.txt").write_text(_readme(version, floor, include_deps, knowledge),
                                    encoding="utf-8")

    manifest = {
        "format": MANIFEST_FORMAT,
        "distribution": DIST_NAME,
        "arbiter_version": version,
        "python_floor": floor,
        "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "built_with": {"python": platform.python_version(),
                       "platform": platform.platform()},
        "dependencies": dependencies,
        "dependencies_included": bool(include_deps and dependencies),
        "wheel_built_offline": offline_build,
        "knowledge": knowledge,
        "analyzers": ANALYZERS_NOTE,
        "files": {},
    }
    for path in _files_under(out):
        rel = path.relative_to(out).as_posix()
        manifest["files"][rel] = {"sha256": sha256_of(path), "bytes": path.stat().st_size}
    (out / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n",
                                     encoding="utf-8")
    return manifest


def _readme(version: str, floor: str, include_deps: bool, knowledge: dict) -> str:
    deps = ("wheels/ holds Arbiter and its runtime dependencies."
            if include_deps else
            "wheels/ holds Arbiter only; PyYAML (and tomli below Python 3.11) must\n"
            "already be installed, or be reachable through pip's own --find-links.")
    know = ("knowledge.json is the calibration knowledge the checkout carried; copy it\n"
            "to .arbiter/knowledge.json in the repository you scan if you want it."
            if knowledge.get("included") else
            "No knowledge file was in the checkout, so none is included.")
    return f"""Arbiter {version} -- air-gapped install bundle
Requires Python {floor or '(see MANIFEST.json)'}.

Install, with no network, in three steps:

  1. Check the bundle: from any machine that already has Arbiter, run
       arbiter bundle verify <this directory>
     It recomputes every SHA-256 in MANIFEST.json and fails on a missing,
     altered or extra file.
  2. Install:  ./install.sh   (POSIX)   or   .\\install.ps1   (PowerShell)
     Both run: pip install --no-index --find-links wheels arbiter-eval
     Set PYTHON to choose the interpreter; a virtualenv is a good target.
  3. Confirm:  arbiter --version    and, if you skipped step 1,
               arbiter bundle verify <this directory>

Analyzers: {ANALYZERS_NOTE}.
A scan that cannot find an analyzer reports that probe as not assessed; it
never passes it.

{deps}
{know}
"""


def load_manifest(bundle_dir: str | Path) -> dict:
    path = Path(bundle_dir).expanduser().resolve() / MANIFEST_NAME
    if not path.is_file():
        raise BundleError(f"{path} not found; is this a bundle directory?")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise BundleError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        raise BundleError(f"{path} has no 'files' table")
    return manifest


def verify(bundle_dir: str | Path) -> VerifyResult:
    """Recompute every hash; report what is missing, altered or unlisted."""
    root = Path(bundle_dir).expanduser().resolve()
    manifest = load_manifest(root)
    result = VerifyResult()
    listed = manifest["files"]
    for rel, entry in sorted(listed.items()):
        path = root / Path(rel)
        if not path.is_file():
            result.missing.append(rel)
            continue
        expected = entry.get("sha256") if isinstance(entry, dict) else entry
        if sha256_of(path) != expected:
            result.altered.append(rel)
        else:
            result.checked += 1
    for path in _files_under(root):
        rel = path.relative_to(root).as_posix()
        if rel != MANIFEST_NAME and rel not in listed:
            result.extra.append(rel)
    return result
