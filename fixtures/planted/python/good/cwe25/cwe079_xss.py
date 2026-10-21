"""CWE-79: a search page that echoes the query back into HTML."""
from flask import Flask, render_template_string, request

app = Flask(__name__)

PAGE = "<h1>Results for {{ q }}</h1><p>No matches.</p>"


@app.get("/search")
def search() -> str:
    q = request.args.get("q", "")
    return render_template_string(PAGE, q=q)
