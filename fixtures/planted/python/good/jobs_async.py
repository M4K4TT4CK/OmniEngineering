"""In-memory job store used by the asyncio API handlers."""
import asyncio


class JobStore:
    def __init__(self, audit):
        self.jobs: dict[str, dict] = {}
        self.audit = audit
        self._lock = asyncio.Lock()

    async def submit(self, job_id: str, payload: dict) -> None:
        async with self._lock:
            self.jobs[job_id] = {"state": "queued", "payload": payload}

    async def claim(self, job_id: str) -> bool:
        async with self._lock:
            job = self.jobs[job_id]
            if job["state"] == "queued":
                job["state"] = "running"
                claimed = True
            else:
                claimed = False
        if claimed:
            await self.audit.record("claim", job_id)
        return claimed


async def drain(store: JobStore) -> None:
    for job_id in list(store.jobs):
        await store.claim(job_id)
        await asyncio.sleep(0)
