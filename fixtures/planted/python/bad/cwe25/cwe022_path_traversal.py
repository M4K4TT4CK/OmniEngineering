"""CWE-22: download a generated report by name."""
import os

from flask import Flask, Response, request

app = Flask(__name__)
REPORTS_DIR = "/srv/reports"


@app.get("/reports")
def download_report() -> Response:
    name = request.args["name"]
    with open(os.path.join(REPORTS_DIR, name), "rb") as fh:  # expect-ext: semgrep/semgrep-rules.python.flask.security.injection.path-traversal-open
        return Response(fh.read(), mimetype="application/pdf")
