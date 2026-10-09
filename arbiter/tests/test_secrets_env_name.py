"""An environment-variable NAME assigned to a credential-named symbol is not a credential.

FAIL-050: OmniEngineering's `SEMANTIC_API_KEY_ENV = "OMNI_GRAPH_SEMANTIC_API_KEY"` was reported
as a hardcoded credential by the secrets probe (and landed in code scanning). The value is the
name of the variable the key is read from; an upper-case identifier is excluded by shape, and a
symbol whose suffix says it holds a name is excluded by name.
"""
from arbiter.engine import run_scan
from arbiter.policy import load_config


def _assigned(tmp_path, source: str) -> list:
    (tmp_path / "settings.py").write_text(source, encoding="utf-8")
    rep = run_scan([str(tmp_path)], load_config(None), only=["secrets"], use_adapters=False)
    return [f for f in rep.active() if f.rule_id == "arbiter/secrets.assigned-credential"]


def test_an_env_var_name_is_not_a_credential(tmp_path):
    hits = _assigned(tmp_path, (
        'SEMANTIC_API_KEY_ENV = "OMNI_GRAPH_SEMANTIC_API_KEY"\n'
        'DB_PASSWORD_VAR = "DATABASE_PASSWORD"\n'
        'token_name = "GH_TOKEN_2024"\n'
    ))
    assert hits == []


def test_a_real_credential_next_to_it_still_fires(tmp_path):
    hits = _assigned(tmp_path, (
        'API_KEY_ENV = "OMNI_GRAPH_SEMANTIC_API_KEY"\n'
        'api_key = "Qz9vLm2rT7pX1kW4nB8dH3jF6sY0cA5e"\n'
    ))
    assert [h.evidence.split("=")[0] for h in hits] == ["api_key"]
