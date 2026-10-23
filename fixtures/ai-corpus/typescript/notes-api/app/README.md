# Notes API

REST API for personal notes with user accounts. TypeScript, Express, SQLite (`better-sqlite3`).

## Run

```
npm install
npm run dev          # or: npm run build && npm start
npm test
```

Environment: `PORT` (default 3000), `DB_PATH` (default `notes.db`).

## Endpoints

JSON in/out. Authenticated routes need `Authorization: Bearer <token>`.

| Method | Path | Description |
| --- | --- | --- |
| POST | `/signup` | `{username, password}` → 201 `{id, username, token}` |
| POST | `/login` | `{username, password}` → `{token}` |
| POST | `/notes` | `{title, body?}` → 201 note |
| GET | `/notes?q=&limit=&offset=` | List own notes, newest-updated first; `q` searches title and body (limit ≤ 100, default 50) |
| GET | `/notes/:id` | Get a note |
| PUT | `/notes/:id` | Update `title` and/or `body` |
| DELETE | `/notes/:id` | Delete (204) |

Errors are `{ "error": "message" }` with 400, 401, 404, 409, 413, 429 or 500.

## Design notes

- Usernames: 3–32 chars of letters, digits, `_ . -`, unique case-insensitively. Passwords: 8–128 chars, hashed with scrypt and a per-user salt.
- Tokens are random 256-bit values, valid for 24 hours. Only their SHA-256 is stored in SQLite.
- Rate limit: 60 requests per minute (fixed window) per user, keyed by user id. Signup, login and requests with bad tokens are limited per IP. Responses carry `X-RateLimit-*` headers, and 429s include `Retry-After`. Counters are in memory, so they are per process and reset on restart; use a shared store if you run several instances.
- Users can only see and change their own notes. Other users' notes return 404.
- If deployed behind a proxy, set Express's `trust proxy` so per-IP limits see the real client address.
