# Payment Webhook Receiver

Express + SQLite (better-sqlite3) service that receives payment-provider webhooks, verifies their HMAC-SHA256 signature, stores them, and forwards successful payments to a per-merchant callback URL.

## Setup

```
npm install
npm test
WEBHOOK_SECRET=... ADMIN_TOKEN=... npm run dev      # or: npm run build && npm start
```

Environment: `WEBHOOK_SECRET` (shared with the provider, required), `ADMIN_TOKEN` (required), `PORT` (default 3000), `DB_PATH` (default `events.db`). Requires Node 20+.

## API

**`POST /webhooks/payments`** — called by the provider. `X-Signature` must be the hex HMAC-SHA256 of the raw request body using `WEBHOOK_SECRET` (an optional `sha256=` prefix is accepted).
Body: `{ "id": "evt_1", "type": "payment.succeeded", "merchant_id": "m1", ... }`

| Status | Meaning |
| --- | --- |
| 202 | stored; callback delivery started if type is `payment.succeeded` |
| 200 | duplicate event id — already stored, not re-forwarded |
| 400 | bad JSON or missing `id`/`type`/`merchant_id` |
| 401 | missing or invalid signature |
| 404 | unknown merchant |

**`PUT /merchants/:id`** (Bearer `ADMIN_TOKEN`) — body `{ "callback_url": "https://..." }`; creates or updates a merchant. Merchants must exist before their events are accepted.

**`GET /merchants/:id/events?limit=50`** (Bearer `ADMIN_TOKEN`) — most recent events first (limit 1–200), including `delivery_status` (`pending`, `delivered`, `failed`, `not_applicable`) and attempt count.

## Behaviour notes

- The provider is acknowledged before the callback is attempted. Callbacks get a 5s timeout, up to 3 attempts with linear backoff, and no redirect following. The forwarded body is the original event JSON, with an `X-Event-Id` header for merchant-side dedup.
- Retries are in-process: pending deliveries are lost if the process dies mid-retry (they remain `pending` in the DB). A durable queue/sweeper would be the next step.
- Deduplication is by event `id`.
- There is no timestamp check in the signature scheme (none was specified), so a captured signed payload could be replayed; dedup by id blunts this for stored events.
- Callback URLs are only checked to be http(s). If merchants are untrusted, add SSRF protection (block private/loopback addresses) before exposing this.
- The code was written without being run or installed; run `npm test` first.

## Layout

- `src/app.ts` — app factory (routes, signature check, storage, delivery)
- `src/server.ts` — entry point
- `src/app.test.ts` — vitest + supertest tests using in-memory SQLite and a fake `fetch`
