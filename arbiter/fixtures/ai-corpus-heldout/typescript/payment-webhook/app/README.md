# payment-webhook

A small Node.js/TypeScript service that receives signed payment webhooks, applies each event to its order exactly once, and stores the results in SQLite.

It uses only `node:http` plus [`better-sqlite3`](https://github.com/WiseLibs/better-sqlite3), which provides synchronous transactions.

## Files

| File | Purpose |
| --- | --- |
| `src/app.ts` | Signature check, payload validation, SQLite store, HTTP routes |
| `src/server.ts` | Entry point; reads configuration from env vars |
| `test/app.test.ts` | Tests (`node:test`, run against a real server and a temporary DB) |

## Running

```sh
npm install
WEBHOOK_SECRET=... npm start      # PORT (default 3000), DATABASE_PATH (default payments.db)
npm test
```

Requires Node.js 20 or later.

## API

### `POST /webhooks/payments`

```json
{ "id": "evt_123", "type": "payment.succeeded", "order_id": "ord_42", "amount": 1999 }
```

- `type` is one of `payment.succeeded`, `payment.failed` or `refund.created`.
- `amount` is a positive integer in minor units (for example, cents).
- `X-Signature` must be the hex HMAC-SHA256 of the **raw request body**, computed with the shared secret. A `sha256=` prefix is optional.

| Response | Meaning |
| --- | --- |
| `200 {"result":"applied"}` | Event applied |
| `200 {"result":"duplicate"}` | Event already processed. Returns 2xx so the provider stops retrying. |
| `401` | Signature is missing or invalid |
| `400` | Payload is signed but invalid |
| `409` | This event id was already used with a different type, order or amount. Nothing is applied. |
| `413` | Body is larger than 64 KiB |
| `5xx` | Nothing was applied, so it is safe for the provider to retry |

### `GET /orders/:orderId`

```json
{ "order_id": "ord_42", "status": "paid", "amount_paid": 1999, "amount_refunded": 0,
  "failed_attempts": 0, "updated_at": "2026-10-22T10:00:00.000Z" }
```

Returns `404` if no event for the order has been received.

## Design notes

- **Exactly-once.** Each event is handled in one `BEGIN IMMEDIATE` transaction that does three things: checks `processed_events` for the event id, records the id, and updates the order. Either all three happen or none do. The immediate write lock means two concurrent deliveries can't both pass the check. This holds even across several processes sharing the same DB file.
- **Retries vs. id reuse.** Each event id is stored with a fingerprint of `(type, order_id, amount)`. The fingerprint does not depend on the raw bytes, so a retry whose JSON has been re-serialized still counts as a duplicate. If the same id arrives with different content, the service returns 409 and applies nothing.
- **Order-independent status.** Orders store running totals (`amount_paid`, `amount_refunded`, `failed_attempts`), and the status is derived from those totals. The status therefore does not depend on which event arrived last. A late `payment.failed` won't downgrade a paid order, and a refund that arrives before its payment still ends up `refunded`.
  - Status rules: `refunded` if refunded ≥ paid > 0; `partially_refunded` if refunded > 0; `paid` if paid > 0; `failed` if there has been at least one failure; otherwise `pending`.
- **Signature first.** The HMAC is checked against the exact bytes received, using a constant-time comparison, before any JSON parsing.
- **Orders are created on first event.** This service does not manage order creation, so it records any order id that appears in a valid signed event.

## Limitations and possible extensions

- **No timestamp in the signature.** Replaying a captured request is harmless because of deduplication. However, the service can't reject old requests. If the provider signs a timestamp, add a check for it.
- **No currency handling.** Amounts are assumed to be in a single currency.
- **Order lookup is unauthenticated.** Put it behind your own auth or an internal network.
