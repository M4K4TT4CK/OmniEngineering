"""The persistent per-file result cache (REQ-041).

Every test here has a control: the assertion that would pass for the wrong
reason is paired with one that shows the mechanism under test is what made
the difference. A cache that is never consulted passes "cached and uncached
agree" trivially, so that test also requires the hits.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from arbiter import cache as C
from arbiter.engine import run_scan
from arbiter.policy import load_config
from arbiter.probes import REGISTRY, probe_by_name

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "fixtures" / "legacy-platform"

# The AWS documentation example key: credential-shaped, and not live.
SECRET_LINE = 'aws_key = "AKIAIOSFODNN7EXAMPLE"\n'
CACHEABLE = [p.name for p in REGISTRY if p.cacheable]


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _cfg(root: Path | None = None) -> dict:
    """A configuration with the cache ON: conftest switches it off for the
    rest of the suite, and these tests are the ones that exercise it."""
    cfg = copy.deepcopy(load_config(None, str(root) if root else None))
    cfg["cache"] = {"enabled": True}
    return cfg


def _scan(root: Path, cfg: dict, **kw):
    kw.setdefault("use_adapters", False)
    kw.setdefault("cache_path", str(root.parent / "cache.json"))
    return run_scan([str(root)], cfg, **kw)


def _counting(monkeypatch, name: str) -> dict:
    """Replace a probe's run with one that counts its calls."""
    probe = probe_by_name(name)
    assert probe is not None
    real = probe.run
    calls = {"n": 0}

    def run(ctx):
        calls["n"] += 1
        return real(ctx)
    monkeypatch.setattr(probe, "run", run)
    return calls


def _secret_paths(report) -> set[str]:
    return {f.location.path for f in report.findings if f.rule_id.startswith("arbiter/secrets.")}


def _cache_stats(report) -> dict:
    return (report.scan_scope or {}).get("cache") or {}


# --------------------------------------------------------------------------

def test_a_cache_hit_does_not_rerun_the_probe(tmp_path, monkeypatch):
    root = _repo(tmp_path, {"a.py": SECRET_LINE, "b.py": "x = 1\n"})
    cfg = _cfg()
    calls = _counting(monkeypatch, "secrets")

    first = _scan(root, cfg, only=["secrets"])
    assert calls["n"] == 1
    assert _secret_paths(first) == {"a.py"}
    assert _cache_stats(first) == {"hits": 0, "misses": 2, "verified": 0, "divergent": 0,
                                   "path": str(tmp_path / "cache.json")}

    second = _scan(root, cfg, only=["secrets"])
    assert calls["n"] == 1, "every file was a hit, so the probe had nothing to run on"
    assert _secret_paths(second) == {"a.py"}, "the cached finding is still reported"
    assert _cache_stats(second)["hits"] == 2
    ran = next(p for p in second.probes if p.name == "secrets")
    assert ran.status == "ran" and ran.finding_count == 1
    assert "2 file(s) from cache" in ran.reason

    # Control: with the cache off the same second scan runs the probe again.
    _scan(root, cfg, only=["secrets"], use_cache=False)
    assert calls["n"] == 2


def test_a_config_change_invalidates_the_cache(tmp_path, monkeypatch):
    root = _repo(tmp_path, {"a.py": SECRET_LINE, "b.py": "x = 1\n"})
    cfg = _cfg()
    calls = _counting(monkeypatch, "secrets")

    _scan(root, cfg, only=["secrets"])
    assert calls["n"] == 1

    # Control: the same configuration is served from the cache.
    _scan(root, copy.deepcopy(cfg), only=["secrets"])
    assert calls["n"] == 1

    changed = copy.deepcopy(cfg)
    changed["quality"]["max_file_lines"] = 123
    rep = _scan(root, changed, only=["secrets"])
    assert calls["n"] == 2, "a changed configuration is a different question; nothing may hit"
    assert _cache_stats(rep)["misses"] == 2 and _cache_stats(rep)["hits"] == 0

    # The invalidation is the rules hash, not an accident of the file set.
    probe = probe_by_name("secrets")
    assert C.rules_hash(cfg, probe) != C.rules_hash(changed, probe)
    assert C.rules_hash(cfg, probe) == C.rules_hash(copy.deepcopy(cfg), probe)


