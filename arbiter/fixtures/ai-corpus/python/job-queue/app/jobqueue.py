"""In-memory job queue with a pool of worker threads and bounded retries."""

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"


@dataclass
class JobType:
    """A handler plus an optional validator that raises ValueError on bad payloads."""

    handler: Callable[[dict], Any]
    validate: Callable[[dict], None] = lambda payload: None


@dataclass
class Job:
    id: str
    type: str
    payload: dict
    status: str = QUEUED
    attempts: int = 0
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class UnknownJobType(Exception):
    pass


class JobQueue:
    def __init__(self, job_types: dict[str, JobType], workers: int = 4,
                 max_retries: int = 3, retry_delay: float = 1.0):
        self._types = job_types
        self._num_workers = workers
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._timers: set[threading.Timer] = set()
        self._stopped = False

    def start(self) -> None:
        for i in range(self._num_workers):
            t = threading.Thread(target=self._worker, name=f"worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        """Stop accepting work, drop pending retries, and wait for running jobs to finish."""
        with self._lock:
            self._stopped = True
            timers = list(self._timers)
            self._timers.clear()
        for timer in timers:
            timer.cancel()
        for _ in self._threads:
            self._queue.put(None)
        for t in self._threads:
            t.join()
        self._threads.clear()

    def submit(self, job_type: str, payload: dict) -> str:
        """Validate and enqueue a job. Raises UnknownJobType or ValueError."""
        spec = self._types.get(job_type)
        if spec is None:
            raise UnknownJobType(job_type)
        spec.validate(payload)
        job = Job(id=uuid.uuid4().hex, type=job_type, payload=payload)
        with self._lock:
            if self._stopped:
                raise RuntimeError("queue is stopped")
            self._jobs[job.id] = job
        self._queue.put(job.id)
        return job.id

    def get(self, job_id: str) -> dict | None:
        """Return a snapshot of the job as a dict, or None if unknown."""
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(vars(job)) if job else None

    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            self._run(job_id)

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = RUNNING
            job.attempts += 1
            job.updated_at = time.time()
            handler = self._types[job.type].handler
            payload = job.payload
        try:
            result = handler(payload)
        except Exception as exc:  # any handler failure is retryable
            self._on_failure(job_id, f"{type(exc).__name__}: {exc}")
        else:
            with self._lock:
                job.status = SUCCEEDED
                job.result = result
                job.error = None
                job.updated_at = time.time()

    def _on_failure(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.error = error
            job.updated_at = time.time()
            # attempts counts the initial run, so retries = attempts - 1
            if job.attempts - 1 < self._max_retries and not self._stopped:
                job.status = QUEUED
                timer = threading.Timer(self._retry_delay, self._requeue, args=(job_id,))
                timer.daemon = True
                self._timers.add(timer)
                timer.start()
            else:
                job.status = FAILED

    def _requeue(self, job_id: str) -> None:
        with self._lock:
            if self._stopped:
                return
            self._timers.discard(threading.current_thread())
        self._queue.put(job_id)
