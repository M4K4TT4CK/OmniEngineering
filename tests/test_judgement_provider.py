"""The judgement provider's request body: no `temperature` for models that reject it."""

from __future__ import annotations

import json

from arbiter import judgement


def _body(model, monkeypatch):
    sent = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"content": [{"type": "text", "text": "[]"}]}).encode()

    def fake_urlopen(req, timeout=None):
        sent.update(json.loads(req.data.decode()))
        return _Resp()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(judgement.urllib.request, "urlopen", fake_urlopen)
    judgement.AnthropicProvider(model=model).complete("s", "u")
    return sent


def test_5x_models_get_no_temperature(monkeypatch):
    for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"):
        assert "temperature" not in _body(model, monkeypatch), model


def test_older_models_keep_temperature_zero(monkeypatch):
    assert _body("claude-sonnet-4-5", monkeypatch)["temperature"] == 0
    assert _body(judgement.DEFAULT_MODEL, monkeypatch).get("temperature", 0) == 0


def test_accepts_temperature_only_matches_the_5_family():
    assert not judgement.accepts_temperature("claude-opus-5-5")
    assert not judgement.accepts_temperature("claude-opus-5")
    assert judgement.accepts_temperature("claude-sonnet-4-5")
    assert judgement.accepts_temperature("claude-3-5-haiku-latest")
