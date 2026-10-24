"""Job retries as model-written queues schedule them (ARB-083)."""
import threading


class Queue:
    def __init__(self, max_retries: int = 3, retry_delay: float = 1.0) -> None:
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self.pending: list[str] = []

    def on_failure(self, job_id: str, attempts: int) -> None:
        if attempts <= self._max_retries:
            timer = threading.Timer(self._retry_delay, self.requeue, args=(job_id,))  # expect: arbiter/python.retry-constant-delay
            timer.start()

    def requeue(self, job_id: str) -> None:
        self.pending.append(job_id)