def test_an_edited_file_misses_the_cache(tmp_path, monkeypatch):
    root = _repo(tmp_path, {"a.py": "x = 1\n", "b.py": "y = 2\n"})
    cfg = _cfg()
    calls = _counting(monkeypatch, "secrets")

    first = _scan(root, cfg, only=["secrets"])
    assert calls["n"] == 1 and _secret_paths(first) == set()

    (root / "a.py").write_text("x = 1\n" + SECRET_LINE, encoding="utf-8")
    second = _scan(root, cfg, only=["secrets"])
    assert calls["n"] == 2, "the edited file must be re-read"
    assert _secret_paths(second) == {"a.py"}, "and its new finding reported"
    # Control: the untouched file was still served from the cache.
    assert _cache_stats(second) == {"hits": 1, "misses": 1, "verified": 0, "divergent": 0,
                                    "path": str(tmp_path / "cache.json")}

    # And the key is the content, not the name or the timestamp.
    rules = C.rules_hash(cfg, probe_by_name("secrets"))
    before = C.key(str(root / "b.py"), rules, "b.py", "root")
    (root / "b.py").write_text("y = 2\n", encoding="utf-8")   # same bytes, new mtime
    assert C.key(str(root / "b.py"), rules, "b.py", "root") == before
    (root / "b.py").write_text("y = 3\n", encoding="utf-8")
    assert C.key(str(root / "b.py"), rules, "b.py", "root") != before
    assert C.key(str(root / "b.py"), rules, "elsewhere/b.py", "root") != before, \
        "the same bytes at another path are another file's findings"


def test_cached_and_uncached_scans_produce_identical_findings(tmp_path):
    cfg = _cfg(LEGACY)
    cache_path = str(tmp_path / "cache.json")

    def snapshot(report):
        return sorted((f.id, json.dumps(f.to_dict(), sort_keys=True, default=str))
                      for f in report.findings)

    plain = run_scan([str(LEGACY)], cfg, only=CACHEABLE, use_adapters=False, use_cache=False)
    cold = run_scan([str(LEGACY)], cfg, only=CACHEABLE, use_adapters=False, cache_path=cache_path)
    warm = run_scan([str(LEGACY)], cfg, only=CACHEABLE, use_adapters=False, cache_path=cache_path)

    assert plain.findings, "the fixture has planted defects for these probes"
    assert snapshot(cold) == snapshot(plain)
    assert snapshot(warm) == snapshot(plain)
    # Control: the warm scan really was answered from the cache. Without this
    # the comparison above passes for a cache that is never consulted.
    assert _cache_stats(cold)["hits"] == 0 and _cache_stats(cold)["misses"] > 0
    assert _cache_stats(warm)["misses"] == 0 and _cache_stats(warm)["hits"] > 0
    assert [p.finding_count for p in warm.probes] == [p.finding_count for p in plain.probes]

    # The mixed case: one file edited, the rest cached, must also agree with
    # a fresh scan of the edited tree. The edit lives in a copy.
    import shutil
    copy_root = tmp_path / "legacy"
    shutil.copytree(LEGACY, copy_root, ignore=shutil.ignore_patterns(".arbiter", "arbiter-out"))
    cfg2 = _cfg(copy_root)
    mixed_cache = str(tmp_path / "cache2.json")
    run_scan([str(copy_root)], cfg2, only=CACHEABLE, use_adapters=False, cache_path=mixed_cache)
    target = next(p for p in sorted(copy_root.rglob("*.py")) if "test" not in p.name)
    target.write_text(target.read_text(encoding="utf-8") + "\n" + SECRET_LINE, encoding="utf-8")
    mixed = run_scan([str(copy_root)], cfg2, only=CACHEABLE, use_adapters=False, cache_path=mixed_cache)
    fresh = run_scan([str(copy_root)], cfg2, only=CACHEABLE, use_adapters=False, use_cache=False)
    assert snapshot(mixed) == snapshot(fresh)
    assert 0 < _cache_stats(mixed)["misses"] < _cache_stats(mixed)["hits"]


def test_no_cache_bypasses_reading_and_writing(tmp_path, monkeypatch):
    root = _repo(tmp_path, {"a.py": SECRET_LINE})
    cfg = _cfg()
    calls = _counting(monkeypatch, "secrets")
    cache_path = tmp_path / "cache.json"

    for _ in range(2):
        rep = _scan(root, cfg, only=["secrets"], use_cache=False)
        assert "cache" not in (rep.scan_scope or {})
    assert calls["n"] == 2, "the probe runs every time"
    assert not cache_path.exists(), "nothing was written"
    assert not (root / ".arbiter" / "cache.json").exists()

    # A pre-existing cache is not read either: plant a stale entry that
    # says a.py is clean, and --no-cache must still find the secret.
    _scan(root, cfg, only=["secrets"])
    assert cache_path.exists()
    doc = json.loads(cache_path.read_text(encoding="utf-8"))
    for k in doc["entries"]["secrets"]:
        doc["entries"]["secrets"][k] = []
    cache_path.write_text(json.dumps(doc), encoding="utf-8")
    assert _secret_paths(_scan(root, cfg, only=["secrets"], use_cache=False)) == {"a.py"}
    # Control: with the cache on, the planted stale entry is believed.
    assert _secret_paths(_scan(root, cfg, only=["secrets"])) == set()

    # The configuration switch does the same as the flag.
    off = copy.deepcopy(cfg)
    off["cache"] = {"enabled": False}
    before = calls["n"]
    rep = _scan(root, off, only=["secrets"])
    assert calls["n"] == before + 1 and "cache" not in (rep.scan_scope or {})


