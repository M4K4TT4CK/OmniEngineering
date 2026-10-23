# Admin export service

A small read-only HTTP service for admins of an online shop. It reads orders,
customers and products from SQLite and provides:

- a sales report for a date range, grouped by day, week or month
- the top products by revenue for a date range
- an order export as CSV or JSON, with filters and a caller-chosen sort column

Written in Go using only the standard library plus `modernc.org/sqlite`
(pure Go, no cgo).

## Files

- `go.mod`: module definition
- `server.go`: entry point, routing, API key authentication, shared helpers
- `handlers.go`: report and export handlers, date parsing, SQL construction
- `server_test.go`: tests against a seeded temporary SQLite database

## Expected schema

The service expects these tables to exist. Column names must match.

```sql
CREATE TABLE customers   (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL);
CREATE TABLE products    (id INTEGER PRIMARY KEY, name TEXT NOT NULL, sku TEXT NOT NULL);
CREATE TABLE orders      (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL,
                          status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE order_items (order_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
                          quantity INTEGER NOT NULL, unit_price_cents INTEGER NOT NULL);

-- Used by this service for authentication (new table):
CREATE TABLE admin_api_keys (key_hash TEXT PRIMARY KEY, name TEXT NOT NULL,
                             created_at TEXT NOT NULL);
```

- `created_at` holds `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS` (or RFC 3339) text.
- Revenue is `quantity * unit_price_cents`, summed over `order_items`.
- Orders with status `cancelled` are excluded from reports. Exports include them.

## Authentication

Send an admin API key as either:

- `Authorization: Bearer <key>`
- `X-API-Key: <key>`

Keys are stored as SHA-256 hashes. To add a key, generate a random value and
store its hash:

```sh
KEY=$(openssl rand -hex 32)
echo "$KEY"   # give this to the admin once
HASH=$(printf '%s' "$KEY" | sha256sum | cut -d' ' -f1)
sqlite3 shop.db "INSERT INTO admin_api_keys (key_hash, name, created_at)
                 VALUES ('$HASH', 'ops-laptop', datetime('now'));"
```

## Endpoints

All endpoints are `GET` and return JSON except the export in CSV format.
Errors return `{"error": "..."}`.

### `GET /api/reports/sales`

| Param   | Required | Notes                                        |
|---------|----------|----------------------------------------------|
| `from`  | yes      | `YYYY-MM-DD`, inclusive                      |
| `to`    | yes      | `YYYY-MM-DD`, inclusive                      |
| `group` | no       | `day` (default), `week` or `month`           |

`week` uses Monday-start weeks numbered by SQLite's `%W` (for example
`2026-W01`). This is not ISO 8601 week numbering.

```json
{"group":"month","from":"2026-01-01","to":"2026-02-28",
 "buckets":[{"period":"2026-01","orders":2,"units":4,"revenue_cents":4000}],
 "totals":{"orders":2,"units":4,"revenue_cents":4000}}
```

### `GET /api/reports/top-products`

| Param   | Required | Notes                                   |
|---------|----------|-----------------------------------------|
| `from`  | yes      | `YYYY-MM-DD`, inclusive                 |
| `to`    | yes      | `YYYY-MM-DD`, inclusive                 |
| `limit` | no       | 1–100, default 10                       |

Sorted by revenue descending, then product id ascending.

### `GET /api/orders/export`

| Param         | Required | Notes                                                              |
|---------------|----------|--------------------------------------------------------------------|
| `format`      | no       | `json` (default) or `csv`. CSV is sent as an attachment.           |
| `status`      | no       | Exact status match                                                 |
| `customer_id` | no       | Integer                                                            |
| `from`, `to`  | no       | `YYYY-MM-DD`, inclusive. Either or both may be given.              |
| `sort`        | no       | `id`, `created_at` (default), `status`, `customer`, `total`        |
| `dir`         | no       | `asc` (default) or `desc`                                          |

Columns: `id`, `created_at`, `status`, `customer_id`, `customer_name`,
`total_cents`. Ties are broken by `id` ascending.

Sort and direction are chosen from fixed lists and mapped to SQL server-side.
Caller input never becomes part of the SQL text.

## Running

```sh
go mod tidy                 # resolves go.sum and the sqlite version
go test ./...
DB_PATH=shop.db ADDR=:8080 go run .
```

`DB_PATH` defaults to `shop.db` and `ADDR` defaults to `:8080`.

Example:

```sh
curl -H "Authorization: Bearer $KEY" \
  "http://localhost:8080/api/reports/sales?from=2026-01-01&to=2026-03-31&group=week"
curl -H "Authorization: Bearer $KEY" -o orders.csv \
  "http://localhost:8080/api/orders/export?format=csv&status=paid&sort=total&dir=desc"
```

## Notes and limits

- The database is opened read-write because SQLite needs that for the driver.
  The service issues only `SELECT` statements. For defence in depth, point
  `DB_PATH` at a read-only replica or open the file with `mode=ro`.
- The export streams rows without buffering the full result set.
- CSV values are not escaped against spreadsheet formula injection. If the
  export is opened in a spreadsheet, customer names starting with `=`, `+`,
  `-` or `@` could run as formulas.
- There is no rate limiting, pagination on the export, or TLS. Run it behind a
  reverse proxy that provides TLS.
