"""File upload service: upload, list, download, delete and thumbnail endpoints."""
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename

THUMB_DIR_NAME = ".thumbnails"  # secure_filename never yields a leading dot, so no clash
DEFAULT_THUMB_SIZE = 128
MAX_THUMB_SIZE = 1024


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        UPLOAD_DIR=os.environ.get("UPLOAD_DIR", "uploads"),
        MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_BYTES", 50 * 1024 * 1024)),
    )
    if config:
        app.config.update(config)

    root = Path(app.config["UPLOAD_DIR"]).resolve()
    thumbs = root / THUMB_DIR_NAME
    thumbs.mkdir(parents=True, exist_ok=True)

    def error(message, status):
        return jsonify(error=message), status

    def file_path(name):
        """Return the path for a stored file name, or None if the name is not valid."""
        if not name or secure_filename(name) != name:
            return None
        return root / name

    @app.errorhandler(413)
    def too_large(_):
        return error("file too large", 413)

    @app.post("/files")
    def upload():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return error("multipart field 'file' is required", 400)
        name = secure_filename(upload.filename)
        if not name:
            return error("invalid file name", 400)
        overwrite = request.args.get("overwrite", "").lower() in ("1", "true", "yes")
        path = root / name
        try:
            with open(path, "wb" if overwrite else "xb") as out:
                shutil.copyfileobj(upload.stream, out)
        except FileExistsError:
            return error(f"'{name}' already exists (use ?overwrite=true)", 409)
        shutil.rmtree(thumbs / name, ignore_errors=True)  # drop stale thumbnails
        return jsonify(name=name, size=path.stat().st_size), 201

    @app.get("/files")
    def list_files():
        items = []
        for entry in sorted(root.iterdir()):
            if not entry.is_file():
                continue
            st = entry.stat()
            items.append(
                {
                    "name": entry.name,
                    "size": st.st_size,
                    "uploaded_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
                }
            )
        return jsonify(items)

    @app.get("/files/<name>")
    def download(name):
        path = file_path(name)
        if path is None or not path.is_file():
            return error("not found", 404)
        return send_from_directory(root, name, as_attachment=True)

    @app.delete("/files/<name>")
    def delete(name):
        path = file_path(name)
        if path is None or not path.is_file():
            return error("not found", 404)
        path.unlink()
        shutil.rmtree(thumbs / name, ignore_errors=True)
        return "", 204

    @app.get("/files/<name>/thumbnail")
    def thumbnail(name):
        path = file_path(name)
        if path is None or not path.is_file():
            return error("not found", 404)
        try:
            size = int(request.args.get("size", DEFAULT_THUMB_SIZE))
        except ValueError:
            return error("size must be an integer", 400)
        if not 1 <= size <= MAX_THUMB_SIZE:
            return error(f"size must be between 1 and {MAX_THUMB_SIZE}", 400)

        thumb_path = thumbs / name / f"{size}.png"
        if not thumb_path.is_file():
            try:
                with Image.open(path) as img:
                    img.thumbnail((size, size))
                    if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                        img = img.convert("RGB")
                    thumb_path.parent.mkdir(exist_ok=True)
                    tmp = thumb_path.with_suffix(".tmp")
                    img.save(tmp, format="PNG")
                    os.replace(tmp, thumb_path)
            except (UnidentifiedImageError, Image.DecompressionBombError):
                return error("file is not a supported image", 415)
        return send_from_directory(thumb_path.parent, thumb_path.name, mimetype="image/png")

    return app


if __name__ == "__main__":
    create_app().run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)))
