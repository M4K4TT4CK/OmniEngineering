# Payment webhook service

A small Go service that receives payment provider webhooks, applies each event to its order exactly once, and exposes an order status lookup. Orders are stored in SQLite.

## Endpoints

- `POST /webhooks`: receives an event. The body is JSON:
  ```json
  {"id": "evt_123", "type": "payment.succeeded", "order_id": "ord_1", "amount": 1500}
  ```
  `amount` is in minor units (cents). Supported types and the status they set:
  - `payment.succeeded` → `paid`
  - `payment.failed` → `failed`
  - `refund.created` → `refunded`

  Other types are acknowledged with `200 {"ignored": true}` and not stored.
  The response is `200 {"duplicate": true}` when the event ID was already applied.
- `GET /orders/{id}`: returns `{"id", "amount", "status", "updated_at"}` (`updated_at` is Unix seconds), or `404`.

## Authentication

The `X-Webhook-Signature` header must contain the hex HMAC-SHA256 of the raw request body, keyed with `WEBHOOK_SECRET`. A `sha256=` prefix is also accepted. Requests with a missing or wrong signature get `401` and change nothing.

## Exactly-once application

Each event is applied in a single SQLite transaction. The transaction inserts the event ID into `processed_events` (primary key), and only if that insert succeeds does it update the order. A redelivered event hits the primary key, so it changes nothing. Because the check and the update share a transaction, concurrent deliveries of the same event are also safe.

Events are applied in arrival order. If a provider delivers a `refund.created` before the `payment.succeeded` for the same order, the order ends in the status of whichever arrived last.

## Running

```sh
go mod tidy                      # generates go.sum for modernc.org/sqlite
export WEBHOOK_SECRET=change-me
go run .                         # ADDR defaults to :8080, DB_PATH to payments.db
go test ./...
```

The SQLite driver is pure Go (`modernc.org/sqlite`), so no C toolchain is needed.
