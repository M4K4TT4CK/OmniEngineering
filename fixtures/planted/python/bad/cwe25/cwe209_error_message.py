"""CWE-200/209: a catch-all handler that returns the exception's text."""
import sqlite3

from flask import Flask, jsonify, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024


@app.post("/notes")
def add_note():
    try:
        with sqlite3.connect("notes.db") as db:
            db.execute("INSERT INTO notes (body) VALUES (?)", (request.json["body"],))
    except Exception as e:
        return jsonify(error=str(e)), 500  # expect: arbiter/python.error-message-to-client
    return jsonify(ok=True), 201
