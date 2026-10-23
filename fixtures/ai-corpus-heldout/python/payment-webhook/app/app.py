"""Payment webhook receiver backed by SQLite.

Standard library only: http.server for HTTP, sqlite3 for storage, hmac for
signature checks. Run with PAYMENT_WEBHOOK_SECRET set (see README.md).
"""

import hashlib
import hmac
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SIGNATURE_HEADER = "X-Signature"
MAX_BODY_BYTES = 64 * 1024

# event type -> (statuses it may be applied from, status it moves the order to)
TRANSITIONS = {
    "payment.succeeded": ({"pending", "failed"}, "paid"),
    "payment.failed": ({"pending"}, "failed"),
    "refund.created": ({"paid"}, "refunded"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    amount INTEGER NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processed_events (
    event_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    order_id TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def connect(db_path):
    # autocommit mode so apply_event() controls transactions explicitly
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path):
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    conn.close()


def create_order(db_path, order_id, amount):
    """Insert a pending order. Amount is in minor units (e.g. cents)."""
    conn = connect(db_path)
    conn.execute(
        "INSERT INTO orders (id, amount, status, updated_at) VALUES (?, ?, 'pending', ?)",
        (order_id, amount, now()),
    )
    conn.close()


def verify_signature(secret, body, header):
    """Check X-Signature is the hex HMAC-SHA256 of the raw request body."""
    if not header:
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip())


def parse_event(body):
    """Decode and validate an event body. Raises ValueError on bad input."""
    event = json.loads(body)
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")
    for key in ("id", "type", "order_id"):
        if not isinstance(event.get(key), str) or not event[key]:
            raise ValueError(f"'{key}' must be a non-empty string")
    amount = event.get("amount")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
        raise ValueError("'amount' must be a non-negative integer")
    return event


def apply_event(conn, event):
    """Apply one verified event exactly once. Returns (http_status, payload)."""
    # BEGIN IMMEDIATE takes the write lock up front, so the duplicate check
    # and the status update cannot interleave with a concurrent delivery.
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = _apply(conn, event)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return result


def _apply(conn, event):
    seen = conn.execute(
        "SELECT 1 FROM processed_events WHERE event_id = ?", (event["id"],)
    ).fetchone()
    if seen:
        return 200, {"status": "duplicate"}

    if event["type"] not in TRANSITIONS:
        return 200, {"status": "ignored"}

    order = conn.execute(
        "SELECT amount, status FROM orders WHERE id = ?", (event["order_id"],)
    ).fetchone()
    # Not recorded, so the provider's retry is applied once the order exists.
    if order is None:
        return 404, {"error": "unknown order"}
    if event["type"].startswith("payment.") and event["amount"] != order["amount"]:
        return 422, {"error": "amount mismatch"}

    allowed_from, new_status = TRANSITIONS[event["type"]]
    applied = order["status"] in allowed_from
    if applied:
        conn.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now(), event["order_id"]),
        )
    # Recorded even when ignored (e.g. a refund arriving before payment), so a
    # redelivery does not re-evaluate it against a later state.
    conn.execute(
        "INSERT INTO processed_events (event_id, type, order_id, processed_at) "
        "VALUES (?, ?, ?, ?)",
        (event["id"], event["type"], event["order_id"], now()),
    )
    return 200, {"status": "applied" if applied else "ignored"}


def get_order(conn, order_id):
    return conn.execute(
        "SELECT id, amount, status, updated_at FROM orders WHERE id = ?", (order_id,)
    ).fetchone()


class WebhookHandler(BaseHTTPRequestHandler):
    server_version = "PaymentWebhook/1.0"

    def do_POST(self):
        if self.path != "/webhooks/payments":
            return self._send(404, {"error": "not found"})

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "bad Content-Length"})
        if length > MAX_BODY_BYTES:
            return self._send(413, {"error": "body too large"})
        body = self.rfile.read(length)

        if not verify_signature(self.server.secret, body, self.headers.get(SIGNATURE_HEADER)):
            return self._send(401, {"error": "bad signature"})

        try:
            event = parse_event(body)
        except ValueError as exc:  # includes json.JSONDecodeError
            return self._send(400, {"error": str(exc)})

        conn = connect(self.server.db_path)
        try:
            status, payload = apply_event(conn, event)
        finally:
            conn.close()
        self._send(status, payload)

    def do_GET(self):
        match = re.fullmatch(r"/orders/([^/]+)", self.path)
        if not match:
            return self._send(404, {"error": "not found"})

        conn = connect(self.server.db_path)
        try:
            row = get_order(conn, match.group(1))
        finally:
            conn.close()
        if row is None:
            return self._send(404, {"error": "unknown order"})
        self._send(200, dict(row))

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class WebhookServer(ThreadingHTTPServer):
    def __init__(self, address, db_path, secret):
        self.db_path = db_path
        self.secret = secret.encode()
        init_db(db_path)
        super().__init__(address, WebhookHandler)


def main():
    secret = os.environ["PAYMENT_WEBHOOK_SECRET"]
    db_path = os.environ.get("ORDERS_DB", "orders.db")
    port = int(os.environ.get("PORT", "8000"))
    print(f"listening on :{port}, db={db_path}")
    WebhookServer(("", port), db_path, secret).serve_forever()


if __name__ == "__main__":
    main()
