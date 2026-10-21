"""CWE-22: download a generated report by name."""
from pathlib import Path

from flask import Flask, Response, abort, request

app = Flask(__name__)
REPORTS_DIR = Path("/srv/reports").resolve()


@app.get("/reports")
def download_report() -> Response:
    target = (REPORTS_DIR / request.args["name"]).resolve()
    if not target.is_relative_to(REPORTS_DIR) or not target.is_file():
        abort(404)
    return Response(target.read_bytes(), mimetype="application/pdf")
