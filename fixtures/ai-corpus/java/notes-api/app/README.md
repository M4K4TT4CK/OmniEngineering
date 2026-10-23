# Notes API

A small REST API for personal notes with user accounts. Java 17, [Javalin](https://javalin.io), SQLite (via `sqlite-jdbc`).

## Run

```
mvn exec:java        # listens on :7070, stores data in ./notes.db
mvn test
```

Environment variables: `PORT` (default `7070`), `NOTES_DB` (SQLite file path, default `notes.db`).

## Endpoints

JSON in, JSON out. Errors look like `{"error": "..."}`.

| Method & path        | Auth | Description |
|----------------------|------|-------------|
| `POST /signup`       | no   | `{"username","password"}` → 201. Username: 3–32 chars of `A-Za-z0-9_` (case-insensitive unique). Password: 8–128 chars. |
| `POST /login`        | no   | Same body → `{"token","expiresAt"}` (token valid 24h). |
| `POST /notes`        | yes  | `{"title","body"}` → 201 with the note. Title required (≤200 chars), body optional (≤10000). |
| `GET /notes`         | yes  | List your notes, most recently updated first. `?q=text` searches title and body (case-insensitive substring); `?limit=` (1–100, default 50) and `?offset=`. |
| `GET /notes/{id}`    | yes  | Fetch one note. |
| `PUT /notes/{id}`    | yes  | Replace title/body. |
| `DELETE /notes/{id}` | yes  | → 204. |

Send the token as `Authorization: Bearer <token>`. Another user's note ID returns 404.

```
curl -X POST localhost:7070/signup -d '{"username":"me","password":"correct horse"}'
TOKEN=$(curl -s -X POST localhost:7070/login -d '{"username":"me","password":"correct horse"}' | jq -r .token)
curl -X POST localhost:7070/notes -H "Authorization: Bearer $TOKEN" -d '{"title":"Hi","body":"first note"}'
curl "localhost:7070/notes?q=first" -H "Authorization: Bearer $TOKEN"
```

## Design notes

- Passwords are hashed with PBKDF2-HMAC-SHA256 (210k iterations, per-user salt). Tokens are random 256-bit values; only their SHA-256 is stored.
- Rate limit: 60 requests per minute per user (fixed window), tracked in memory. Exceeding it returns 429 with `Retry-After`. `/signup` and `/login` have no user yet, so they are limited per client IP with the same budget. Requests with an invalid token get 401 and don't count. Limits reset on restart and are per process, so a multi-instance deployment would need a shared store.
- Behind a reverse proxy, the client IP seen by the app is the proxy's; adjust if you rely on the IP limit.
- SQLite uses a single shared connection with synchronized access, which is plenty for this scale.
- Everything is in `src/main/java/notes/App.java`; tests in `src/test/java/notes/AppTest.java` run the real server against in-memory SQLite.
