"""assurance.blanket-suppression and gosec's `#nosec Gxxx` (ARB-065).

`// #nosec G115 -- reason` names a rule; a bare `#nosec` still does not.
"""
from __future__ import annotations

from arbiter.engine import run_scan
from arbiter.policy import load_config


def _scan(tmp_path, files: dict[str, str], probe: str):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    rep = run_scan([str(tmp_path)], load_config(None), only=[probe], use_adapters=False,
                   use_cache=False)
    return rep.active()


# --- assurance.blanket-suppression ------------------------------------------------

def test_gosec_nosec_naming_a_rule_is_not_blanket(tmp_path):
    found = [f for f in _scan(tmp_path, {
        "go.mod": "module m\n\ngo 1.22\n",
        "a.go": ("package m\n\nfunc f(n int) uint16 {\n"
                 "\treturn uint16(n) // #nosec G115 -- bounded by the check above\n}\n\n"
                 "func g(n int) uint16 {\n\treturn uint16(n) // #nosec G115 G602\n}\n\n"
                 "func h(n int) uint16 {\n\treturn uint16(n) // #nosec\n}\n"),
    }, "assurance") if f.rule_id == "arbiter/assurance.blanket-suppression"]
    assert [(f.location.start_line, f.evidence.split(":")[0]) for f in found] == [(12, "gosec")]
