"""HTTP surface: FastAPI routes over the job store and the admin tasks."""
import sqlite3

from fastapi import FastAPI, HTTPException

import auth
import db
import shell_tasks

app = FastAPI()
conn = sqlite3.connect("jobs.db", check_same_thread=False)


@app.get("/jobs/{job_id}")
def read_job(job_id: str) -> dict:
    row = db.get_job(conn, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such job")
    return {"id": row[0], "status": row[1]}


@app.post("/sessions")
def create_session() -> dict:
    return {"token": auth.new_session_token()}


@app.post("/admin/archive")
def archive(directory: str) -> dict:
    shell_tasks.archive_logs(directory)
    return {"ok": True}
