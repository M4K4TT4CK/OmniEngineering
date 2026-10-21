"""CWE-918: fetch a preview of a user-supplied link."""
import requests
from flask import Flask, request

app = Flask(__name__)


@app.get("/preview")
def preview() -> dict:
    resp = requests.get(request.args["url"], timeout=5)  # expect-ext: semgrep/semgrep-rules.python.django.security.injection.ssrf.ssrf-injection-requests
    return {"status": resp.status_code, "title": resp.text[:200]}
