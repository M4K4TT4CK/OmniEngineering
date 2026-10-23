"""CWE-77: clone a user-supplied repository URL, validated and `--`-separated."""
import subprocess
import tempfile
from urllib.parse import urlparse

from flask import Flask, abort, request

app = Flask(__name__)


@app.post("/imports")
def import_repo() -> dict:
    repo_url = request.json["repo_url"]
    if urlparse(repo_url).scheme != "https":
        abort(400)
    dest = tempfile.mkdtemp()
    subprocess.run(["git", "clone", "--", repo_url, dest], check=True, timeout=120)
    return {"path": dest}


@app.get("/reachable")
def reachable() -> dict:
    url = request.args["url"]
    if url.startswith("-"):
        abort(400)
    out = subprocess.run(["curl", "-sI", url], capture_output=True, timeout=10)
    return {"ok": out.returncode == 0}
