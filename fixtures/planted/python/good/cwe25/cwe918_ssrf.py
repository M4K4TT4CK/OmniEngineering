"""CWE-918: fetch a preview of a link, only from allowlisted hosts."""
from urllib.parse import urlparse

import requests
from flask import Flask, abort, request

app = Flask(__name__)
ALLOWED_HOSTS = {"github.com", "gitlab.com", "docs.python.org"}


@app.get("/preview")
def preview() -> dict:
    parsed = urlparse(request.args["url"])
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        abort(400)
    resp = requests.get(parsed.geturl(), timeout=5, allow_redirects=False)
    return {"status": resp.status_code, "title": resp.text[:200]}
