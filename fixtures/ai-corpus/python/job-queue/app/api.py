"""HTTP API (FastAPI) for the job queue. Run with: uvicorn api:app"""

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from handlers import JOB_TYPES
from jobqueue import JobQueue, UnknownJobType


class JobRequest(BaseModel):
    type: str
    payload: dict[str, Any] = {}


def create_app(job_queue: JobQueue | None = None) -> FastAPI:
    """Build the app. A queue may be injected (used by tests); otherwise one is
    created from the environment (WORKERS, default 4)."""
    jq = job_queue or JobQueue(JOB_TYPES, workers=int(os.environ.get("WORKERS", "4")))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        jq.start()
        yield
        jq.stop()

    app = FastAPI(title="Job Queue", lifespan=lifespan)

    @app.post("/jobs", status_code=202)
    def submit_job(req: JobRequest):
        try:
            job_id = jq.submit(req.type, req.payload)
        except UnknownJobType:
            raise HTTPException(400, f"unknown job type '{req.type}'")
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {"id": job_id}

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        job = jq.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return job

    return app


app = create_app()
