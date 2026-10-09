"""The air-gapped bundle (ARB-051): what it holds, what it refuses, what it says.

The pip steps are the only part that needs a build backend or an index, so
most of these tests stand a fake runner in for them and exercise the manifest
and `verify` on real files. One test runs `pip wheel` for real when the
interpreter can build without isolation, which is the offline path the bundle
exists for.
"""
from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from arbiter import bundle, cli

ROOT = Path(__file__).resolve().parents[1]


def _fake_pip(argv, capture_output=True, text=True):
    """Stand in for the two pip steps: write a wheel where pip would."""
    if "wheel" in argv:
        where = Path(argv[argv.index("-w") + 1])
        with zipfile.ZipFile(where / "arbiter_eval-0.1.0-py3-none-any.whl", "w") as zf:
            zf.writestr("arbiter_eval-0.1.0.dist-info/METADATA", "Name: arbiter-eval\n")
    elif "download" in argv:
        where = Path(argv[argv.index("-d") + 1])
        (where / "pyyaml-6.0.2-fake.whl").write_bytes(b"not really a wheel")
    return subprocess.CompletedProcess(argv, 0, "", "")


def _failing_pip(argv, capture_output=True, text=True):
    return subprocess.CompletedProcess(argv, 1, "", "ERROR: no matching distribution")


@pytest.fixture
def built(tmp_path):
    out = tmp_path / "bundle"
    manifest = bundle.build(out, include_deps=False, runner=_fake_pip)
    return out, manifest


def test_a_bundle_holds_the_wheel_the_scripts_and_a_manifest_per_file(built):
    out, manifest = built
    names = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert "wheels/arbiter_eval-0.1.0-py3-none-any.whl" in names
    assert {"install.sh", "install.ps1", "README.txt", "MANIFEST.json"} <= names
    # Every file but the manifest is hashed; the manifest cannot hash itself.
    assert set(manifest["files"]) == names - {"MANIFEST.json"}
    for entry in manifest["files"].values():
        assert len(entry["sha256"]) == 64 and entry["bytes"] >= 0
    assert manifest["arbiter_version"] == "0.1.0"
    assert manifest["python_floor"].startswith(">=3.")
    assert manifest["built"].endswith("Z")


def test_the_manifest_and_the_readme_say_the_analyzers_are_excluded_and_why(built):
    """A bundle found on a disk a year from now must explain its own gap."""
    out, manifest = built
    assert manifest["analyzers"] == bundle.ANALYZERS_NOTE
    assert "ARB-005" in manifest["analyzers"]
    for tool in ("ruff", "bandit", "semgrep", "checkov", "gitleaks"):
        assert tool in manifest["analyzers"]
    readme = (out / "README.txt").read_text(encoding="utf-8")
    assert bundle.ANALYZERS_NOTE in readme
    assert "pip install --no-index --find-links wheels arbiter-eval" in readme
    # No analyzer binary or wheel sneaks in under another name.
    for name in manifest["files"]:
        assert not any(t in name.lower() for t in ("semgrep", "checkov", "bandit",
                                                   "gitleaks", "ruff"))


def test_the_install_scripts_install_from_the_bundle_and_nowhere_else(built):
    out, _ = built
    sh = (out / "install.sh").read_text(encoding="utf-8")
    ps1 = (out / "install.ps1").read_bytes().decode("utf-8")
    for script in (sh, ps1):
        assert "--no-index" in script and "--find-links" in script
        assert "arbiter-eval" in script
    assert "\r" not in sh and ps1.count("\r\n") > 1


def test_the_knowledge_file_travels_when_the_checkout_has_one(tmp_path):
    out = tmp_path / "with"
    manifest = bundle.build(out, include_deps=False, runner=_fake_pip)
    expected = ROOT / bundle.KNOWLEDGE_DEFAULT_PATH
    if expected.is_file():
        assert manifest["knowledge"]["included"] is True
        assert (out / "knowledge.json").read_bytes() == expected.read_bytes()
        assert "knowledge.json" in manifest["files"]
    else:  # pragma: no cover - this checkout tracks the file
        assert manifest["knowledge"]["included"] is False

    # A source without one says so rather than shipping an empty file.
    bare = tmp_path / "bare-src"
    bare.mkdir()
    (bare / "pyproject.toml").write_text(
        '[project]\nname = "arbiter-eval"\nversion = "9.9.9"\n'
        'requires-python = ">=3.10"\ndependencies = ["PyYAML>=6.0"]\n',
        encoding="utf-8")
    manifest = bundle.build(tmp_path / "without", include_deps=False,
                            source=bare, runner=_fake_pip)
    assert manifest["knowledge"]["included"] is False
    assert not (tmp_path / "without" / "knowledge.json").exists()
    assert manifest["arbiter_version"] == "9.9.9"


