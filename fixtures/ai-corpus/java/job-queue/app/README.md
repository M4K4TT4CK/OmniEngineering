# Job Queue

In-memory background job queue with an HTTP API, a worker thread pool, and automatic retries.
Java 17, Maven. The HTTP server is the JDK's built-in `com.sun.net.httpserver`; dependencies are
Jackson (JSON) and Angus Mail (SMTP).

## Run

```
mvn test
mvn compile exec:java -Dexec.mainClass=jobqueue.App
```

| Env var | Default | Purpose |
|---|---|---|
| `PORT` | `8080` | HTTP port |
| `WORKERS` | `4` | Worker threads |
| `IMAGE_DIR` | unset | If set, `resize-image` paths must be inside this directory |
| `SMTP_HOST` | – | Required for `send-email` |
| `SMTP_PORT` | `587` | |
| `SMTP_USER`, `SMTP_PASSWORD` | – | Enables SMTP auth when `SMTP_USER` is set |
| `SMTP_FROM` | `SMTP_USER` | Sender address |
| `SMTP_STARTTLS` | `true` | |

## API

Submit a job (`202` with the id; `400` for unknown type or invalid payload):

```
curl -X POST localhost:8080/jobs -d '{"type":"send-email","payload":{"recipient":"a@example.com","subject":"Hi","body":"Hello"}}'
curl -X POST localhost:8080/jobs -d '{"type":"resize-image","payload":{"path":"/data/photo.jpg","width":400}}'
```

Check status and result (`404` if unknown):

```
curl localhost:8080/jobs/<id>
# {"id":"...","type":"resize-image","status":"SUCCEEDED","attempts":1,"result":{"outputPath":"...","width":400,"height":300},"error":null}
```

Status is `QUEUED`, `RUNNING`, `SUCCEEDED` or `FAILED`. `error` holds the most recent failure message.

### Job types

- `resize-image`: `path` (required), `width` (optional, default 800, aspect ratio kept). Writes `<name>-resized.<ext>` next to the original.
- `send-email`: `recipient`, `subject`, `body` (all required).

## Behaviour and limits

- A failed job is retried up to 3 times (4 attempts total) with a 1 s delay, then marked `FAILED`. A job waiting for retry shows as `QUEUED`.
- Payloads are validated at submit time, so malformed requests are rejected rather than retried.
- Jobs and results live only in memory: they are lost on restart and never evicted, so memory grows with job count.
- There is no authentication. `resize-image` reads and writes server file paths from the request, so set `IMAGE_DIR` and do not expose the API to untrusted callers.

## Layout

- `JobQueue.java`: queue, worker pool, retry logic
- `Handlers.java`: `resize-image` and `send-email`
- `App.java`: HTTP API and `main`
- `src/test/.../JobQueueTest.java`: queue, handler, and HTTP tests
