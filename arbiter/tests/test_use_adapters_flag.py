"""`use_adapters=False` keeps every external analyzer out of a scan, even after
another scan in the same process registered them.

FAIL-044: the flag only skipped `register_adapters()`, and the registry is
module-global, so the first scan with adapters made every later
`use_adapters=False` scan run all five analyzers. The fast test tier paid
minutes of semgrep for tests that asked for Arbiter's own rules alone.
"""

from arbiter.engine import run_scan
from arbiter.policy import load_config
from arbiter.probes import REGISTRY, Probe


def _fake_external(calls: list) -> Probe:
    def run(ctx):
        calls.append(1)
        return []
    return Probe(name="fake-analyzer-flag", dimensions=["quality"], checks=1, run=run,
                 scope="repo", external=True)


def test_use_adapters_false_skips_a_registered_external_probe(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    calls: list = []
    probe = _fake_external(calls)
    REGISTRY.append(probe)
    try:
        rep = run_scan([str(tmp_path)], load_config(None), use_adapters=False)
        outcome = next(o for o in rep.probes if o.name == probe.name)
        assert outcome.status == "skipped"
        assert "disabled for this run" in outcome.reason
        assert calls == []

        # An explicit `only=` naming the analyzer wins over the flag: the
        # caller asked for it by name.
        rep = run_scan([str(tmp_path)], load_config(None), only=[probe.name], use_adapters=False)
        outcome = next(o for o in rep.probes if o.name == probe.name)
        assert outcome.status == "ran"
        assert calls == [1]
    finally:
        REGISTRY.remove(probe)
