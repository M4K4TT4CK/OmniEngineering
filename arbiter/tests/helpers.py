"""Helpers shared by more than one test file.

Test files never import from each other; anything two of them need lives
here. ROOT, LEGACY and SYSTEM mirror conftest.py so a test can name the
fixtures without a fixture argument.
"""
from __future__ import annotations

from pathlib import Path

from arbiter.core import Finding, Location
from arbiter.engine import run_scan
from arbiter.policy import load_config

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "fixtures" / "legacy-platform"
SYSTEM = ROOT / "fixtures" / "system" / "arbiter-system.yaml"
TFPLAN = ROOT / "fixtures" / "tfplan"


def _scan_text(tmp_path, name: str, content: str, only: list[str], cfg_extra: dict | None = None):
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text(content)
    cfg = dict(load_config(None))
    cfg.update(cfg_extra or {})
    return run_scan([str(tmp_path)], cfg, only=only).active()


def _finding(rule, path="a.tf", line=1, evidence=""):
    return Finding(rule_id=rule, title=f"{rule} here", evidence=evidence or f"{path}:{line}",
                   location=Location(path=path, start_line=line))


def _git_repo(root, files, second=None):
    """A real git repository with one commit, and optionally a second."""
    import subprocess
    root.mkdir(parents=True, exist_ok=True)
    def run(*a):
        subprocess.run(["git", "-C", str(root), *a], capture_output=True, check=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@t"); run("config", "user.name", "t")
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    run("add", "-A"); run("commit", "-qm", "one")
    base = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    if second:
        for name, text in second.items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        run("add", "-A"); run("commit", "-qm", "two")
    return base


CLEAN_PY = "def add(a, b):\n    return a + b\n"


LEAKY_PY = 'TOKEN = "ghp_' + "b" * 36 + '"\n'
