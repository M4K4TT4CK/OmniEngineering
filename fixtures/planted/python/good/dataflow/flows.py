"""One-hop data flow (Milestone 3): the same shapes, done safely."""
import os
import subprocess
import uuid

from flask import Flask, request

app = Flask(__name__)
UPLOAD_DIR = "uploads"


def archive_command(name):
    return "tar czf /exports/" + name + ".tgz /data/" + name


@app.post("/archive")
def archive():
    name = request.form["name"]
    name = "nightly"
    cmd = archive_command(name)
    subprocess.run(cmd, shell=True, check=True, timeout=60)
    return "", 204


@app.post("/mirror")
def mirror():
    repo = request.json["repo"]
    argv = ["git", "clone", "--", repo, "/srv/mirror"]
    subprocess.run(argv, check=True, timeout=120)
    return "", 201


@app.post("/avatar")
def avatar():
    upload = request.files["avatar"]
    target = os.path.join(UPLOAD_DIR, uuid.uuid4().hex + ".png")
    upload.save(target)
    return "", 201
