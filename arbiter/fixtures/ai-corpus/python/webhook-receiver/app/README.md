# Payment webhook receiver

FastAPI + SQLite service that receives payment-provider events, verifies their
HMAC-SHA256 signature, stores them, and forwards successful payments to a
per-merchant callback URL.

## Run

```
pip install -r requirements.txt
export WEBHOOK_SECRET=...        # shared secret with the provider
export API_TOKEN=...             # bearer token for the listing endpoint
export MERCHANT_CALLBACKS='{"m1": "https://merchant.example/hook"}'
export DB_PATH=events.db         # optional, default events.db
uvicorn app:create_app_from_env --factory
```

Tests: `pytest`

## Endpoints

**`POST /webhooks`** – body is a JSON object with string fields `id`, `type`,
`merchant_id` (other fields are kept as-is). `X-Signature` must be the hex
HMAC-SHA256 of the raw request body using `WEBHOOK_SECRET` (a `sha256=` prefix
is tolerated). Responses: `200` (`received` or `duplicate`), `400` malformed
body, `401` missing/invalid signature.

- Events are keyed by `id`; redeliveries are acknowledged but not re-stored or re-forwarded.
- Events of type `payment.succeeded` for a merchant with a configured callback
  are POSTed (same raw body, re-signed with the same secret in `X-Signature`)
  after the response is sent. Outcome is recorded as `forward_status`
  (`delivered`, `failed`, `pending`, or `none`).

**`GET /merchants/{merchant_id}/events?limit=50`** – newest first (`limit` 1–200).
Requires `Authorization: Bearer $API_TOKEN`.

## Limitations

- Forwarding is a single attempt with a 5s timeout and no redirects; failures are
  recorded but not retried. A production system would add a retry queue.
- Merchant callbacks come from the `MERCHANT_CALLBACKS` env var (JSON map), so
  changing them needs a restart. One shared secret is used for all merchants.
- No replay protection beyond event-id de-duplication (no timestamp check).
