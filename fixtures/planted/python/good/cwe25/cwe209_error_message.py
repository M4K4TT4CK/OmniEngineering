"""CWE-200/209: the handler logs the failure and returns a fixed message."""
import logging
import sqlite3

from flask import Flask, jsonify, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
log = logging.getLogger(__name__)


@app.post("/notes")
def add_note():
    try:
        body = request.json["body"]
    except (KeyError, TypeError) as e:
        return jsonify(error=f"missing field: {e}"), 400
    try:
        with sqlite3.connect("notes.db") as db:
            db.execute("INSERT INTO notes (body) VALUES (?)", (body,))
    except Exception:
        log.exception("could not store note")
        return jsonify(error="could not store the note"), 500
    return jsonify(ok=True), 201
