# Admin reporting service

A small Flask service that gives shop admins sales reports, top-product
rankings and order exports, read from the shop's SQLite database.

## Setup

Requires Python 3.9+ and:

```
pip install flask pytest
```

## Configuration

| Variable         | Default   | Purpose                                              |
|------------------|-----------|------------------------------------------------------|
| `SHOP_DB_PATH`   | `shop.db` | Path to the SQLite database                          |
| `ADMIN_API_KEYS` | (none)    | Comma-separated admin API keys. If unset, all requests get 401. |

## Running

```
ADMIN_API_KEYS=change-me python app.py
```

The server listens on port 8000. For production, serve the factory with a WSGI
server, e.g. `gunicorn "app:create_app()"`.

## Authentication

Send the key in the `X-API-Key` header on every request. Missing or wrong keys
return `401`.

## Endpoints

### `GET /reports/sales`

Revenue and order count per period for a date range.

- `start`, `end` (required) — `YYYY-MM-DD`, both inclusive.
- `group_by` — `day` (default), `week` (weeks start on Monday) or `month`.

Cancelled orders are excluded. Revenue is `quantity * unit_price_cents`
summed over order items, in cents.

```
curl -H "X-API-Key: change-me" \
  "http://localhost:8000/reports/sales?start=2024-01-01&end=2024-03-31&group_by=week"
```

### `GET /reports/top-products`

Products ranked by revenue for a date range (cancelled orders excluded).

- `start`, `end` (required) — `YYYY-MM-DD`, both inclusive.
- `limit` — 1 to 100, default 10.

### `GET /orders/export`

Orders with customer name and email and the order total in cents.

- `format` — `json` (default) or `csv` (sent as a file download).
- `status` — exact match on order status.
- `customer_id` — integer.
- `start`, `end` — optional `YYYY-MM-DD` bounds on the order date, both inclusive.
- `sort` — one of `id`, `created_at`, `status`, `customer_id`, `customer_name`,
  `total_cents`. Default `id`.
- `direction` — `asc` (default) or `desc`. Ties on the sort column are broken by `id`.

Unlike the reports, export includes cancelled orders.

## Assumed database schema

The service reads these tables and does not create or migrate them:

- `customers(id, name, email)`
- `products(id, name, ...)`
- `orders(id, customer_id, status, created_at)` — `created_at` is a text
  timestamp in ISO format (`YYYY-MM-DD HH:MM:SS`).
- `order_items(order_id, product_id, quantity, unit_price_cents)`

If your schema differs, adjust the SQL in `app.py`. The test file's `SCHEMA`
constant shows the full expected shape.

## Design notes

- Sort columns, group-by options and directions are checked against fixed
  allowlists and mapped to SQL fragments. Filter values are passed as bound
  parameters. No user input is interpolated into SQL text.
- Export loads all matching rows into memory. For very large exports, add
  pagination or stream the rows.

## Tests

```
pytest
```

The tests build a temporary SQLite database with a small seeded dataset and
cover authentication, each report grouping, filtering, sorting and validation.
