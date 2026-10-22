"""Every arbiter module imports on its own, first, in a fresh interpreter (ARB-078).

`import arbiter.lang_rules` before `arbiter.probes` used to raise a circular
ImportError: lang_rules imported probes at load time, and probes imports
lang_rules at its end to register the packs. The test suite never saw it,
because conftest and earlier tests had already imported probes. Each check
here runs in a new subprocess so nothing is imported beforehand.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
MODULES = sorted(p.stem for p in (SRC / "arbiter").glob("*.py") if not p.stem.startswith("__"))


def _fresh(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # This checkout first, never whichever arbiter happens to be installed.
    env["PYTHONPATH"] = os.pathsep.join([str(SRC), *filter(None, [env.get("PYTHONPATH")])])
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=env, timeout=120, check=False)


def test_lang_rules_imports_first_and_the_packs_still_register():
    r = _fresh(
        "import arbiter.lang_rules as l, arbiter\n"
        "from pathlib import Path\n"
        "assert Path(l.__file__).resolve().parent == Path(arbiter.__file__).resolve().parent\n"
        "assert l.load_packs(), 'packs load without probes imported'\n"
        "import arbiter.probes as p\n"
        "names = {x.name for x in p.REGISTRY}\n"
        "missing = {'lang_' + k for k in l.load_packs()} - names\n"
        "assert not missing, missing\n"
        "print('ok', arbiter.__file__)\n")
    assert r.returncode == 0, r.stderr
    assert str(SRC) in r.stdout, f"imported a different arbiter: {r.stdout}"


@pytest.mark.parametrize("module", MODULES)
def test_every_module_imports_first_in_a_fresh_interpreter(module):
    r = _fresh(f"import arbiter.{module}")
    last = (r.stderr.strip().splitlines() or [""])[-1]
    if r.returncode and last.startswith("ModuleNotFoundError") and "'arbiter" not in last:
        pytest.skip(f"optional dependency missing here: {last}")
    assert r.returncode == 0, last
