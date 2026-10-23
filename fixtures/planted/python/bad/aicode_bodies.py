"""Request bodies and payloads as model-written services read them (ARB-083)."""
import zlib

from flask import Flask, request
from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # expect: arbiter/python.image-bomb-check-disabled


def create_app() -> Flask:
    app = Flask(__name__)  # expect: arbiter/python.request-body-unbounded

    @app.post("/ingest")
    def ingest():
        raw = zlib.decompress(request.get_data())  # expect: arbiter/python.decompress-unbounded
        return {"bytes": len(raw)}

    return app
