# Notes API

A small REST API for personal notes with user accounts. Built with Sinatra, SQLite and bcrypt.

## Files

- `app.rb` – the application (database layer and routes)
- `config.ru` – Rack entry point
- `test_app.rb` – Minitest / Rack::Test suite
- `Gemfile` – dependencies

## Setup and run

```sh
bundle install
bundle exec rackup -p 4567        # or: ruby app.rb
ruby test_app.rb                  # run the tests (uses in-memory SQLite)
```

The database file defaults to `notes.db`; override with `NOTES_DB=/path/to/file.db`.

## Endpoints

All bodies are JSON. Authenticated endpoints need `Authorization: Bearer <token>`.

| Method | Path | Description |
|---|---|---|
| POST | `/signup` | `{username, password}` → 201. Username: 3–32 chars of letters, digits, `_` (case-insensitive unique). Password: 8–72 bytes. |
| POST | `/login` | `{username, password}` → `{token, expires_at}` |
| POST | `/notes` | `{title, body?}` → 201 with the note |
| GET | `/notes` | List your notes, newest-updated first. Query: `q` (search title/body), `limit` (1–100, default 20), `offset` |
| GET | `/notes/:id` | Fetch one note |
| PUT | `/notes/:id` | Update `title` and/or `body` |
| DELETE | `/notes/:id` | Delete (204) |

Errors are `{"error": "..."}` with an appropriate status (400, 401, 404, 409, 422, 429).

```sh
curl -X POST localhost:4567/signup -d '{"username":"alice","password":"s3cretpass"}'
TOKEN=$(curl -s -X POST localhost:4567/login -d '{"username":"alice","password":"s3cretpass"}' | ruby -rjson -e 'puts JSON.parse(STDIN.read)["token"]')
curl -X POST localhost:4567/notes -H "Authorization: Bearer $TOKEN" -d '{"title":"Hello","body":"world"}'
curl "localhost:4567/notes?q=hello" -H "Authorization: Bearer $TOKEN"
```

## Design notes

- **Passwords** are hashed with bcrypt. Login does a dummy bcrypt comparison for unknown usernames to reduce username-enumeration timing differences.
- **Tokens** are 256-bit random values valid for 7 days. Only their SHA-256 hash is stored, so a database leak does not expose usable tokens.
- **Rate limit**: 60 requests per minute (fixed window), counted per user for authenticated requests and per client IP otherwise (signup, login, bad tokens). State is kept in SQLite, so it is shared across processes. Exceeding it returns 429 with `Retry-After`; `X-RateLimit-*` headers are sent on every response. Behind a reverse proxy, make sure `request.ip` reflects the real client (e.g. configure trusted proxies), otherwise anonymous clients share one bucket.
- Search is a case-insensitive substring match (`LIKE`, with wildcards escaped) over title and body. Notes belonging to other users always return 404.
- There is no logout/token-revocation endpoint; tokens simply expire.
