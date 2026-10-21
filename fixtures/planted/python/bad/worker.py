"""Background job runner: a queue of jobs drained by a pool of threads."""
import logging
import queue
import threading

log = logging.getLogger(__name__)


class TaskQueue:
    def __init__(self):
        self.tasks = []

    def take(self):
        if len(self.tasks) > 0:  # expect: arbiter/concurrency.check-then-act-unlocked
            return self.tasks.pop(0)
        return None


class WorkerPool:
    def __init__(self, jobs: queue.Queue):
        self.jobs = jobs
        self.processed = 0

    def worker(self):
        while True:  # expect: arbiter/python.worker-loop-no-stop-signal
            job = self.jobs.get()
            try:
                job.run()
                self.processed += 1  # expect: arbiter/concurrency.unsynchronised-counter
            except Exception:  # expect: arbiter/python.swallowed-error-in-worker-loop
                pass

    def start(self, n: int):
        for _ in range(n):
            threading.Thread(target=self.worker).start()  # expect: arbiter/python.thread-not-daemon-never-joined


def call_until_ok(fn):
    while True:  # expect: arbiter/python.retry-loop-no-backoff
        try:
            return fn()
        except ConnectionError:
            continue
