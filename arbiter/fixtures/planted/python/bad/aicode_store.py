"""An in-memory job store as model-written queues keep it (ARB-083)."""
import threading
import uuid
from dataclasses import dataclass, field


@dataclass
class Job:
    payload: dict
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "queued"


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def submit(self, payload: dict) -> Job:
        job = Job(payload)
        with self._lock:
            self._jobs[job.id] = job  # expect: arbiter/aiapp.store-never-evicted
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)
