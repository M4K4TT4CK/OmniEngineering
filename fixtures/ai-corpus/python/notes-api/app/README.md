# Notes API

A small REST API for personal notes, built with FastAPI and SQLite (stdlib `sqlite3`).

## Run

```bash
pip install -r requirements.txt
uvicorn app:app
```

The database file defaults to `notes.db`; override with `NOTES_DB=/path/to/file.db`.
Interactive docs are served at `/docs`.

## Test

```bash
pytest
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/signup` | `{"username", "password"}` → creates account, returns token |
| POST | `/login` | `{"username", "password"}` → returns token |
| POST | `/notes` | `{"title", "body"}` → create note |
| GET | `/notes?q=&limit=&offset=` | List your notes (newest-updated first); `q` searches title and body, case-insensitively |
| GET | `/notes/{id}` | Fetch one note |
| PATCH | `/notes/{id}` | Update `title` and/or `body` |
| DELETE | `/notes/{id}` | Delete a note |

Send the token as `Authorization: Bearer <token>` on `/notes` requests.

```bash
TOKEN=$(curl -s localhost:8000/signup -H 'content-type: application/json' \
  -d '{"username":"alice","password":"correct horse"}' | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s localhost:8000/notes -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"title":"Hello","body":"world"}'
curl -s "localhost:8000/notes?q=hello" -H "Authorization: Bearer $TOKEN"
```

## Design notes

- **Passwords** are hashed with scrypt (per-user random salt). Usernames are 3–32 chars of letters, digits, `_`, and are case-insensitive; passwords are 8–128 chars.
- **Tokens** are random, valid for 24 hours, and only their SHA-256 hash is stored.
- **Isolation**: every note query is scoped to the authenticated user; other users' notes return 404.
- **Rate limiting**: 60 requests per rolling minute per user (keyed on the token's user), returning `429` with `Retry-After`. `/signup`, `/login` and requests with missing/invalid tokens are limited per client IP instead. The limiter is in memory, so it is per process: with multiple workers each has its own counters (use a shared store such as Redis for that). Behind a reverse proxy, configure uvicorn's `--proxy-headers` so the IP is the real client's.
- Tokens are not revocable via an endpoint (no logout) in this version.