def test_the_packs_are_not_copied_because_the_wheel_carries_them(built):
    """pyproject lists packs/** as package data; copying them beside the wheel
    would be a second, unversioned copy."""
    out, manifest = built
    assert not any(name.startswith("packs/") for name in manifest["files"])
    assert not (out / "packs").exists()


def test_skipping_the_download_is_recorded_not_hidden(tmp_path):
    calls = []

    def recording(argv, capture_output=True, text=True):
        calls.append(argv)
        return _fake_pip(argv)

    manifest = bundle.build(tmp_path / "deps", include_deps=True, runner=recording)
    assert manifest["dependencies_included"] is True
    assert any("download" in argv and "--only-binary=:all:" in argv for argv in calls)
    assert any(name.startswith("wheels/pyyaml") for name in manifest["files"])

    calls.clear()
    manifest = bundle.build(tmp_path / "nodeps", include_deps=False, runner=recording)
    assert manifest["dependencies_included"] is False
    assert not any("download" in argv for argv in calls)
    assert "PyYAML" in (tmp_path / "nodeps" / "README.txt").read_text(encoding="utf-8")


def test_verify_passes_a_bundle_as_built(built):
    out, _ = built
    result = bundle.verify(out)
    assert result.ok, result.lines()
    assert result.checked == len(json.loads((out / "MANIFEST.json").read_text())["files"])
    assert result.lines()[-1].startswith("bundle verified")


def test_verify_fails_when_one_byte_changes(built):
    out, _ = built
    wheel = next((out / "wheels").glob("*.whl"))
    data = bytearray(wheel.read_bytes())
    data[len(data) // 2] ^= 0x01
    wheel.write_bytes(bytes(data))
    result = bundle.verify(out)
    assert not result.ok
    assert result.altered == [f"wheels/{wheel.name}"]
    assert not result.missing and not result.extra


def test_verify_fails_on_an_extra_file(built):
    """pip installs whatever wheel it finds in wheels/, so an unlisted file is
    the thing a verifier exists to notice."""
    out, _ = built
    (out / "wheels" / "arbiter_eval-0.1.0-planted.whl").write_bytes(b"\x00")
    result = bundle.verify(out)
    assert not result.ok
    assert result.extra == ["wheels/arbiter_eval-0.1.0-planted.whl"]
    assert "extra (not in manifest)" in "\n".join(result.lines())


def test_verify_fails_on_a_missing_file(built):
    out, _ = built
    (out / "install.sh").unlink()
    result = bundle.verify(out)
    assert not result.ok and result.missing == ["install.sh"]


def test_the_cli_verdict_and_exit_code_follow_the_result(built, capsys):
    out, _ = built
    assert cli.main(["bundle", "verify", str(out)]) == cli.EXIT_OK
    assert "bundle verified" in capsys.readouterr().out
    (out / "README.txt").write_text("edited", encoding="utf-8")
    assert cli.main(["bundle", "verify", str(out)]) == cli.EXIT_GATE_FAIL
    shown = capsys.readouterr().out
    assert "altered: README.txt" in shown and "FAILED" in shown
    assert cli.main(["bundle", "verify", str(out / "nowhere")]) == cli.EXIT_ERROR


def test_build_refuses_a_directory_that_already_holds_files(tmp_path):
    """A leftover would be an extra file the manifest does not name."""
    out = tmp_path / "used"
    out.mkdir()
    (out / "stale.txt").write_text("x", encoding="utf-8")
    with pytest.raises(bundle.BundleError, match="not empty"):
        bundle.build(out, include_deps=False, runner=_fake_pip)


def test_a_failed_pip_step_is_reported_with_its_reason(tmp_path):
    with pytest.raises(bundle.BundleError, match="no matching distribution"):
        bundle.build(tmp_path / "b", include_deps=False, runner=_failing_pip)


def test_pip_wheel_builds_the_checkout_offline_when_setuptools_is_present(tmp_path):
    """The one real pip run: the checkout wheels without an index when the
    interpreter can build without isolation. Skipped, not faked, where it
    cannot -- that is the risk ARB-051 names, and a skip says so."""
    pytest.importorskip("setuptools", reason="pip wheel needs setuptools for an offline build")
    out = tmp_path / "real"
    manifest = bundle.build(out, include_deps=False, source=ROOT)
    # A distribution-patched setuptools (Debian's) can import yet fail to
    # build; the builder then retries with isolation and says so.
    assert isinstance(manifest["wheel_built_offline"], bool)
    wheel = next((out / "wheels").glob("arbiter_eval-*.whl"))
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    # The packs ride inside the wheel, which is why the bundle does not copy them.
    assert any(n.startswith("arbiter/packs/") and n.endswith(".yaml") for n in names)
    assert any(n.endswith(".adapter.toml") for n in names)
    assert bundle.verify(out).ok
