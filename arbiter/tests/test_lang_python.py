"""The Python rule pack (ARB-061): shapes the planted corpus does not pin down.

The corpus (fixtures/planted/python) proves every rule fires on its defect and
stays silent on the correct twin. These tests pin the precision decisions
made against real code -- requests, flask, httpx and pygoat -- so a later
edit that widens a rule shows up here, not in a reviewer's inbox.
"""
from __future__ import annotations

import pytest
from helpers import _scan_text

from arbiter import ast as ts
from arbiter import probes  # noqa: F401 - registers the packs before lang_rules is imported
from arbiter.lang_rules import load_packs

PACK = "python"
RULES = {
    "http-no-timeout", "http-timeout-disabled", "retry-loop-no-backoff",
    "swallowed-error-in-worker-loop", "worker-loop-no-stop-signal",
    "thread-not-daemon-never-joined", "sql-injection", "command-injection",
    "code-injection", "unsafe-deserialization", "insecure-random-for-secret",
    "weak-password-hash",
}

pytestmark = pytest.mark.skipif(not ts.available(), reason="tree-sitter not installed")


def rules_in(tmp_path, code: str, name: str = "service.py") -> set[str]:
    found = _scan_text(tmp_path, name, code, only=["lang_python"])
    return {f.rule_id.removeprefix("arbiter/python.") for f in found}


def test_pack_has_every_rule_with_full_metadata():
    pack = load_packs()[PACK]
    by_id = {r["id"]: r for r in pack["rules"]}
    assert set(by_id) == RULES
    for rule in by_id.values():
        for key in ("title", "severity", "confidence", "cwe", "description", "remediation", "tags"):
            assert rule.get(key), f"{rule['id']} lacks {key}"
        assert all(c.startswith("CWE-") for c in rule["cwe"])


# --- outbound HTTP ---------------------------------------------------------

def test_http_timeout_passed_through_kwargs_is_not_reported(tmp_path):
    assert "http-no-timeout" not in rules_in(tmp_path, (
        "import requests\n"
        "def call(url, **kw):\n"
        "    kw.setdefault('timeout', 5)\n"
        "    return requests.get(url, **kw)\n"))


def test_session_calls_are_out_of_scope(tmp_path):
    # Precision over recall: a Session may carry its own adapter timeout.
    assert "http-no-timeout" not in rules_in(tmp_path, (
        "import requests\n"
        "s = requests.Session()\n"
        "def call(url):\n"
        "    return s.get(url)\n"))


def test_two_disabled_timeouts_in_one_file_are_two_findings(tmp_path):
    # Evidence is the hit text; the hit is the whole call so two different
    # calls do not share a fingerprint and collapse.
    found = _scan_text(tmp_path, "c.py", (
        "import httpx\n"
        "a = httpx.Client(timeout=None)\n"
        "b = httpx.AsyncClient(base_url='https://x', timeout=None)\n"), only=["lang_python"])
    assert sorted(f.location.start_line for f in found
                  if f.rule_id.endswith("http-timeout-disabled")) == [2, 3]


def test_httpx_default_timeout_is_fine(tmp_path):
    assert not rules_in(tmp_path, "import httpx\nc = httpx.Client()\nr = httpx.get('https://x')\n")


# --- retry and worker loops ------------------------------------------------

def test_prompt_loop_is_not_a_retry_loop(tmp_path):
    # pygoat/PyGoatBot.py: `input()` blocks on a human, so no busy loop.
    assert "retry-loop-no-backoff" not in rules_in(tmp_path, (
        "while True:\n"
        "    try:\n"
        "        n = int(input('number: '))\n"
        "        break\n"
        "    except ValueError:\n"
        "        print('again')\n"))


def test_queue_poll_loop_is_not_a_retry_loop(tmp_path):
    # No exit on success inside the try: this drains a queue, it does not retry.
    assert "retry-loop-no-backoff" not in rules_in(tmp_path, (
        "import queue\n"
        "def drain(q, handle):\n"
        "    while True:\n"
        "        try:\n"
        "            item = q.get_nowait()\n"
        "        except queue.Empty:\n"
        "            continue\n"
        "        handle(item)\n"))


def test_redis_optimistic_transaction_retry_is_not_flagged(tmp_path):
    # rq/worker/base.py: WATCH/MULTI retried at once on WatchError, as Redis intends.
    assert "retry-loop-no-backoff" not in rules_in(tmp_path, (
        "from redis import WatchError\n"
        "def save(pipe, key):\n"
        "    while True:\n"
        "        try:\n"
        "            pipe.watch(key)\n"
        "            pipe.multi()\n"
        "            pipe.execute()\n"
        "            break\n"
        "        except WatchError:\n"
        "            continue\n"))
    # The same loop catching anything else is still a busy retry.
    assert "retry-loop-no-backoff" in rules_in(tmp_path, (
        "def save(pipe, key):\n"
        "    while True:\n"
        "        try:\n"
        "            pipe.execute()\n"
        "            break\n"
        "        except ConnectionError:\n"
        "            continue\n"))


