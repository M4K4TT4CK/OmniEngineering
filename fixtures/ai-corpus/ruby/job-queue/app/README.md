# Ruby Job Queue

In-memory background job queue with an HTTP API (Sinatra + Puma) and a pool of worker threads.
Jobs are lost on restart.

## Files

- `job_queue.rb` – `JobQueue` (worker pool, retries, status) and the `resize-image` / `send-email` handlers
- `app.rb` – HTTP API
- `config.ru` – boots the queue and the app
- `test_job_queue.rb` – tests

## Setup and run

```
bundle install
bundle exec rackup -p 9292     # or: bundle exec puma config.ru
ruby test_job_queue.rb         # tests
```

`resize-image` needs ImageMagick installed (`convert`/`magick`).
Run a single Puma process (the default): the queue lives in process memory.

## Configuration (environment)

| Variable | Purpose |
|---|---|
| `WORKERS` | worker threads (default 4) |
| `IMAGE_ROOT` | if set, `resize-image` only accepts files under this directory (recommended) |
| `SMTP_ADDRESS`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_STARTTLS` (true), `SMTP_FROM` | SMTP settings for `send-email` |

## API

`POST /jobs` → `202 {"id": "<uuid>"}` (`400` bad JSON, `422` unknown type or invalid payload)

```
curl -X POST localhost:9292/jobs -H 'Content-Type: application/json' \
  -d '{"type":"send-email","payload":{"to":"a@example.com","subject":"Hi","body":"Hello"}}'

curl -X POST localhost:9292/jobs -H 'Content-Type: application/json' \
  -d '{"type":"resize-image","payload":{"path":"/data/photo.jpg","width":200,"height":200}}'
```

`GET /jobs/:id` → `200` with `id, type, status, attempts, result, error, created_at, updated_at` (`404` if unknown).
`status` is `queued`, `running`, `completed` or `failed`.

## Job types

- `resize-image`: `path` plus `width` and/or `height`. Writes `<name>_resized.<ext>` next to the original; the result holds the output path and final size.
- `send-email`: `to` (single address), `subject` (single line), `body`.

## Retries

A failing job is re-queued up to 3 times (4 attempts total) with no delay, then marked `failed`
with the last error. While waiting for a retry its status is `queued` and `error` holds the latest failure.
