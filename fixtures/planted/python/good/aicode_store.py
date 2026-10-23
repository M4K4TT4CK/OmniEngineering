"""An in-memory job store that forgets finished jobs (ARB-083)."""
import threading
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Job:
    payload: dict
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "queued"
    finished_at: float | None = None


class JobStore:
    RETAIN_SECONDS = 3600

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def submit(self, payload: dict) -> Job:
        job = Job(payload)
        with self._lock:
            self._drop_finished()
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _drop_finished(self) -> None:
        horizon = time.time() - self.RETAIN_SECONDS
        for job_id in [k for k, j in self._jobs.items() if j.finished_at and j.finished_at < horizon]:
            del self._jobs[job_id]
