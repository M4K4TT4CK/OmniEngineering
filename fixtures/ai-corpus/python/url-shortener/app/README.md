# URL Shortener

Small Flask + SQLite service. Links are stored in `shortener.db` (created on first start).

## Run

    pip install -r requirements.txt
    python app.py            # http://127.0.0.1:5000

## Test

    pytest

## API

**Create** — `POST /api/links`

    curl -X POST localhost:5000/api/links -H 'Content-Type: application/json' \
         -d '{"url": "https://example.com", "alias": "ex"}'

`alias` is optional (1–32 chars of `A-Za-z0-9_-`). Returns `201` with `code`, `short_url`, `url`, `title`.
`400` for an invalid URL/alias, `409` if the alias is taken.

**Redirect** — `GET /<code>` → `302` to the long URL and increments the click count. `404` if unknown.

**Stats** — `GET /api/links/<code>/stats` → `code`, `url`, `title`, `clicks`, `created_at` (UTC ISO 8601).

## Notes

- The page title is fetched once, at creation time, with a 5s timeout. If it can't be fetched, `title` is `null` and the link is still created.
- To limit SSRF, the title fetcher only contacts hosts that resolve to public IP addresses and does not follow redirects (so titles of redirecting URLs are `null`). DNS rebinding between the check and the fetch is not fully mitigated.
- Fetching the title happens synchronously in the create request.
- `api` is a reserved alias. The built-in Flask server is for development; use a WSGI server (e.g. gunicorn) in production.
