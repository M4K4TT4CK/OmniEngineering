"""CWE-434: avatar upload saved under the client's filename, any type."""
import os
import shutil

from django.core.files.storage import FileSystemStorage
from fastapi import FastAPI, UploadFile

from flask import Flask, request

app = Flask(__name__)  # expect: arbiter/python.request-body-unbounded
UPLOAD_DIR = "static/avatars"


@app.post("/avatar")
def upload_avatar() -> dict:
    f = request.files["avatar"]
    f.save(os.path.join(UPLOAD_DIR, f.filename))  # expect: arbiter/python.upload-client-filename
    return {"url": f"/static/avatars/{f.filename}"}


api = FastAPI()


@api.post("/documents")
async def upload_document(file: UploadFile) -> dict:
    with open(f"static/docs/{file.filename}", "wb") as out:  # expect: arbiter/python.upload-client-filename
        shutil.copyfileobj(file.file, out)
    return {"name": file.filename}


def django_upload(request):
    """Django: the uploaded file kept under its own name in MEDIA_ROOT."""
    uploaded = request.FILES["doc"]
    stored = FileSystemStorage().save(uploaded.name, uploaded)  # expect: arbiter/python.upload-client-filename
    return {"name": stored}
