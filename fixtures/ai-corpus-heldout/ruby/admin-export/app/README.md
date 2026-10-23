# Admin reporting service

A small Sinatra API that reports on the shop's SQLite database: sales by
day/week/month, top products by revenue, and an orders export (CSV or JSON).

## Files

- `app.rb` — the API (`ReportApp`)
- `config.ru` — loads the database and API keys from the environment
- `test_app.rb` — Minitest tests against an in-memory database
- `Gemfile` — dependencies

## Setup

```sh
bundle install
export DATABASE_PATH=/path/to/shop.db
export ADMIN_API_KEYS=key-one,key-two
bundle exec rackup -p 9292
```

Every request must send the key in the `X-API-Key` header. If
`ADMIN_API_KEYS` is unset, all requests are refused.

## Endpoints

All dates are `YYYY-MM-DD` and the range is inclusive.

| Endpoint | Parameters |
| --- | --- |
| `GET /reports/sales` | `from`, `to` (required); `group_by` = `day` (default), `week`, `month` |
| `GET /reports/top-products` | `from`, `to` (required); `limit` = 1–100 (default 10) |
| `GET /orders/export` | `format` = `json` (default) or `csv`; `status`; `customer_id`; `from`, `to` (optional); `sort` = `created_at` (default), `id`, `status`, `customer_id`, `customer_name`, `total_cents`; `direction` = `asc` (default) or `desc` |

Example:

```sh
curl -H "X-API-Key: $KEY" \
  "http://localhost:9292/reports/sales?from=2026-01-01&to=2026-03-31&group_by=week"
```

## Assumed schema

The service reads existing tables and does not create them:

- `customers(id, name, ...)`
- `products(id, sku, name, ...)`
- `orders(id, customer_id, status, created_at)` — `created_at` is ISO 8601 text
- `order_items(order_id, product_id, quantity, unit_price_cents)`

Revenue is `sum(quantity * unit_price_cents)`. Sales and top-product reports
leave out orders with status `cancelled`. The export includes every order.
Week groupings use SQLite's `%W` (weeks start on Monday; `YYYY-Wnn`).

## Tests

```sh
bundle exec ruby test_app.rb
```

## Notes

- Sort columns and grouping values are whitelisted; caller input is never
  placed into SQL text.
- The export is not paginated, so large date ranges return large responses.
- CSV values are not escaped against spreadsheet formula injection.
