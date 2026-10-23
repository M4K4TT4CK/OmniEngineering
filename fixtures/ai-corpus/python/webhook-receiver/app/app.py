"""Payment webhook receiver.

POST /webhooks                      receive a signed event from the provider
GET  /merchants/{id}/events         list a merchant's recent events (bearer auth)
"""
import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Callable, Optional

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request

SUCCESS_EVENT_TYPE = "payment.succeeded"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    merchant_id     TEXT NOT NULL,
    type            TEXT NOT NULL,
    payload         TEXT NOT NULL,
    received_at     TEXT NOT NULL,
    forward_status  TEXT NOT NULL DEFAULT 'none',  -- none | pending | delivered | failed
    forward_detail  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_merchant ON events (merchant_id, received_at);
"""

# forwarder(url, body, headers) -> HTTP status code; may raise on network errors
Forwarder = Callable[[str, bytes, dict], int]


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def http_forwarder(url: str, body: bytes, headers: dict) -> int:
    with httpx.Client(timeout=5.0, follow_redirects=False) as client:
        return client.post(url, content=body, headers=headers).status_code


def create_app(
    db_path: str,
    secret: str,
    api_token: str,
    callbacks: dict,
    forwarder: Forwarder = http_forwarder,
) -> FastAPI:
    if not secret or not api_token:
        raise ValueError("secret and api_token must be non-empty")
    for merchant, url in callbacks.items():
        if not str(url).startswith(("http://", "https://")):
            raise ValueError(f"callback URL for {merchant!r} must be http(s)")

    app = FastAPI(title="Payment webhook receiver")

    def db() -> sqlite3.Connection:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    with closing(db()) as conn:
        conn.executescript(SCHEMA)

    def set_forward_status(event_id: str, status: str, detail: str) -> None:
        with closing(db()) as conn, conn:
            conn.execute(
                "UPDATE events SET forward_status = ?, forward_detail = ? WHERE event_id = ?",
                (status, detail, event_id),
            )

    def forward(event_id: str, url: str, body: bytes) -> None:
        headers = {"Content-Type": "application/json", "X-Signature": sign(secret, body)}
        try:
            code = forwarder(url, body, headers)
        except Exception as exc:  # network failure, timeout, ...
            set_forward_status(event_id, "failed", type(exc).__name__)
            return
        if 200 <= code < 300:
            set_forward_status(event_id, "delivered", str(code))
        else:
            set_forward_status(event_id, "failed", f"HTTP {code}")

    @app.post("/webhooks")
    async def receive(
        request: Request,
        background: BackgroundTasks,
        x_signature: Optional[str] = Header(default=None),
    ):
        body = await request.body()
        if not x_signature:
            raise HTTPException(401, "missing signature")
        provided = x_signature.removeprefix("sha256=").strip().lower()
        if not hmac.compare_digest(provided, sign(secret, body)):
            raise HTTPException(401, "invalid signature")

        try:
            event = json.loads(body)
        except ValueError:
            raise HTTPException(400, "body is not valid JSON")
        if not isinstance(event, dict) or not all(
            isinstance(event.get(k), str) and event[k] for k in ("id", "type", "merchant_id")
        ):
            raise HTTPException(400, "event must have string fields id, type, merchant_id")

        callback_url = callbacks.get(event["merchant_id"])
        should_forward = event["type"] == SUCCESS_EVENT_TYPE and callback_url is not None

        with closing(db()) as conn, conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO events "
                "(event_id, merchant_id, type, payload, received_at, forward_status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event["id"],
                    event["merchant_id"],
                    event["type"],
                    body.decode(),
                    datetime.now(timezone.utc).isoformat(),
                    "pending" if should_forward else "none",
                ),
            )
            is_new = cur.rowcount == 1

        # Duplicate deliveries are acknowledged but neither re-stored nor re-forwarded.
        if is_new and should_forward:
            background.add_task(forward, event["id"], callback_url, body)
        return {"status": "received" if is_new else "duplicate"}

    @app.get("/merchants/{merchant_id}/events")
    def list_events(
        merchant_id: str,
        limit: int = Query(50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        expected = f"Bearer {api_token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "unauthorized")
        with closing(db()) as conn:
            rows = conn.execute(
                "SELECT event_id, type, payload, received_at, forward_status, forward_detail "
                "FROM events WHERE merchant_id = ? "
                "ORDER BY received_at DESC, rowid DESC LIMIT ?",
                (merchant_id, limit),
            ).fetchall()
        return {
            "merchant_id": merchant_id,
            "events": [
                {
                    "id": r["event_id"],
                    "type": r["type"],
                    "received_at": r["received_at"],
                    "forward_status": r["forward_status"],
                    "forward_detail": r["forward_detail"],
                    "payload": json.loads(r["payload"]),
                }
                for r in rows
            ],
        }

    return app


def create_app_from_env() -> FastAPI:
    """Entry point for `uvicorn app:create_app_from_env --factory`."""
    return create_app(
        db_path=os.environ.get("DB_PATH", "events.db"),
        secret=os.environ["WEBHOOK_SECRET"],
        api_token=os.environ["API_TOKEN"],
        callbacks=json.loads(os.environ.get("MERCHANT_CALLBACKS", "{}")),
    )
