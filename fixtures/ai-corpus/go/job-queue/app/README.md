# Job Queue

An in-memory background job queue with an HTTP API, written in Go (standard library only, Go 1.22+).

- `main.go` – HTTP API and startup
- `queue.go` – queue, worker pool, retry logic
- `jobs.go` – the `resize-image` and `send-email` job types
- `queue_test.go` – tests

## Run

    go run .
    go test ./...

## Configuration (environment)

| Variable | Default | Purpose |
|---|---|---|
| `ADDR` | `:8080` | Listen address |
| `WORKERS` | `4` | Worker goroutines |
| `QUEUE_SIZE` | `1000` | Max jobs waiting; further submissions get `503` |
| `IMAGE_ROOT` | `.` | Directory that `resize-image` may read and write; paths outside it (including via symlinks) are rejected |
| `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` | – | SMTP settings for `send-email` (`SMTP_FROM` defaults to `SMTP_USER`) |

## API

`POST /jobs` with `{"type": "...", "payload": {...}}` returns `202 {"id": "..."}`.
Unknown types or invalid payloads return `400`.

`GET /jobs/{id}` returns the job: `status` (`queued`, `running`, `completed`, `failed`),
`attempts`, `result`, `error`, and timestamps. Unknown ids return `404`.

### Job types

    curl -X POST localhost:8080/jobs -d '{"type":"resize-image","payload":{"path":"photo.jpg","width":200}}'
    curl -X POST localhost:8080/jobs -d '{"type":"send-email","payload":{"to":"a@example.com","subject":"Hi","body":"Hello"}}'
    curl localhost:8080/jobs/<id>

- `resize-image`: `path`, `width`, optional `height` (0 keeps aspect ratio). Supports PNG, JPEG, GIF input. Writes `<name>-resized.<ext>` beside the original (GIFs become PNG). Uses nearest-neighbour scaling, so quality is basic.
- `send-email`: `to`, `subject`, `body` (plain text) sent via `net/smtp`.

## Behaviour and limitations

- A failed (or panicking) job is retried up to 3 times (4 attempts total) with a 2 s delay; after that it is `failed`.
- Jobs and results live in memory only: they are lost on restart and never evicted.
- Retries are not guaranteed to run on shutdown; pending work is dropped when the process stops.
- The API has no authentication; run it on a trusted network.
