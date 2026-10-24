# Job Queue

In-memory background job queue with an HTTP API (FastAPI), a pool of worker threads, and automatic retries.

## Files

- `jobqueue.py` – queue core: job store, worker threads, retry logic
- `handlers.py` – the `resize-image` and `send-email` job types
- `api.py` – HTTP API
- `test_jobqueue.py` – tests (queue, handlers, API)

## Setup and run

```
pip install -r requirements.txt
uvicorn api:app
pytest
```

## API

`POST /jobs` with `{"type": "...", "payload": {...}}` → `202 {"id": "<job id>"}`.
Unknown type → `400`; invalid payload → `422`.

`GET /jobs/{id}` → `{id, type, payload, status, attempts, result, error, created_at, updated_at}`
(`404` if unknown). `status` is `queued`, `running`, `succeeded` or `failed`.

### Job types

**resize-image** – `{"path": "/images/a.png", "width": 800, "height": 600}`
`width`/`height` are optional (default 1024) and define a bounding box; aspect ratio is kept.
Writes `a_resized.png` next to the original. Result: `{output_path, width, height}`.
If `IMAGE_ROOT` is set, paths are resolved relative to it and anything outside is rejected.
Since the API reads and writes server files, set `IMAGE_ROOT` and don't expose the service unauthenticated.

**send-email** – `{"recipient": "a@b.com", "subject": "Hi", "body": "Hello"}`

SMTP environment variables: `SMTP_HOST` (required), `SMTP_PORT` (default 587), `SMTP_USER`,
`SMTP_PASSWORD`, `SMTP_FROM` (defaults to `SMTP_USER`), `SMTP_STARTTLS` (default `true`).
Port 465 uses implicit SSL.

## Behaviour

- `WORKERS` env var sets the number of worker threads (default 4).
- A failed job is retried up to 3 times (4 attempts total) with a 1 second delay; after that it is `failed`
  and `error` holds the last exception. Jobs show `queued` while waiting for a retry.
- Jobs live only in memory and are lost on restart. Run a single uvicorn process (no `--workers`),
  otherwise each process would have its own separate queue.
- Retries re-run the whole job, so a `send-email` that fails after the server accepted the message could be sent twice.
- Finished jobs are never evicted, so memory grows with job count.
