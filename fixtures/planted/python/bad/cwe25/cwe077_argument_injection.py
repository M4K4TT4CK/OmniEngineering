"""CWE-77: clone a user-supplied repository URL (argument injection, CWE-88)."""
import subprocess
import tempfile

from flask import Flask, request

app = Flask(__name__)


@app.post("/imports")
def import_repo() -> dict:
    repo_url = request.json["repo_url"]
    dest = tempfile.mkdtemp()
    subprocess.run(["git", "clone", repo_url, dest], check=True, timeout=120)  # gap: CWE-77 - nothing in this build reports it (docs/cwe-top25.md)
    return {"path": dest}
