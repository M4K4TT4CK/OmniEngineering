"""CWE-434: avatar upload with a type allowlist and a server-chosen name."""
import os
import uuid

from flask import Flask, abort, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
UPLOAD_DIR = "static/avatars"
ALLOWED = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


@app.post("/avatar")
def upload_avatar() -> dict:
    f = request.files["avatar"]
    ext = os.path.splitext(f.filename or "")[1].lower()
    if ALLOWED.get(ext) != f.mimetype:
        abort(415)
    name = f"{uuid.uuid4().hex}{ext}"
    f.save(os.path.join(UPLOAD_DIR, name))
    return {"url": f"/static/avatars/{name}"}
