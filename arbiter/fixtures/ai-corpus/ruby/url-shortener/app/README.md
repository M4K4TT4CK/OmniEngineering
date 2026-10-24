# URL Shortener

A small URL shortener built with Sinatra and SQLite.

## Setup and run

```sh
bundle install
bundle exec rackup -p 4567     # or: ruby app.rb
```

The database file defaults to `shortener.db` in the current directory; override with `DATABASE_PATH`.

## Tests

```sh
bundle exec ruby test/app_test.rb
```

Tests use an in-memory database and a stubbed title fetcher, so they need no network.

## API

### Create a link
`POST /api/links` with JSON `{"url": "https://example.com", "alias": "optional-alias"}`

- `201` → `{"code", "short_url", "url", "title", "clicks", "created_at"}`
- `400` malformed JSON, `422` invalid URL/alias, `409` alias already taken
- Aliases are 3–32 characters of `A-Z a-z 0-9 - _` (case-sensitive; `api` is reserved). Without an alias a random 7-character code is generated.

### Redirect
`GET /:code` → `302` to the long URL and increments the click count. `404` if unknown.

### Stats
`GET /api/links/:code/stats` → `{"code", "url", "title", "clicks", "created_at"}` (`created_at` is UTC ISO 8601). Reading stats does not count as a click.

### Example
```sh
curl -X POST localhost:4567/api/links -H 'Content-Type: application/json' \
     -d '{"url":"https://example.com","alias":"ex"}'
curl -i localhost:4567/ex
curl localhost:4567/api/links/ex/stats
```

## Notes

- The page title is fetched once, at creation, with a 5s timeout, up to 3 redirects and a 256 KB read limit. If the fetch fails the link is still created with `title: null`. Creation blocks while the fetch runs.
- The title fetcher refuses loopback, private and link-local addresses (SSRF guard) and connects to the address it validated. Links to such URLs can still be created and redirected; only the title lookup is skipped.
- Redirects use `302` rather than `301` so browsers don't cache them and skip click counting.
- Layout: `app.rb` (store, title fetcher, routes), `config.ru`, `test/app_test.rb`.
