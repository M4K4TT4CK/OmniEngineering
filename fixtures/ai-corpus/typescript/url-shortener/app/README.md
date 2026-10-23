# URL Shortener

A small URL shortener built with TypeScript, Express and SQLite (`better-sqlite3`). Requires Node.js 18+.

## Setup

```sh
npm install
npm run dev        # run with tsx
# or
npm run build && npm start
npm test           # vitest + supertest, in-memory SQLite, title fetching stubbed
```

Configuration (environment variables): `PORT` (default `3000`), `DB_PATH` (default `links.db`), `BASE_URL` (optional, used to build `shortUrl`; defaults to the request's host).

## API

### `POST /api/links`
Body: `{ "url": "https://example.com/long", "alias": "optional-alias" }`

Returns `201` with `{ code, shortUrl, url, title, createdAt }`. The page title is fetched from the long URL at creation time; if that fails, `title` is `null` and the link is still created.
Errors: `400` invalid URL/alias (alias: 3–32 chars of `A-Za-z0-9_-`, not `api`/`health`), `409` alias already taken.

### `GET /:code`
`302` redirect to the original URL and increments the click count. `404` if unknown.

### `GET /api/links/:code/stats`
Returns `{ code, url, title, clicks, createdAt }` (`createdAt` is ISO 8601). `404` if unknown.

## Notes

- Only `http`/`https` URLs are accepted. Generated codes are 7 random base62 characters.
- Title fetching refuses hosts that resolve to private/loopback/link-local addresses (including across up to 3 redirects), has a 5s timeout and reads at most 128 KB. The check happens before the request, so DNS rebinding is not fully prevented; a production deployment should also use an egress proxy or firewall.
- There is no authentication or rate limiting.
