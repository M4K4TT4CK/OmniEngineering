"""Shared state touched by concurrent workers without a lock (ARB-053, ARB-054).

Each defect is the shape found in the interview test on 2026-10-20, and each
has a twin that does the same work correctly and must stay silent.
"""
from __future__ import annotations

from arbiter.engine import run_scan
from arbiter.policy import load_config

CHECK = "arbiter/concurrency.check-then-act-unlocked"
COUNTER = "arbiter/concurrency.unsynchronised-counter"


def _findings(tmp_path, source: str, name: str = "worker.py") -> list[tuple[str, int]]:
    (tmp_path / name).write_text(source, encoding="utf-8")
    rep = run_scan([str(tmp_path)], load_config(None), only=["concurrency"], use_adapters=False)
    return sorted((f.rule_id, f.location.start_line) for f in rep.active())


# --- ARB-053: check-then-act -------------------------------------------------

QUEUE = '''import threading


class TaskQueue:
    def __init__(self):
        self.tasks = []
        self._lock = threading.Lock()

    def take(self):
        if len(self.tasks) > 0:
            return self.tasks.pop(0)
        return None
'''

LOCKED_QUEUE = QUEUE.replace(
    "        if len(self.tasks) > 0:\n            return self.tasks.pop(0)\n",
    "        with self._lock:\n            if len(self.tasks) > 0:\n                return self.tasks.pop(0)\n")


def test_the_interview_queue_take_is_reported(tmp_path):
    assert _findings(tmp_path, QUEUE) == [(CHECK, 10)]


def test_the_same_take_under_its_lock_is_silent(tmp_path):
    assert "with self._lock" in LOCKED_QUEUE
    assert _findings(tmp_path, LOCKED_QUEUE) == []


def test_an_explicit_acquire_counts_as_holding_the_lock(tmp_path):
    src = QUEUE.replace("    def take(self):\n",
                        "    def take(self):\n        self._lock.acquire()\n")
    assert _findings(tmp_path, src) == []


def test_a_lock_bound_from_threading_is_recognised_whatever_its_name(tmp_path):
    src = (QUEUE.replace("self._lock = threading.Lock()", "self.guard = threading.RLock()")
           .replace("        if len(self.tasks) > 0:\n            return self.tasks.pop(0)\n",
                    "        with self.guard:\n            if self.tasks:\n                return self.tasks.pop(0)\n"))
    assert _findings(tmp_path, src) == []


def test_a_module_level_dict_reached_through_a_local_alias_is_reported(tmp_path):
    src = (
        "from concurrent.futures import ThreadPoolExecutor\n"
        "JOBS = {}\n\n\n"
        "def claim(job_id):\n"
        "    job = JOBS[job_id]\n"
        "    if job['state'] == 'queued':\n"
        "        job['state'] = 'running'\n"
        "    return job\n"
    )
    assert _findings(tmp_path, src) == [(CHECK, 7)]


def test_insert_if_absent_on_a_shared_cache_is_reported(tmp_path):
    src = (
        "import threading\n"
        "CACHE = dict()\n\n\n"
        "def put(key, value):\n"
        "    if key not in CACHE:\n"
        "        CACHE[key] = value\n"
    )
    assert _findings(tmp_path, src) == [(CHECK, 6)]


def test_a_local_collection_is_not_shared(tmp_path):
    src = (
        "import threading\n\n\n"
        "def drain(items):\n"
        "    pending = list(items)\n"
        "    if pending:\n"
        "        pending.pop()\n"
    )
    assert _findings(tmp_path, src) == []


def test_a_file_with_no_threads_is_not_reported(tmp_path):
    assert _findings(tmp_path, QUEUE.replace("import threading\n", "")
                     .replace("        self._lock = threading.Lock()\n", "")) == []


def test_async_check_then_act_is_reported_only_across_an_await(tmp_path):
    racy = (
        "import asyncio\n\n\n"
        "class Q:\n"
        "    async def take(self):\n"
        "        if self.items:\n"
        "            await asyncio.sleep(0)\n"
        "            return self.items.pop()\n"
    )
    safe = racy.replace("            await asyncio.sleep(0)\n", "")
    assert _findings(tmp_path, racy) == [(CHECK, 6)]
    assert _findings(tmp_path, safe, name="worker.py") == []


def test_tests_are_not_scanned(tmp_path):
    (tmp_path / "tests").mkdir()
    assert _findings(tmp_path, QUEUE, name="tests/test_queue.py") == []


# --- ARB-054: counters in workers ---------------------------------------------

POOL = '''import threading


class Pool:
    def __init__(self):
        self.processed = 0
        self._lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        self.processed += 1
'''


def test_a_counter_bumped_in_a_thread_target_is_reported(tmp_path):
    assert _findings(tmp_path, POOL) == [(COUNTER, 13)]


def test_the_same_counter_under_a_lock_is_silent(tmp_path):
    src = POOL.replace("        self.processed += 1\n",
                       "        with self._lock:\n            self.processed += 1\n")
    assert _findings(tmp_path, src) == []


def test_an_atomic_id_counter_is_silent(tmp_path):
    src = (POOL.replace("self.processed = 0", "self.ids = itertools.count()")
           .replace("import threading\n", "import itertools\nimport threading\n")
           .replace("        self.processed += 1\n", "        return next(self.ids)\n"))
    assert _findings(tmp_path, src) == []


def test_a_function_that_is_not_a_worker_is_silent(tmp_path):
    src = POOL.replace("target=self._work", "target=self._other")
    assert _findings(tmp_path, src) == []


def test_executor_jobs_globals_and_thread_subclasses_are_workers(tmp_path):
    src = (
        "import threading\n"
        "from concurrent.futures import ThreadPoolExecutor\n"
        "DONE = 0\n\n\n"
        "def job(n):\n"
        "    global DONE\n"
        "    DONE += 1\n\n\n"
        "class Worker(threading.Thread):\n"
        "    def run(self):\n"
        "        self.count += 1\n\n\n"
        "def main():\n"
        "    with ThreadPoolExecutor() as ex:\n"
        "        ex.submit(job, 1)\n"
    )
    assert _findings(tmp_path, src) == [(COUNTER, 8), (COUNTER, 13)]


def test_a_local_counter_in_a_worker_is_silent(tmp_path):
    src = POOL.replace("        self.processed += 1\n",
                       "        n = 0\n        n += 1\n        return n\n")
    assert _findings(tmp_path, src) == []


def test_an_async_task_counter_is_silent(tmp_path):
    """One coroutine runs at a time; `+=` with no await cannot interleave."""
    src = (
        "import asyncio\n\n\n"
        "class P:\n"
        "    async def work(self):\n"
        "        self.done += 1\n\n"
        "    def start(self, loop):\n"
        "        loop.create_task(self.work())\n"
    )
    assert _findings(tmp_path, src) == []


def test_a_plain_function_on_an_event_loop_is_not_a_thread(tmp_path):
    """anyio's own semaphore: the file imports threading for to_thread, but the
    function runs on the loop's single thread with no await inside it."""
    src = (
        "import asyncio\n"
        "import threading\n\n\n"
        "class Sem:\n"
        "    def notify(self):\n"
        "        if self._waiters:\n"
        "            self._waiters.popitem()\n\n"
        "    def start(self):\n"
        "        threading.Thread(target=self.spin).start()\n\n"
        "    def spin(self):\n"
        "        if self._waiters:\n"
        "            self._waiters.popitem()\n"
    )
    assert _findings(tmp_path, src) == [(CHECK, 14)]