def test_deadline_bounded_blocking_wait_is_not_a_retry_loop(tmp_path):
    # make_ai.py `wait_for` in probe_mcp_server: a deadline, a blocking
    # `get(timeout=remaining)`, an except that re-raises and one that skips a
    # line. The seed rule reported it; none of its retry signals are present.
    assert "retry-loop-no-backoff" not in rules_in(tmp_path, (
        "import json, queue, time\n"
        "def wait_for(lines, request_id, deadline):\n"
        "    while True:\n"
        "        remaining = deadline - time.monotonic()\n"
        "        if remaining <= 0:\n"
        "            raise TimeoutError\n"
        "        try:\n"
        "            line = lines.get(timeout=remaining)\n"
        "        except queue.Empty as exc:\n"
        "            raise TimeoutError from exc\n"
        "        try:\n"
        "            message = json.loads(line)\n"
        "        except json.JSONDecodeError:\n"
        "            continue\n"
        "        if message.get('id') == request_id:\n"
        "            return message\n"))


def test_retry_with_per_attempt_timeout_but_no_backoff_is_reported(tmp_path):
    assert "retry-loop-no-backoff" in rules_in(tmp_path, (
        "import requests\n"
        "def fetch(url):\n"
        "    while True:\n"
        "        try:\n"
        "            return requests.get(url, timeout=5)\n"
        "        except requests.ConnectionError:\n"
        "            logger.warning('retrying')\n"))


def test_swallowed_error_needs_a_worker_module(tmp_path):
    code = (
        "{imp}"
        "def run(items):\n"
        "    for item in items:\n"
        "        try:\n"
        "            handle(item)\n"
        "        except Exception:\n"
        "            continue\n")
    assert "swallowed-error-in-worker-loop" not in rules_in(tmp_path, code.format(imp=""))
    assert "swallowed-error-in-worker-loop" in rules_in(
        tmp_path, code.format(imp="from concurrent.futures import ThreadPoolExecutor\n"))


def test_specific_exception_swallowed_is_not_reported(tmp_path):
    assert "swallowed-error-in-worker-loop" not in rules_in(tmp_path, (
        "import threading\n"
        "def run(q):\n"
        "    while True:\n"
        "        try:\n"
        "            q.get_nowait()\n"
        "        except KeyError:\n"
        "            pass\n"))


def test_worker_loop_with_sentinel_return_has_a_stop(tmp_path):
    code = (
        "import threading\n"
        "def worker(q):\n"
        "    while True:\n"
        "        job = q.get()\n"
        "        {exit}\n"
        "        job.run()\n")
    assert "worker-loop-no-stop-signal" in rules_in(tmp_path, code.format(exit="pass"))
    assert "worker-loop-no-stop-signal" not in rules_in(
        tmp_path, code.format(exit="if job is None:\n            return"))


def test_worker_loop_rule_is_scoped_to_worker_names_and_threaded_modules(tmp_path):
    loop = "def {name}():\n    while True:\n        tick()\n"
    assert "worker-loop-no-stop-signal" not in rules_in(tmp_path, "import threading\n" + loop.format(name="main"))
    assert "worker-loop-no-stop-signal" not in rules_in(tmp_path, loop.format(name="worker"))
    assert "worker-loop-no-stop-signal" in rules_in(tmp_path, "import threading\n" + loop.format(name="worker"))


def test_joined_or_daemon_threads_are_fine(tmp_path):
    base = "import threading\nt = threading.Thread(target=print{daemon})\nt.start()\n{join}"
    assert "thread-not-daemon-never-joined" in rules_in(tmp_path, base.format(daemon="", join=""))
    assert "thread-not-daemon-never-joined" not in rules_in(tmp_path, base.format(daemon=", daemon=True", join=""))
    assert "thread-not-daemon-never-joined" not in rules_in(tmp_path, base.format(daemon="", join="t.join(timeout=5)\n"))
    # A string join is not a thread join.
    assert "thread-not-daemon-never-joined" in rules_in(tmp_path, base.format(daemon="", join="', '.join(['a'])\n"))


# --- injection -------------------------------------------------------------

