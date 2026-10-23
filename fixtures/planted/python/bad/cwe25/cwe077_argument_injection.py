"""CWE-77: clone a user-supplied repository URL (argument injection, CWE-88)."""
import subprocess
import tempfile

from flask import Flask, request

app = Flask(__name__)


@app.post("/imports")
def import_repo() -> dict:
    repo_url = request.json["repo_url"]
    dest = tempfile.mkdtemp()
    subprocess.run(["git", "clone", repo_url, dest], check=True, timeout=120)  # expect: arbiter/python.argument-injection
    return {"path": dest}


@app.get("/reachable")
def reachable() -> dict:
    out = subprocess.run(["curl", "-sI", request.args["url"]], capture_output=True, timeout=10)  # expect: arbiter/python.argument-injection
    return {"ok": out.returncode == 0}
