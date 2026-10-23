# Shop admin reporting service

A small Express + SQLite (better-sqlite3) service, written in TypeScript, that gives shop admins sales reports and order exports.

## Files

- `src/db.ts`: schema, DB setup, API key hashing and admin creation
- `src/app.ts`: HTTP app (auth, validation, reports, export)
- `src/server.ts`: entry point and `create-admin` CLI
- `test/app.test.ts`: tests (Vitest + Supertest, using an in-memory DB)

## Running

```sh
npm install
npm run create-admin -- "Jane Admin"   # prints a new API key once
npm run dev                            # or: npm run build && npm start
npm test
```

Environment variables: `DATABASE_PATH` (default `shop.db`), `PORT` (default `3000`), `HOST` (default `127.0.0.1`).

The schema is created on startup if it is missing. `created_at` is ISO-8601 UTC text. Money is stored as integer cents.

## Authentication

Every endpoint except `/health` needs an `X-API-Key` header. Keys are random 256-bit tokens. Only their SHA-256 hashes are stored, in `admin_users`. To revoke a key, set `active = 0` on its row.

## Endpoints

Dates are `YYYY-MM-DD` and both ends are inclusive (UTC). Reports count only orders whose status is `paid`, `shipped` or `delivered`.

| Endpoint | Parameters |
|---|---|
| `GET /reports/sales` | `from`, `to` (required); `groupBy` = `day` (default) \| `week` \| `month` |
| `GET /reports/top-products` | `limit` (1–100, default 10); `from`, `to` (optional) |
| `GET /orders/export` | `format` = `json` (default) \| `csv`; `status` (comma-separated list); `customerId`; `from`; `to`; `sort`; `order` = `asc` \| `desc` |

- **Weeks** start on Monday. Each week is labelled with its Monday's date.
- **Sort columns** for `sort`: `id`, `created_at` (default), `status`, `customer_id`, `customer_name`, `customer_email`, `item_count`, `total_cents`. Ties are broken by order id.

Example:

```sh
curl -H "X-API-Key: $KEY" "localhost:3000/orders/export?format=csv&status=paid,shipped&from=2024-01-01&to=2024-03-31&sort=total_cents&order=desc"
```

## Security notes

- All values go into SQL as bound parameters. Sort columns and grouping expressions are picked from fixed whitelists, so unknown values return `400`.
- CSV cells that start with `=`, `+`, `-`, `@`, tab or CR get an apostrophe prefix, so they can't run as spreadsheet formulas.
- If a query parameter is given more than once or is malformed, the request is rejected with `400` instead of being guessed at.
- The export builds the whole response in memory. That is fine for typical admin volumes, but very large shops should add paging or streaming.
