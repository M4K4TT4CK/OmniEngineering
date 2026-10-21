"""authored.stub-on-production-path and stubs by design (ARB-065).

Abstract base methods and "not supported by this backend" overrides are
declarations; a bare stub and an always-yes `return True  # TODO` are not.
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


# --- authored.stub-on-production-path ---------------------------------------------

def test_abstract_and_not_supported_methods_are_stubs_by_design(tmp_path):
    """celery/backends/rpc.py:412 and celery/concurrency/base.py:106."""
    found = _scan(tmp_path, {"pkg/backend.py": (
        "import abc\n\n\n"
        "class BasePool:\n    def on_apply(self, *args, **kwargs):\n        pass\n\n\n"
        "class RPCBackend:\n    def save_group(self, group_id, result):\n"
        "        raise NotImplementedError(\n"
        "            'save_group is not supported by this backend.')\n\n"
        "    def render(self):\n        raise NotImplementedError\n\n"
        "    def verify_token(self, token):\n        return True  # TODO real check\n\n\n"
        "class Store(abc.ABC):\n    def get(self, key):\n        ...\n\n\n"
        "class Hook:\n    def fire(self):\n        pass\n\n\n"
        "class Loud(Hook):\n    @abc.abstractmethod\n    def run(self):\n        pass\n\n"
        "    def save(self):\n        \"\"\"Subclasses must implement this.\"\"\"\n"
        "        raise NotImplementedError\n\n\n"
        "def top_level():\n    pass\n")}, "authored")
    names = {f.evidence.split(":")[1] for f in found
             if f.rule_id == "arbiter/authored.stub-on-production-path"}
    assert names == {"render", "verify_token", "top_level"}


def test_explained_hooks_and_docstring_examples_are_not_stubs(tmp_path):
    """celery's rpc backend explains `_forget` in a comment; `on_*` hooks are
    empty by convention; django's debug.py has `def my_view` in a docstring.
    A `pass  # TODO` is unfinished, not explained."""
    found = _scan(tmp_path, {"pkg/mod.py": (
        'def sensitive_variables(*variables):\n'
        '    """\n    Example::\n\n        def my_view(request):\n            pass\n    """\n'
        '    return variables\n\n\n'
        "class Backend:\n"
        "    def _forget(self, task_id):\n"
        "        # results are messages on the reply queue, nothing stored server-side.\n"
        "        pass\n\n"
        "    def on_consumer_ready(self, consumer):\n        pass\n\n"
        "    def ensure_chords_allowed(self):\n        raise NotImplementedError(E_NO_CHORD.strip())\n\n"
        "    def touch(self, key):\n        pass  # TODO\n\n"
        "    def on_authorize(self, request):\n        pass\n")}, "authored")
    names = {f.evidence.split(":")[1] for f in found
             if f.rule_id == "arbiter/authored.stub-on-production-path"}
    # A hook whose name says it decides access is still reported.
    assert names == {"touch", "on_authorize"}
