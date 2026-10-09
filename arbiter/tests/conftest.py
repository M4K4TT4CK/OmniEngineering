"""Fixtures shared across the suite.

The two fixture scans are the most expensive thing the tests do, and more
than one file needs them. Session scope means the suite pays for each once
rather than once per file -- without this, adding a second file that scans
`fixtures/legacy-platform` doubles the wall clock of the pull-request gate.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from arbiter.engine import run_scan
from arbiter.policy import load_config

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "fixtures" / "legacy-platform"
SYSTEM = ROOT / "fixtures" / "system" / "arbiter-system.yaml"

# The external analyzers are calibrated separately and are not always
# installed; a fixture scan measures Arbiter's own rules.
EXTERNAL = ["checkov", "semgrep", "bandit", "ruff", "gitleaks"]


@pytest.fixture(scope="session", autouse=True)
def _result_cache_off():
    """The suite measures Arbiter's rules the way CI does, without the
    result cache (REQ-041): a fixture scan must never be answered from a
    previous session's findings, and the fixtures must not accumulate
    cache files. tests/test_cache.py switches it on per scan, explicitly."""
    from arbiter.policy import DEFAULTS
    DEFAULTS["cache"]["enabled"] = False
    yield
    DEFAULTS["cache"]["enabled"] = True


@pytest.fixture(scope="session")
def legacy_report():
    cfg = load_config(None, str(LEGACY))
    return run_scan([str(LEGACY)], cfg, skip=EXTERNAL)


@pytest.fixture(scope="session")
def system_report():
    cfg = load_config(None)
    return run_scan([], cfg, system_path=str(SYSTEM), skip=EXTERNAL)


# The Terraform plan fixture is scanned twice (with the plan and without it)
# by tests/test_probes_iac.py and once more by the claim-integrity check in
# tests/test_policy_gate.py, so it lives here for the same reason as the two
# above.
TFPLAN = ROOT / "fixtures" / "tfplan"


@pytest.fixture(scope="session")
def plan_report():
    return run_scan([str(TFPLAN)], load_config(None), only=["resource_policy"])


@pytest.fixture(scope="session")
def source_only_report(tmp_path_factory):
    """The same repository with the plan removed — the old behaviour."""
    import shutil
    dst = tmp_path_factory.mktemp("srconly") / "repo"
    shutil.copytree(TFPLAN, dst)
    (dst / "tfplan.json").unlink()
    return run_scan([str(dst)], load_config(None), only=["resource_policy"])
