# Notes API

A small REST API for personal notes with user accounts, written in Go.
Standard library `net/http` for routing, SQLite (pure-Go `modernc.org/sqlite`, no cgo) for storage, bcrypt for passwords.

## Run

```sh
go mod tidy      # first time only: resolves dependencies and writes go.sum
go run .
go test ./...
```

Requires Go 1.22+. Config via environment: `ADDR` (default `:8080`), `DB_PATH` (default `notes.db`).

## Endpoints

All bodies are JSON. Authenticated endpoints need `Authorization: Bearer <token>`.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/signup` | `{"username","password"}` → `201 {"token","expires_at"}` |
| POST | `/login` | same body → `200 {"token","expires_at"}` |
| POST | `/logout` | revoke the current token (`204`) |
| POST | `/notes` | `{"title","body"}` → `201` note |
| GET | `/notes` | list your notes; `?q=` searches title and body, `?limit=` (1–100, default 50), `?offset=` |
| GET | `/notes/{id}` | fetch one note |
| PUT | `/notes/{id}` | replace `title` and `body` |
| DELETE | `/notes/{id}` | delete (`204`) |

Example:

```sh
TOKEN=$(curl -s localhost:8080/signup -d '{"username":"alice","password":"correct horse"}' | jq -r .token)
curl -s localhost:8080/notes -H "Authorization: Bearer $TOKEN" -d '{"title":"Hello","body":"first note"}'
curl -s "localhost:8080/notes?q=hello" -H "Authorization: Bearer $TOKEN"
```

## Behavior notes

- **Rate limiting:** 60 requests/minute per user (fixed window, in memory), returning `429` with `Retry-After`. `/signup` and `/login` have no user yet, so they are limited per client IP at the same rate. `X-Forwarded-For` is not trusted; behind a proxy, adapt `limitIP`. Limits reset on restart and aren't shared across multiple instances.
- **Tokens:** random 256-bit values valid for 24 hours; only their SHA-256 hash is stored.
- **Passwords:** 8–72 bytes, hashed with bcrypt. Usernames are 3–32 chars (letters, digits, `_-.`) and case-insensitively unique.
- **Isolation:** other users' notes return `404`, not `403`, so note IDs don't leak.
- **Search:** case-insensitive substring match (ASCII) on title and body; `%` and `_` are treated literally.
- **Transport:** the server speaks plain HTTP; run it behind TLS in any real deployment.

## Layout

- `main.go` — server, handlers, rate limiter, schema
- `main_test.go` — tests using an in-memory SQLite database
