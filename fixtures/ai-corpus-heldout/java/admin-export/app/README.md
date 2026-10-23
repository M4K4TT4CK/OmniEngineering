# Shop admin reporting service

A small read-only HTTP service that reports on the shop's SQLite database. It uses
the JDK's built-in `HttpServer` and `sqlite-jdbc`, and has no other runtime dependencies.

## Files

- `pom.xml`: Maven build (Java 17)
- `src/main/java/shop/AdminReportService.java`: the server, queries, auth, schema and CLI
- `src/test/java/shop/AdminReportServiceTest.java`: end-to-end tests against a temporary database

## Build, set up and run

```sh
mvn package                                         # compiles, runs the tests, builds the fat jar
export SHOP_DB=shop.db
java -jar target/admin-reporting-1.0.0.jar init-db  # creates tables if they are missing
java -jar target/admin-reporting-1.0.0.jar add-admin alice   # prints a new API key once
java -jar target/admin-reporting-1.0.0.jar          # serves on HOST:PORT (default 127.0.0.1:8080)
```

Only a SHA-256 hash of each API key is stored, in `admin_users.api_key_hash`. To revoke a
key, set `active = 0` on its row.

## API

Every request must send `X-API-Key: <key>` or `Authorization: Bearer <key>`. If the key is
missing or wrong, the service returns `401`. Bad parameters get a `400` with `{"error": "..."}`.
Dates are `YYYY-MM-DD`, and ranges include both end dates. Money values are integer cents.
Revenue figures leave out `cancelled` and `refunded` orders.

| Endpoint | Parameters |
|---|---|
| `GET /reports/sales` | `from`, `to` (required), `group_by=day\|week\|month` (default `day`). Weeks start on Monday, and months are labelled by their first day. |
| `GET /reports/top-products` | `limit` (1–100, default 10), plus optional `from` and `to` |
| `GET /orders/export` | `format=json\|csv` (default `json`), `status`, `customer` (customer id), `from`, `to`, `sort=id\|created_at\|status\|total\|customer_id\|customer_name` (default `created_at`), `order=asc\|desc` (default `desc`), `limit` (1–10000, default 1000) |

```sh
curl -H "X-API-Key: $KEY" "localhost:8080/reports/sales?from=2024-01-01&to=2024-03-31&group_by=week"
curl -H "X-API-Key: $KEY" "localhost:8080/orders/export?format=csv&status=paid&sort=total&order=desc"
```

## Security notes

- Every value is bound as a query parameter. The sort column, sort direction and grouping
  expression are looked up in fixed allow-lists and are never copied from the request.
- The service opens SQLite in read-only mode for all requests.
- In CSV exports, text cells that start with `= + - @` (or a tab or carriage return) get a `'`
  prefix, so spreadsheet programs won't run them as formulas.
- By default the service binds to localhost. It has no TLS, so put it behind a TLS-terminating
  proxy before you expose it.

## Expected schema

`customers(id, name, email)`, `products(id, name, sku, price_cents)`,
`orders(id, customer_id, status, created_at, total_cents)` with `created_at` stored as UTC
text in the form `YYYY-MM-DD HH:MM:SS`, and `order_items(order_id, product_id, quantity, unit_price_cents)`.
The full DDL is in `initSchema`.
