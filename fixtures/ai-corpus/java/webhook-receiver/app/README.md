# Payment Webhook Receiver

Small Java 17 service (JDK `HttpServer`, Jackson, SQLite) that receives payment events,
verifies their HMAC-SHA256 signature, stores them, and forwards successful payments to a
per-merchant callback URL.

## Run

    export WEBHOOK_SECRET=shared-secret-with-provider
    export ADMIN_TOKEN=some-long-random-token
    mvn compile exec:java            # optional: PORT (default 8080), DB_PATH (default webhooks.db)

Test with `mvn test`.

## API

**`POST /webhooks/payments`** — called by the provider. `X-Signature` must be the hex
HMAC-SHA256 of the raw request body, keyed with `WEBHOOK_SECRET`. Body:

    {"id": "evt_1", "type": "payment.succeeded", "merchant_id": "m1", "data": {...}}

`id`, `type` and `merchant_id` are required strings. Responses: `200` (stored, or already
seen — duplicates by `id` are acknowledged but not re-forwarded), `400` malformed, `401` bad or
missing signature, `413` body over 1 MB.

**`PUT /merchants/{id}/callback`** — body `{"callback_url": "https://..."}` sets the merchant's callback.
**`GET /merchants/{id}/events?limit=N`** — recent events, newest first (default 50, max 200), each with
`id`, `type`, `payload`, `received_at` (epoch ms) and `delivery_status`.

Both merchant endpoints require `Authorization: Bearer $ADMIN_TOKEN`.

## Forwarding

Events of type `payment.succeeded` are POSTed (raw body, original `X-Signature`, plus `X-Event-Id`)
to the merchant's callback URL after being stored. Delivery is asynchronous, with up to 3
attempts and increasing delays; any 2xx counts as success. `delivery_status` is one of
`not_applicable`, `no_callback`, `pending`, `delivered`, `failed`.

## Notes and limitations

- Pending deliveries are not resumed after a restart, and failed ones are not retried later.
- Callback URLs are limited to http/https and redirects are not followed, but the service does not block
  private or internal addresses. Only trusted admins should set callbacks; add an allowlist or
  egress filtering for production.
- There is no timestamp in the signed payload, so captured valid requests could be replayed
  (duplicate event ids are ignored, which limits the impact).
- Run behind TLS (e.g. a reverse proxy).
