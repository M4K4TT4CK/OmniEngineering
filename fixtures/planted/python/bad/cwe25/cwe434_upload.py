"""CWE-434: avatar upload saved under the client's filename, any type."""
import os

from flask import Flask, request

app = Flask(__name__)
UPLOAD_DIR = "static/avatars"


@app.post("/avatar")
def upload_avatar() -> dict:
    f = request.files["avatar"]
    f.save(os.path.join(UPLOAD_DIR, f.filename))  # gap: CWE-434 - nothing in this build reports it (docs/cwe-top25.md)
    return {"url": f"/static/avatars/{f.filename}"}
