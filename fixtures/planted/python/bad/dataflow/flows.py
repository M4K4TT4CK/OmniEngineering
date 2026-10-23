"""One-hop data flow (Milestone 3): each defect is one step from its sink."""
import os
import subprocess

from flask import Flask, request

app = Flask(__name__)
UPLOAD_DIR = "uploads"


def archive_command(name):
    return "tar czf /exports/" + name + ".tgz /data/" + name


@app.post("/archive")
def archive():
    cmd = archive_command(request.form["name"])
    subprocess.run(cmd, shell=True, check=True, timeout=60)  # expect: arbiter/python.command-injection
    return "", 204


@app.post("/mirror")
def mirror():
    repo = request.json["repo"]
    argv = ["git", "clone", repo, "/srv/mirror"]
    subprocess.run(argv, check=True, timeout=120)  # expect: arbiter/python.argument-injection
    return "", 201


@app.post("/avatar")
def avatar():
    upload = request.files["avatar"]
    target = os.path.join(UPLOAD_DIR, upload.filename)
    upload.save(target)  # expect: arbiter/python.upload-client-filename
    return "", 201