def test_sql_literal_concatenation_and_parameters_are_fine(tmp_path):
    assert "sql-injection" not in rules_in(tmp_path, (
        "def f(conn, uid):\n"
        "    conn.execute('SELECT * FROM t ' + 'WHERE id = ?', (uid,))\n"
        "    conn.execute('SELECT * FROM t WHERE id = %s', (uid,))\n"
        "    msg = f'select {uid} rows'\n"
        "    label = 'Update {}'.format(uid)\n"))


def test_sql_built_into_a_variable_is_reported_wherever_it_is_built(tmp_path):
    assert "sql-injection" in rules_in(tmp_path, "def q(table):\n    return f'select id from {table} where x = 1'\n")
    assert "sql-injection" in rules_in(tmp_path, "def q(n):\n    return 'INSERT INTO t VALUES (%s)' % n\n")
    assert "sql-injection" in rules_in(tmp_path, "def q(n):\n    return 'DELETE FROM t WHERE id = ' + str(n)\n")


def test_a_regex_that_parses_sql_is_not_a_query(tmp_path):
    # omni_graph.py builds SQL-shaped regexes to read schema files; no query runs.
    assert "sql-injection" not in rules_in(tmp_path, (
        "import re\n"
        "NAME = r'(\\w+)'\n"
        "def f(stmt):\n"
        "    return re.match(r'CREATE\\s+TABLE\\s+' + NAME + r'\\s*(.*)$', stmt)\n"))
    # The same string handed to a cursor is still reported.
    assert "sql-injection" in rules_in(tmp_path, "def f(cur, n):\n    cur.execute('CREATE TABLE ' + n)\n")


def test_shell_with_literal_or_quoted_command_is_fine(tmp_path):
    assert "command-injection" not in rules_in(tmp_path, (
        "import os, shlex, subprocess\n"
        "subprocess.run('ls -l | wc -l', shell=True)\n"
        "subprocess.run(['ls', path])\n"
        "os.system('logrotate /etc/logrotate.conf')\n"
        "subprocess.check_output('grep ' + shlex.quote(p) + ' f', shell=True)\n"))


def test_shell_true_from_bare_import_is_reported(tmp_path):
    assert "command-injection" in rules_in(tmp_path, "from subprocess import run\nrun(cmd, shell=True)\n")


def test_compiled_code_loading_is_not_code_injection(tmp_path):
    # flask/config.py from_pyfile: deliberate loading of an operator-owned file.
    assert "code-injection" not in rules_in(tmp_path, (
        "def load(path, d):\n"
        "    with open(path, 'rb') as f:\n"
        "        exec(compile(f.read(), path, 'exec'), d)\n"
        "    eval('1 + 1')\n"))
    assert "code-injection" in rules_in(tmp_path, "def f(expr):\n    return eval(expr)\n")


def test_yaml_safe_loader_positional_or_keyword_is_fine(tmp_path):
    assert "unsafe-deserialization" not in rules_in(tmp_path, (
        "import yaml\n"
        "a = yaml.load(s, yaml.SafeLoader)\n"
        "b = yaml.load(s, Loader=yaml.CSafeLoader)\n"
        "c = yaml.safe_load(s)\n"))
    assert "unsafe-deserialization" in rules_in(tmp_path, "import yaml\na = yaml.load(s, Loader=yaml.Loader)\n")


# --- cryptography ------------------------------------------------------------

def test_random_is_only_reported_for_secret_shaped_names(tmp_path):
    assert "insecure-random-for-secret" not in rules_in(tmp_path, (
        "import random\n"
        "worker = random.choice(WORKERS)\n"
        "jitter = random.uniform(0, 1)\n"
        "token = random.SystemRandom().choice(ALPHABET)\n"))
    assert "insecure-random-for-secret" in rules_in(tmp_path, (
        "import random\n"
        "def make():\n"
        "    api_key = hex(random.getrandbits(128))\n"
        "    return api_key\n"))


def test_weak_digest_is_only_reported_for_passwords(tmp_path):
    assert "weak-password-hash" not in rules_in(tmp_path, (
        "import hashlib\n"
        "def etag(body):\n"
        "    return hashlib.sha1(body).hexdigest()\n"
        "def hash_password(pw):\n"
        "    return hashlib.scrypt(pw.encode(), salt=b'x', n=2**14, r=8, p=1)\n"))
    found = rules_in(tmp_path, (
        "import hashlib\n"
        "def store(user, password):\n"
        "    user.digest = hashlib.new('md5', password.encode()).hexdigest()\n"))
    assert "weak-password-hash" in found


def test_pack_rules_skip_test_files(tmp_path):
    assert not rules_in(tmp_path, "import pickle\nx = pickle.loads(b)\n", name="tests/test_x.py")
