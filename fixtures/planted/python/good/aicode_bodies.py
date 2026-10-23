"""Request bodies and payloads read with limits (ARB-083)."""
import zlib

from flask import Flask, abort, request

MAX_INFLATED = 16 * 1024 * 1024


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

    @app.post("/ingest")
    def ingest():
        d = zlib.decompressobj()
        raw = d.decompress(request.get_data(), MAX_INFLATED)
        if d.unconsumed_tail:
            abort(413)
        return {"bytes": len(raw)}

    return app
