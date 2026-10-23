"""Webhook forwarding that refuses internal addresses (ARB-083)."""
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx


def deliver(merchant: dict, body: bytes) -> int:
    url = merchant["callback_url"]
    host = urlsplit(url).hostname or ""
    infos = socket.getaddrinfo(host, None)
    if not infos or not all(ipaddress.ip_address(i[4][0]).is_global for i in infos):
        return -1
    return httpx.post(url, content=body, timeout=5).status_code
