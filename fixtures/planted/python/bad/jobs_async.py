"""In-memory job store used by the asyncio API handlers."""
import asyncio


class JobStore:
    def __init__(self, audit):
        self.jobs: dict[str, dict] = {}
        self.audit = audit

    async def submit(self, job_id: str, payload: dict) -> None:
        self.jobs[job_id] = {"state": "queued", "payload": payload}

    async def claim(self, job_id: str) -> bool:
        job = self.jobs[job_id]
        if job["state"] == "queued":  # expect: arbiter/concurrency.check-then-act-unlocked
            await self.audit.record("claim", job_id)
            job["state"] = "running"
            return True
        return False


async def drain(store: JobStore) -> None:
    for job_id in list(store.jobs):
        await store.claim(job_id)
        await asyncio.sleep(0)
