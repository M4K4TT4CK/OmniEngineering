# Payment webhook receiver

Receives payment provider webhooks, applies each event to its order exactly
once, and serves order status lookups. Standard library only (no install step).
Requires Python 3.9+.

## Files

- `app.py` — HTTP server, signature check, idempotent event handling, SQLite storage
- `test_app.py` — unittest suite (runs the real server on a random local port)

## Running

```sh
export PAYMENT_WEBHOOK_SECRET='shared-secret-from-provider'
export ORDERS_DB=orders.db   # optional, default orders.db
export PORT=8000             # optional, default 8000
python app.py
```

Orders must exist before their events arrive. Create one from Python:

```python
from app import create_order
create_order("orders.db", "order-1", 1500)  # amount in minor units
```

## Endpoints

- `POST /webhooks/payments` — provider webhook.
  - Header `X-Signature`: hex HMAC-SHA256 of the raw request body, keyed with the shared secret.
  - Body: `{"id": "evt_1", "type": "payment.succeeded", "order_id": "order-1", "amount": 1500}`
  - Types: `payment.succeeded`, `payment.failed`, `refund.created`.
- `GET /orders/<order_id>` — returns `{"id", "amount", "status", "updated_at"}` or 404.

Responses for the webhook:

| Status | Meaning | Provider should |
|---|---|---|
| 200 `applied` | State changed | nothing |
| 200 `duplicate` | Event id already processed | nothing |
| 200 `ignored` | Valid, but no state change (see below) | nothing |
| 400 | Malformed body | not retry (fix the sender) |
| 401 | Bad or missing signature | not retry |
| 404 | Order unknown yet | retry later |
| 422 | Event amount differs from order amount | not retry; investigate |

## Exactly-once design

- Each event id is stored in `processed_events` (primary key). A redelivery of a
  processed id returns `duplicate` and changes nothing.
- Each event is applied in a `BEGIN IMMEDIATE` transaction, which serializes
  writers. The duplicate check, status update, and record insert commit together
  or not at all, so concurrent redeliveries apply once (covered by a test).
- Transitions are guarded by the current status:
  - `payment.succeeded` applies from `pending` or `failed` → `paid`
  - `payment.failed` applies from `pending` → `failed`
  - `refund.created` applies from `paid` → `refunded`
- An event that fails its guard is recorded and returned as `ignored`. This means
  a refund that arrives before its payment is dropped permanently. If that race
  matters for your provider, return 409 for it instead so the provider retries.
- Events for unknown orders (404) and amount mismatches (422) are not recorded,
  so a later retry can still apply.

## Tests

```sh
python -m unittest -v
```
