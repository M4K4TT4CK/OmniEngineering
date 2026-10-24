# Payment webhook receiver

Sinatra + SQLite service that receives payment events, verifies their HMAC-SHA256 signature, stores them, and forwards successful payments to a per-merchant callback URL.

## Files

- `app.rb` – the app (`WebhookApp`), SQLite `Store`, and HTTP `Forwarder`
- `config.ru` – configuration from environment and boot
- `app_test.rb` – Minitest + rack-test suite
- `Gemfile`

## Setup and run

```sh
bundle install
export WEBHOOK_SECRET=...      # shared secret with the payment provider
export ADMIN_TOKEN=...         # bearer token for the read endpoint
export MERCHANTS_FILE=merchants.json   # {"m_123": "https://shop.example/payment-callback"}
export DATABASE_PATH=webhooks.db       # optional, default webhooks.db
bundle exec rackup
```

Tests: `bundle exec ruby app_test.rb`

## API

### `POST /webhooks`

JSON body must contain string fields `id`, `type`, `merchant_id` (other fields are kept as-is). `X-Signature` must be the hex HMAC-SHA256 of the **raw body** using `WEBHOOK_SECRET` (an optional `sha256=` prefix is accepted).

| Status | Meaning |
|---|---|
| 200 `received` | stored (and forwarded if `type` is `payment.succeeded`) |
| 200 `duplicate` | event `id` already seen; nothing re-stored or re-forwarded |
| 401 | missing/invalid signature |
| 404 | unknown merchant |
| 413 / 422 | body over 1 MB / malformed event |

### `GET /merchants/:id/events?limit=50`

Requires `Authorization: Bearer $ADMIN_TOKEN`. Returns the merchant's most recent events (newest first, default 50, max 100) including `forward_status`: `none`, `delivered`, or `failed`.

## Behaviour notes

- Signatures are compared in constant time. If `WEBHOOK_SECRET` is unset, all webhooks are rejected.
- Events are de-duplicated by `id`, so provider retries are safe.
- Forwarding is synchronous (3s connect / 5s read timeout) and sends the original raw body. A failed forward is recorded as `failed` but the webhook still returns 200, since the event is safely stored.
- Not implemented: forward retries, signing of forwarded requests, replay-window checks on timestamps. Add a background job for retries if delivery guarantees matter.
- Callback URLs are operator-configured and restricted to http(s); don't let untrusted users set them without SSRF protection.
