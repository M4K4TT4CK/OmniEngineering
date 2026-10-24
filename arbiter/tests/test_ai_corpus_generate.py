"""The AI-corpus generator (tools/ai_corpus_generate.py): the held-out task
set, model alternation over the grid, the spend cap and install. No model is
called here; `generate` is the only function that runs `claude`, and it is
not exercised."""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from collections import Counter

from helpers import ROOT

_spec = importlib.util.spec_from_file_location("ai_corpus_generate", ROOT / "tools" / "ai_corpus_generate.py")
gen = importlib.util.module_from_spec(_spec)
sys.modules["ai_corpus_generate"] = gen
_spec.loader.exec_module(gen)


def test_heldout_tasks_are_new_and_neutral():
    assert len(gen.HELDOUT_TASKS) == 8
    assert not set(gen.HELDOUT_TASKS) & set(gen.TASKS)
    for lang in gen.LANGUAGES:
        for task in gen.HELDOUT_TASKS:
            prompt = gen.prompt_for(lang, task, gen.HELDOUT_TASKS).lower()
            for word in ("secur", "vulnerab", "review", "harden", "safe", "attack"):
                assert word not in prompt, (lang, task, word)


def test_two_models_alternate_so_each_language_and_task_gets_both():
    models = ["opus", "haiku"]
    grid = {(l, t): gen.model_for(l, t, gen.HELDOUT_TASKS, models)
            for l in gen.LANGUAGES for t in gen.HELDOUT_TASKS}
    assert Counter(grid.values()) == {"opus": 20, "haiku": 20}
    for lang in gen.LANGUAGES:
        assert {m for (l, _), m in grid.items() if l == lang} == set(models)
    for task in gen.HELDOUT_TASKS:
        assert {m for (_, t), m in grid.items() if t == task} == set(models)


def test_command_is_file_tools_only_with_no_project_settings():
    cmd = gen.command("p", "haiku", 2.0)
    assert cmd[:3] == ["claude", "-p", "p"]
    tools = cmd[cmd.index("--tools") + 1:cmd.index("--allowedTools")]
    assert set(tools) == {"Read", "Write", "Edit", "Glob"}
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--max-budget-usd") + 1] == "2.0"
    assert "--max-budget-usd" not in gen.command("p", "haiku")


def test_budget_refuses_a_run_that_could_pass_the_cap():
    b = gen.Budget(cap=5.0, per_run=2.0)
    assert b.start() and b.start()          # 0 spent + 2 in flight * 2 = 4 <= 5
    started = []
    t = threading.Thread(target=lambda: started.append(b.start()))
    t.start()
    t.join(0.2)
    assert t.is_alive(), "a third run waits while two are in flight"
    b.finish(0.5)                            # 0.5 + 2 * 2 = 4.5 <= 5: the waiter starts
    t.join(2)
    assert started == [True]
    b.finish(1.9)
    b.finish(1.9)                            # spent 4.3, nothing in flight
    assert not b.start()                     # 4.3 + 2 > 5
    assert gen.Budget(None, None).start()


def test_install_copies_apps_unchanged_and_writes_the_manifest(tmp_path, monkeypatch):
    out, corpus = tmp_path / "gen", tmp_path / "corpus"
    (out / "go" / "chat-relay").mkdir(parents=True)
    (out / "go" / "chat-relay" / "main.go").write_text("package main\n")
    (corpus / "go" / "chat-relay").mkdir(parents=True)
    (corpus / "go" / "chat-relay" / "labels.json").write_text("{}")
    log = [{"language": "go", "task": "chat-relay", "prompt": "Build x", "model_alias": "haiku",
            "model": "claude-haiku-x", "date": "2026-10-23", "exit_code": 0, "cost_usd": 0.01,
            "files": ["main.go"]},
           {"language": "go", "task": "csv-import", "error": "budget"}]
    assert gen.install(out, corpus, log) == ["go/chat-relay"]
    assert (corpus / "go" / "chat-relay" / "app" / "main.go").read_text() == "package main\n"
    assert (corpus / "go" / "chat-relay" / "labels.json").read_text() == "{}"
    manifest = json.loads((corpus / "manifest.json").read_text())
    assert [a["task"] for a in manifest["apps"]] == ["chat-relay"]
    assert manifest["apps"][0]["model"] == "claude-haiku-x" and "exit_code" not in manifest["apps"][0]


def test_the_committed_heldout_manifest_records_both_models_and_every_prompt():
    manifest = json.loads((ROOT / "fixtures" / "ai-corpus-heldout" / "manifest.json").read_text())
    apps = manifest["apps"]
    assert len(apps) == 40
    assert {a["model_alias"] for a in apps} == {"opus", "haiku"}
    for a in apps:
        assert a["prompt"] == gen.prompt_for(a["language"], a["task"], gen.HELDOUT_TASKS)
        assert a["model"].startswith("claude-") and a["date"]


def test_heldout_seal_lists_exactly_the_heldout_tree():
    seal = json.loads((ROOT / "tools" / "gold" / "heldout.json").read_text())["ai_corpus_heldout"]
    corpus = ROOT / seal["path"]
    have = sorted(p.parent.relative_to(corpus).as_posix() for p in corpus.glob("*/*/app"))
    assert sorted(seal["apps"]) == have
    assert sorted(seal["apps"]) == sorted(f"{l}/{t}" for l in gen.LANGUAGES for t in gen.HELDOUT_TASKS)
