"""CWE-200: an error handler that hands the traceback to the client."""
import traceback

from fastapi import FastAPI
from flask import Flask

app = Flask(__name__)


@app.errorhandler(Exception)
def handle_error(exc: Exception) -> tuple[dict, int]:
    return {"error": str(exc), "trace": traceback.format_exc()}, 500  # expect: arbiter/python.error-traceback-to-client


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)  # expect: arbiter/python.debug-mode-enabled expect-ext: semgrep/semgrep-rules.python.flask.security.audit.debug-enabled, bandit/B201


api = FastAPI(debug=True)  # expect: arbiter/python.debug-mode-enabled
