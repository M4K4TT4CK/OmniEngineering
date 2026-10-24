# Payment webhook service

A small Java 17 service that receives signed payment webhooks, applies each event to its order exactly once, and stores orders in SQLite.

It uses the JDK's built-in `HttpServer`, `sqlite-jdbc` and Jackson. No framework is needed.

## Files

| File | Purpose |
|---|---|
| `WebhookServer.java` | HTTP endpoints, HMAC verification, payload validation, `main` |
| `PaymentStore.java` | SQLite schema and transactional, idempotent event application |
| `WebhookServerTest.java` | JUnit 5 end-to-end tests against a real server and a temp database |
| `pom.xml` | Maven build (sources are kept flat in this directory) |

## Running

```sh
mvn test                                    # run the tests
WEBHOOK_SECRET=... mvn -q compile exec:java # start the server
```

| Variable | Default | |
|---|---|---|
| `WEBHOOK_SECRET` | (required) | Shared HMAC secret |
| `PORT` | `8080` | Listen port |
| `DB_PATH` | `payments.db` | SQLite file |

## API

### `POST /webhooks`

```json
{"id": "evt_123", "type": "payment.succeeded", "order_id": "ord_42", "amount": 1999}
```

- `X-Signature` header: the hex HMAC-SHA256 of the **raw request body**, computed with the shared secret. A `sha256=` prefix is also accepted.
- `type` is one of `payment.succeeded`, `payment.failed` or `refund.created`.
- `amount` is a positive integer in minor units, such as cents. Fractional or string amounts are rejected.

| Response | Meaning |
|---|---|
| `200 {"result":"applied"}` | Event applied |
| `200 {"result":"duplicate"}` | Already applied; acknowledged so the provider stops retrying |
| `400` | Signature valid but payload invalid |
| `401` | Signature missing or wrong |
| `409` | Event id already used for an event with *different* content |
| `413` | Body larger than 64 KiB |
| `500` | Nothing was committed; the provider's retry will apply the event |

### `GET /orders/{orderId}`

```json
{"order_id":"ord_42","status":"PAID","amount_paid":1999,"amount_refunded":0,"updated_at":"2026-10-23T12:00:00Z"}
```

Returns `404` if no event has been received for the order.

## Design notes

- **Exactly once.** The event id goes into `processed_events` (primary key) in the same SQLite transaction that updates the order, so either both writes happen or neither does.
  - A redelivery after success is detected and answered with `200 duplicate`.
  - A failure part-way rolls everything back, so the retry applies the event cleanly.
  - Within one process, writes are serialised on a single connection. If several processes share the database file, the primary key and SQLite locking still prevent double application: the loser gets an error, and the provider's retry then sees a duplicate.
- **Signature first.** The service checks the HMAC on the raw bytes before it parses anything, and uses a constant-time comparison.
- **Delivery order doesn't matter.** An order keeps running totals (`amount_paid`, `amount_refunded`) and a "failure seen" flag, and its status is derived from those:
  - `PAID`, `PARTIALLY_REFUNDED` or `REFUNDED` when anything has been paid
  - otherwise `PAYMENT_FAILED` or `PENDING`

  Because of this, a late `payment.failed` doesn't override a success, and a refund that arrives before its payment resolves correctly once the payment arrives.
- **Orders are created on first event.** There is no separate order-creation API.
- **Limitations.**
  - The payload has no timestamp, so there is no replay window. Replays are harmless because of deduplication, but a captured request can still be resent.
  - Currency is not modelled.
