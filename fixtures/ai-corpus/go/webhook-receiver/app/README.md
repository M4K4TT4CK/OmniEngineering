# Webhook receiver

Receives JSON payment events, verifies the `X-Signature` HMAC-SHA256 header,
stores events in SQLite, and forwards `payment.succeeded` events to a
per-merchant callback URL. Standard library plus `modernc.org/sqlite`
(pure Go, no cgo).

## Setup

`go.sum` is not included. Generate it once with `go mod tidy`.

```
export WEBHOOK_SECRET=...   # shared secret with the payment provider (required)
export ADMIN_TOKEN=...      # bearer token for the management endpoints (required)
export DB_PATH=events.db    # optional, default events.db
export LISTEN_ADDR=:8080    # optional, default :8080
go run .
go test ./...
```

## Endpoints

### `POST /webhooks`
Body is the event JSON, which must contain `id`, `type` and `merchant_id`.
`X-Signature` is the hex HMAC-SHA256 of the raw body, keyed with
`WEBHOOK_SECRET` (an optional `sha256=` prefix is accepted).

- `202` stored (and queued for forwarding, if applicable)
- `200` duplicate `id`: acknowledged, not stored or forwarded again
- `400` malformed payload, `401` bad or missing signature, `413` body over 1 MiB

### `PUT /merchants/{id}` (admin)
Sets the callback URL: `{"callback_url": "https://merchant.example/hook"}`.
Only absolute http(s) URLs are accepted.

### `GET /merchants/{id}/events?limit=50` (admin)
Newest first. `limit` is 1–200 (default 50). Each item has the stored payload
and its `delivery_status` (`none`, `pending`, `delivered`, `failed`) and
`delivery_tries`.

Admin endpoints need `Authorization: Bearer $ADMIN_TOKEN`.

## Forwarding

Only events with `type == "payment.succeeded"` for a merchant with a callback
URL are forwarded. The original body is POSTed with an `X-Signature` computed
with the same secret. A 2xx response counts as delivered. Otherwise it retries
up to 3 attempts with linear backoff (2s, 4s), then marks the event `failed`.
Redirects are not followed.

## Limitations

- Retries run in memory. Events still `pending` when the process stops are not
  resumed on restart.
- Callback URLs are set by admins and are not checked against private address
  ranges. If admins aren't fully trusted, add egress filtering.
- No timestamp in the signature scheme, so replay protection is limited to
  event-ID de-duplication.
