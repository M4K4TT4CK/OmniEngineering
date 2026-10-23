"""Webhook forwarding as model-written receivers ship it (ARB-083)."""
import httpx


def deliver(merchant: dict, body: bytes) -> int:
    url = merchant["callback_url"]
    return httpx.post(url, content=body, timeout=5).status_code  # expect: arbiter/python.ssrf-stored-callback