def test_the_cache_file_is_never_scanned(tmp_path):
    """A file Arbiter writes must never be read back as evidence (the class
    of defect `test_output_directory_is_not_scanned` guards against). The
    cache holds every finding's evidence, so it is full of exactly the
    strings the probes look for."""
    root = _repo(tmp_path, {"a.py": "x = 1\n"})
    cfg = _cfg()
    # Not under .arbiter/, which the walker skips anyway: the point is the
    # exclusion of the cache path itself, wherever --cache-path puts it.
    cache_path = root / "cache.json"
    cache_path.write_text(json.dumps({
        "schema_version": C.SCHEMA_VERSION, "arbiter_version": C.ARBITER_VERSION,
        "entries": {"secrets": {"k": [{"evidence": SECRET_LINE}]}},
    }), encoding="utf-8")

    rep = run_scan([str(root)], cfg, only=["secrets"], use_adapters=False,
                   cache_path=str(cache_path))
    assert "cache.json" not in {f.location.path for f in rep.findings}
    assert _cache_stats(rep)["misses"] == 1, "one file was inventoried: a.py"

    # Control: the same text in an ordinary file is found.
    (root / "notes.py").write_text(json.dumps({"evidence": SECRET_LINE}), encoding="utf-8")
    rep = run_scan([str(root)], cfg, only=["secrets"], use_adapters=False,
                   cache_path=str(cache_path))
    assert _secret_paths(rep) == {"notes.py"}

    # Second control: once it is no longer the cache, the same file at the
    # same place is ordinary evidence again.
    rep = run_scan([str(root)], cfg, only=["secrets"], use_adapters=False, use_cache=False)
    assert _secret_paths(rep) == {"cache.json", "notes.py"}

    # And the default location is never read, with the cache on or off.
    cache_path.unlink()
    (root / ".arbiter").mkdir()
    (root / ".arbiter" / "cache.json").write_text(SECRET_LINE, encoding="utf-8")
    for use_cache in (True, False):
        rep = run_scan([str(root)], cfg, only=["secrets"], use_adapters=False,
                       use_cache=use_cache)
        assert _secret_paths(rep) == {"notes.py"}


def test_an_uncacheable_probe_always_runs(tmp_path, monkeypatch):
    root = _repo(tmp_path, {"a.py": SECRET_LINE, "README.md": "# hi\n"})
    cfg = _cfg()
    assert probe_by_name("house_rules").cacheable is False
    assert probe_by_name("quality").cacheable is False
    plain = _counting(monkeypatch, "quality")
    cached = _counting(monkeypatch, "secrets")

    for _ in range(3):
        _scan(root, cfg, only=["quality", "secrets"])
    assert plain["n"] == 3, "an uncacheable probe runs on every scan"
    assert cached["n"] == 1, "control: the cacheable one ran once"
    doc = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    assert "quality" not in doc["entries"] and "secrets" in doc["entries"]


def test_verify_cache_reports_a_divergent_entry(tmp_path):
    root = _repo(tmp_path, {"a.py": SECRET_LINE, "b.py": "x = 1\n"})
    cfg = _cfg()
    cache_path = tmp_path / "cache.json"
    _scan(root, cfg, only=["secrets"])

    # Control: an untampered cache verifies clean, every hit re-run.
    rep = _scan(root, cfg, only=["secrets"], verify_cache=True, verify_sample=1.0)
    assert not [f for f in rep.findings if f.rule_id == C.DIVERGENCE_RULE]
    assert _cache_stats(rep)["verified"] == 2 and _cache_stats(rep)["divergent"] == 0
    assert _secret_paths(rep) == {"a.py"}

    # Tamper: the entry for a.py now says the file is clean.
    doc = json.loads(cache_path.read_text(encoding="utf-8"))
    tampered = [k for k, v in doc["entries"]["secrets"].items() if v]
    assert len(tampered) == 1
    doc["entries"]["secrets"][tampered[0]] = []
    cache_path.write_text(json.dumps(doc), encoding="utf-8")

    # Without verification the lie is believed -- that is what --no-cache and
    # --verify-cache exist for.
    assert _secret_paths(_scan(root, cfg, only=["secrets"])) == set()

    rep = _scan(root, cfg, only=["secrets"], verify_cache=True, verify_sample=1.0)
    div = [f for f in rep.findings if f.rule_id == C.DIVERGENCE_RULE]
    assert len(div) == 1
    assert div[0].location.path == "a.py" and div[0].probe == "secrets"
    assert div[0].severity == "high" and div[0].dimension == "assurance"
    assert "secrets" in div[0].title
    assert _secret_paths(rep) == {"a.py"}, "the fresh result is what gets reported"
    assert _cache_stats(rep)["divergent"] == 1 and _cache_stats(rep)["verified"] == 2

    # The entry was replaced: a plain scan now reports the secret again.
    rep = _scan(root, cfg, only=["secrets"])
    assert _secret_paths(rep) == {"a.py"}
    assert not [f for f in rep.findings if f.rule_id == C.DIVERGENCE_RULE]
    assert _cache_stats(rep)["hits"] == 2

    # The default sample is a fraction, never nothing: at least one file.
    rep = _scan(root, cfg, only=["secrets"], verify_cache=True)
    assert _cache_stats(rep)["verified"] == 1


