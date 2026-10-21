"""CWE-79: a search page that echoes the query back into HTML."""
from flask import Flask, request

app = Flask(__name__)


@app.get("/search")
def search() -> str:
    q = request.args.get("q", "")
    return f"<h1>Results for {q}</h1><p>No matches.</p>"  # expect-ext: semgrep/semgrep-rules.python.flask.security.injection.raw-html-format
