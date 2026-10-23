# URL Shortener

Small Java 17 service: JDK built-in HTTP server, SQLite (via `sqlite-jdbc`) for storage, Jackson for JSON.

## Run

    mvn test           # run tests
    mvn compile exec:java

Config via environment variables: `PORT` (default `8080`), `DB_PATH` (default `links.db`).

## API

**Create** — `POST /api/links`

    curl -X POST localhost:8080/api/links -H 'Content-Type: application/json' \
         -d '{"url":"https://example.com/some/page","alias":"my-link"}'

`alias` is optional (3–32 chars of `A-Za-z0-9_-`); without it a random 7-character code is generated.
Returns `201` with `code`, `shortUrl`, `url`, `title`, `createdAt`.
Errors: `400` invalid input (URL must be absolute http/https), `409` alias taken.

**Redirect** — `GET /{code}` → `302` to the original URL and increments the click count. `404` if unknown.

**Stats** — `GET /api/links/{code}/stats` →

    {"code":"my-link","url":"https://example.com/some/page","title":"Example","createdAt":"2026-10-22T12:00:00Z","clicks":3}

## Notes

- The page title is fetched synchronously when the link is created (5s timeout, first 64 KiB of an HTML response, no redirects followed). If the fetch fails, the link is still created and `title` is `null`.
- Because the URL is user-supplied, the fetcher refuses hosts resolving to loopback, private, link-local and similar addresses (SSRF protection). The check resolves DNS separately from the request, so a DNS-rebinding attacker could still slip through; for hardened deployments, fetch through an egress proxy.
- There is no authentication or rate limiting.
- The tests use a stub title fetcher, so they need no network access.
