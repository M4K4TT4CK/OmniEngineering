"""CWE-200: an error handler that logs the detail and returns an opaque id."""
import logging
import os
import uuid

from fastapi import FastAPI
from flask import Flask

app = Flask(__name__)
log = logging.getLogger(__name__)


@app.errorhandler(Exception)
def handle_error(exc: Exception) -> tuple[dict, int]:
    incident = uuid.uuid4().hex
    log.exception("unhandled error %s", incident)
    return {"error": "internal error", "incident": incident}, 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=os.environ.get("FLASK_DEBUG") == "1")


api = FastAPI(debug=os.environ.get("API_DEBUG") == "1")
