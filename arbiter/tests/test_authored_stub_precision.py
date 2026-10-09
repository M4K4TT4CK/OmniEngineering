"""A stub is a function whose body does nothing, not a function with a `pass` somewhere in it.

FAIL-042: `store_mcp_probe_memo` in OmniEngineering's make_ai.py writes a JSON file and swallows a
failed write with `except OSError: pass`; the stub detector searched the first eight body lines for a
marker anywhere and called the function a stub. Only a marker at the body's own indentation counts.
"""
from arbiter.engine import run_scan
from arbiter.policy import load_config


def _stub_names(tmp_path, source: str) -> set[str]:
    (tmp_path / "a.py").write_text(source, encoding="utf-8")
    rep = run_scan([str(tmp_path)], load_config(None), only=["authored"], use_adapters=False)
    return {f.evidence.split(":")[1] for f in rep.active() if "stub" in f.rule_id}


def test_a_pass_nested_under_except_is_not_a_stub(tmp_path):
    names = _stub_names(tmp_path, (
        "import json\n\n\n"
        "def store_memo(path, data):\n"
        '    """Write the memo; a failed write is not a finding."""\n'
        "    if path is None:\n"
        "        return\n"
        "    try:\n"
        "        path.write_text(json.dumps(data))\n"
        "    except OSError:\n"
        "        pass\n"
    ))
    assert "store_memo" not in names


def test_a_body_that_is_only_pass_is_still_a_stub(tmp_path):
    names = _stub_names(tmp_path, (
        "def render_footer():\n    pass\n\n\n"
        "def documented_stub():\n"
        '    """Says what it will do."""\n'
        "    ...\n\n\n"
        "def multi_line_doc():\n"
        '    """One.\n\n    Two.\n    """\n'
        "    raise NotImplementedError\n"
    ))
    assert names == {"render_footer", "documented_stub", "multi_line_doc"}


def test_a_pass_under_an_if_or_with_is_not_a_stub(tmp_path):
    names = _stub_names(tmp_path, (
        "def guard(flag, lock):\n"
        "    if flag:\n"
        "        pass\n"
        "    with lock:\n"
        "        pass\n"
        "    return flag\n"
    ))
    assert "guard" not in names
