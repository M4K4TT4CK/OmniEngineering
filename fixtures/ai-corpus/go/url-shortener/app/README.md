# URL Shortener

A small URL shortener in Go (stdlib `net/http`, Go 1.22+ routing) with SQLite
persistence via the pure-Go driver `modernc.org/sqlite` (no cgo needed).

## Run

```sh
go mod tidy        # first time only: resolves dependencies and writes go.sum
go run .
go test ./...
```

Configuration (environment variables):

| Variable   | Default    | Purpose                                              |
|------------|------------|------------------------------------------------------|
| `ADDR`     | `:8080`    | Listen address                                       |
| `DB_PATH`  | `links.db` | SQLite file                                          |
| `BASE_URL` | (request)  | Prefix for `short_url`; defaults to `http://<Host>`  |

## API

**Create** — `POST /api/links`

```sh
curl -X POST localhost:8080/api/links \
  -d '{"url":"https://example.com/some/page","alias":"example"}'
```

`alias` is optional (1–32 chars of `A-Za-z0-9_-`; `api` is reserved). Without it a
random 7-character code is generated. Returns `201` with
`code`, `short_url`, `url`, `title`, `created_at`. Errors: `400` invalid input,
`409` alias taken.

**Redirect** — `GET /{code}` → `302` to the long URL and increments the click
count. `404` if unknown.

**Stats** — `GET /api/links/{code}/stats` →
`{"code","url","title","clicks","created_at"}`. Does not count as a click.

## Notes

- Only absolute `http`/`https` URLs are accepted.
- The page title is fetched once at creation (5s timeout, first 512 KB, max 5
  redirects). A failed fetch leaves the title empty and does not fail creation.
- Because the server fetches user-supplied URLs, connections to loopback,
  private, link-local and CGNAT addresses are refused (checked at connect time,
  so redirects and DNS tricks are covered). Tests disable this guard only to
  reach local `httptest` servers.
- Files: `main.go` (service), `main_test.go` (tests), `go.mod`, this README.
