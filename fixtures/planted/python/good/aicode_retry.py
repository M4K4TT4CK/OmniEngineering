"""Job retries with exponential backoff and jitter (ARB-083)."""
import random
import threading


class Queue:
    def __init__(self, max_retries: int = 3, retry_delay: float = 1.0) -> None:
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self.pending: list[str] = []

    def on_failure(self, job_id: str, attempts: int) -> None:
        if attempts <= self._max_retries:
            delay = min(30.0, self._retry_delay * 2 ** attempts) * random.uniform(0.5, 1.0)
            timer = threading.Timer(delay, self.requeue, args=(job_id,))
            timer.start()

    def requeue(self, job_id: str) -> None:
        self.pending.append(job_id)
