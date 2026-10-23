# Payment webhook service

Receives payment provider webhooks, applies each event to its order exactly once, and exposes order status over HTTP. Orders are stored in SQLite.

## Setup

Requires Ruby 3.x.

```sh
bundle install
```

## Configuration

| Variable         | Default            | Purpose                                      |
|------------------|--------------------|----------------------------------------------|
| `WEBHOOK_SECRET` | (none)             | Shared HMAC secret. If unset, all webhooks are rejected. |
| `DATABASE_PATH`  | `orders.sqlite3`   | SQLite database file.                        |

## Running

```sh
WEBHOOK_SECRET=... bundle exec ruby app.rb
```

## Endpoints

### `POST /webhooks/payments`

Body is JSON:

```json
{ "id": "evt_123", "type": "payment.succeeded", "order_id": "order-1", "amount": 1500 }
```

- `type` is one of `payment.succeeded`, `payment.failed`, `refund.created`.
- `amount` is an integer in minor units (e.g. cents).
- The signature header `X-Provider-Signature` must be `sha256=<hex>`, where `<hex>` is the HMAC-SHA256 of the raw request body using the shared secret.

Responses:

| Status | Body `result` / `error`        | Meaning                                                  |
|--------|--------------------------------|----------------------------------------------------------|
| 200    | `applied`                      | Event changed the order's status.                        |
| 200    | `duplicate`                    | Event id was already processed; nothing changed.         |
| 200    | `ignored`                      | Valid event that does not apply to the order's current status. |
| 200    | `unsupported`                  | Event type not handled; not recorded.                    |
| 400    | `malformed event`              | Invalid JSON or missing/mistyped fields.                 |
| 401    | `invalid signature`            | Missing or wrong signature.                              |

### `GET /orders/:id`

Returns `{ "id", "amount", "status", "updated_at" }`, or 404 if the order does not exist.

## Design

- **Exactly-once application.** Each event id is recorded in `processed_events`. The duplicate check, the order update and the event record run in one `BEGIN IMMEDIATE` transaction, so a redelivery never applies twice and a failure leaves no partial state.
- **Status transitions.** `payment.succeeded` and `payment.failed` apply only to `pending` orders; `refund.created` applies only to `paid` orders. Other combinations are recorded as `ignored`, so a late or out-of-order event cannot move an order backwards.
- **Unknown orders** are created as `pending` with the amount from the first event that names them.
- **Signatures** are compared in constant time.

## Limitations

- An event that arrives before its prerequisite (e.g. `refund.created` before `payment.succeeded`) is recorded as `ignored` and is not re-applied if the prerequisite arrives later. If the provider can reorder events, the status logic needs to be revisited.
- The amount in events is not checked against the amount already stored on the order.
- A single process serializes database access with a mutex. Multiple processes sharing one database file rely on SQLite's locking.

## Tests

```sh
bundle exec ruby webhook_test.rb
```
