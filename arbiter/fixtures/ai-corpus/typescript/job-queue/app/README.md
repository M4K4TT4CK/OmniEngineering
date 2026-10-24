# job-queue

In-memory background job queue: an HTTP API in front of a pool of Node `worker_threads`.

## Usage

```sh
npm install
npm run build
npm start        # listens on PORT (default 3000)
npm test
```

Configuration (environment):

| Variable | Purpose |
| --- | --- |
| `PORT` | HTTP port (default 3000) |
| `WORKERS` | Worker threads (default 4) |
| `IMAGE_ROOT` | Optional. If set, `resize-image` only accepts paths inside this directory |
| `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`, `SMTP_SECURE` | SMTP settings for `send-email` (`SMTP_FROM` defaults to `SMTP_USER`; TLS is on by default for port 465) |

## API

`POST /jobs` with `{"type": "...", "payload": {...}}` returns `202 {"id": "<uuid>"}`.

- `resize-image`: `{"path": "/data/photo.jpg", "width": 800}` (`width` optional, never enlarges). Writes `photo-resized.jpg` next to the original; the result holds `outputPath`, `width`, `height`, `size`.
- `send-email`: `{"to": "a@example.com", "subject": "Hi", "body": "Plain text"}`. The result holds `messageId` and `accepted`.

`GET /jobs/:id` returns `id`, `type`, `status` (`queued`, `running`, `completed`, `failed`), `attempts`, `result`, `error`, `createdAt`, `updatedAt`. Unknown ids return 404; invalid submissions return 400.

```sh
curl -X POST localhost:3000/jobs -H 'Content-Type: application/json' \
  -d '{"type":"send-email","payload":{"to":"a@example.com","subject":"Hi","body":"Hello"}}'
curl localhost:3000/jobs/<id>
```

## Behaviour

- Each worker runs one job at a time; extra jobs wait in a FIFO queue.
- A failed attempt (handler error or worker thread crash) is retried up to 3 times, so a job runs at most 4 times. Retry `n` waits `n` seconds. While waiting, the job is `queued` and `error` holds the last failure. After the last failure it becomes `failed`.
- A crashed worker thread is replaced.

## Limitations

- Jobs live in memory only: they are lost on restart and never evicted, so memory grows with job count.
- There is no authentication. `resize-image` reads and writes server file paths chosen by the caller, so set `IMAGE_ROOT` and don't expose the API untrusted. `IMAGE_ROOT` does not resolve symlinks.
- Retries apply to every error, including permanent ones such as a missing file.
- Tests use a fake worker script (`test/fake-worker.js`) with the same message protocol as `src/worker.ts`, so the real image and email handlers are not covered by tests.
- Nothing here has been installed, built, or run yet.

## Layout

- `src/jobs.ts`: types and payload validation
- `src/queue.ts`: queue, retries, worker pool
- `src/worker.ts`, `src/handlers.ts`: worker thread entry and job handlers (sharp, nodemailer)
- `src/server.ts`: HTTP API and entry point
- `test/`: vitest tests and the fake worker