def test_a_corrupt_cache_file_is_ignored(tmp_path, monkeypatch):
    root = _repo(tmp_path, {"a.py": SECRET_LINE})
    cfg = _cfg()
    cache_path = tmp_path / "cache.json"
    calls = _counting(monkeypatch, "secrets")

    for junk in ("{not json", '{"schema_version": 999, "entries": {}}',
                 '{"schema_version": 1, "arbiter_version": "x", "entries": {}}',
                 '{"schema_version": 1, "entries": "nope"}', ""):
        cache_path.write_text(junk, encoding="utf-8")
        loaded = C.ResultCache.load(cache_path)
        assert loaded.entries == {} and not loaded.loaded_from_disk
        before = calls["n"]
        rep = _scan(root, cfg, only=["secrets"])
        assert calls["n"] == before + 1, "treated as empty: the probe ran"
        assert _secret_paths(rep) == {"a.py"}
        assert _cache_stats(rep)["hits"] == 0
        # and the scan leaves a valid file behind
        json.loads(cache_path.read_text(encoding="utf-8"))

    # Control: the valid file it wrote is then used.
    before = calls["n"]
    rep = _scan(root, cfg, only=["secrets"])
    assert calls["n"] == before and _cache_stats(rep)["hits"] == 1
    assert C.ResultCache.load(cache_path).loaded_from_disk

    # A missing file is the same as an empty one, and an unwritable path
    # costs the scan nothing.
    assert C.ResultCache.load(tmp_path / "nowhere" / "cache.json").entries == {}
    unwritable = C.ResultCache(cache_path / "not-a-dir" / "cache.json")
    unwritable.put("secrets", "k", [])
    assert unwritable.save() is False


def test_the_cache_is_capped_and_written_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "MAX_KEYS", 5)
    cache = C.ResultCache(tmp_path / "cache.json")
    for i in range(8):
        cache.put("secrets", f"k{i}", [{"i": i}])
    assert cache.save()
    reloaded = C.ResultCache.load(tmp_path / "cache.json")
    assert sorted(reloaded.entries["secrets"]) == ["k3", "k4", "k5", "k6", "k7"], \
        "the oldest entries are the ones dropped"
    assert not list(tmp_path.glob(".cache-*.tmp")), "no temporary file is left behind"
    # A hit refreshes an entry, so a file read every scan is never evicted
    # just because it was first seen long ago.
    assert reloaded.get("secrets", "k3") == [{"i": 3}]
    reloaded.put("secrets", "new", [])
    reloaded.save()
    assert "k3" in C.ResultCache.load(tmp_path / "cache.json").entries["secrets"]
    assert "k4" not in C.ResultCache.load(tmp_path / "cache.json").entries["secrets"]


@pytest.mark.parametrize("name", CACHEABLE)
def test_every_cacheable_probe_is_file_scoped_and_attributes_every_finding(name):
    probe = probe_by_name(name)
    assert probe.scope == "file", "cacheable is a stronger claim than file-scoped"


def test_a_same_size_edit_within_one_mtime_tick_changes_the_key(tmp_path):
    """FAIL-041: the digest is of the bytes, never of (path, mtime, size). Two
    same-size writes with the timestamp pinned must still key differently.
    Bytes, not text: text mode writes CRLF on Windows and the sizes would differ."""
    f = tmp_path / "b.py"
    f.write_bytes(b"y = 2\n")
    pinned = f.stat().st_mtime
    before = C.key(str(f), "rules", "b.py", "root")
    f.write_bytes(b"y = 3\n")
    os.utime(f, (pinned, pinned))                      # same size, same mtime
    assert f.stat().st_size == len(b"y = 2\n")
    assert C.key(str(f), "rules", "b.py", "root") != before
    # Control: writing the original bytes back restores the original key.
    f.write_bytes(b"y = 2\n")
    os.utime(f, (pinned, pinned))
    assert C.key(str(f), "rules", "b.py", "root") == before
