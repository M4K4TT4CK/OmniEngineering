"""Background job runner: a queue of jobs drained by a pool of threads."""
import json
import logging
import queue
import random
import threading
import time

log = logging.getLogger(__name__)


class TaskQueue:
    def __init__(self):
        self.tasks = []
        self._lock = threading.Lock()

    def take(self):
        with self._lock:
            if len(self.tasks) > 0:
                return self.tasks.pop(0)
        return None


class WorkerPool:
    def __init__(self, jobs: queue.Queue):
        self.jobs = jobs
        self.processed = 0
        self.failed = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def worker(self):
        while not self._stop.is_set():
            try:
                job = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                job.run()
                with self._lock:
                    self.processed += 1
            except Exception:
                log.exception("job %s failed", getattr(job, "id", "?"))
                with self._lock:
                    self.failed += 1
            finally:
                self.jobs.task_done()

    def start(self, n: int):
        for _ in range(n):
            t = threading.Thread(target=self.worker, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)


def call_until_ok(fn, attempts: int = 5, base_delay: float = 0.1):
    delay = base_delay
    while True:
        try:
            return fn()
        except ConnectionError:
            attempts -= 1
            if attempts == 0:
                raise
            time.sleep(delay + random.uniform(0, delay / 2))
            delay *= 2


def wait_for_reply(lines: queue.Queue, request_id: int, timeout: float) -> dict:
    # Bounded by a deadline and blocking on the queue: not a retry loop.
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError from exc
        if line is None:
            raise EOFError
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue  # a log line on stdout; not ours
        if isinstance(message, dict) and message.get("id") == request_id:
            return message
