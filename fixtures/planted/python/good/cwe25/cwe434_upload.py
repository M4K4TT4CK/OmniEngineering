"""CWE-434: avatar upload with a type allowlist and a server-chosen name."""
import os
import shutil
import uuid

from django.core.files.storage import FileSystemStorage
from fastapi import FastAPI, HTTPException, UploadFile

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


api = FastAPI()
DOC_TYPES = {"application/pdf": ".pdf"}


@api.post("/documents")
async def upload_document(file: UploadFile) -> dict:
    ext = DOC_TYPES.get(file.content_type or "")
    if ext is None:
        raise HTTPException(status_code=415)
    name = f"{uuid.uuid4().hex}{ext}"
    with open(f"var/docs/{name}", "wb") as out:
        shutil.copyfileobj(file.file, out)
    return {"name": name}


def django_upload(request):
    """Django: a server-chosen name for an allowlisted type."""
    uploaded = request.FILES["doc"]
    if uploaded.content_type != "application/pdf":
        return {"error": "unsupported type"}
    stored = FileSystemStorage().save(f"{uuid.uuid4().hex}.pdf", uploaded)
    return {"name": stored}
