"""CSV customer import service: upload a CSV, import it in the background, poll progress and errors."""

import csv
import io
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from flask import Flask, jsonify, request

REQUIRED_COLUMNS = ["name", "email", "phone", "country", "signup_date"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9 \-()]{5,20}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    email TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT,
    country TEXT NOT NULL,
    signup_date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS imports (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    total_rows INTEGER NOT NULL DEFAULT 0,
    processed_rows INTEGER NOT NULL DEFAULT 0,
    inserted_rows INTEGER NOT NULL DEFAULT 0,
    updated_rows INTEGER NOT NULL DEFAULT 0,
    failed_rows INTEGER NOT NULL DEFAULT 0,
    fatal_error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS import_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id TEXT NOT NULL REFERENCES imports(id),
    row_number INTEGER NOT NULL,
    field TEXT,
    message TEXT NOT NULL
);
"""

UPSERT_SQL = """
INSERT INTO customers (email, name, phone, country, signup_date)
VALUES (:email, :name, :phone, :country, :signup_date)
ON CONFLICT(email) DO UPDATE SET
    name = excluded.name,
    phone = excluded.phone,
    country = excluded.country,
    signup_date = excluded.signup_date
"""


def utcnow():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path):
    with db_connection(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)


def validate_row(row):
    """Return (record, errors). record is None when errors is non-empty.

    errors is a list of (field, message) tuples.
    """
    errors = []
    name = (row.get("name") or "").strip()
    email = (row.get("email") or "").strip().lower()
    phone = (row.get("phone") or "").strip()
    country = (row.get("country") or "").strip().upper()
    signup_raw = (row.get("signup_date") or "").strip()

    if not name:
        errors.append(("name", "name is required"))
    if not EMAIL_RE.match(email):
        errors.append(("email", "email is not valid"))
    if phone and not PHONE_RE.match(phone):
        errors.append(("phone", "phone is not valid"))
    if not COUNTRY_RE.match(country):
        errors.append(("country", "country must be a 2-letter ISO code"))

    signup_date = None
    try:
        signup_date = datetime.strptime(signup_raw, "%Y-%m-%d").date().isoformat()
    except ValueError:
        errors.append(("signup_date", "signup_date must be YYYY-MM-DD"))

    if errors:
        return None, errors
    record = {
        "email": email,
        "name": name,
        "phone": phone or None,
        "country": country,
        "signup_date": signup_date,
    }
    return record, []


def process_row(conn, import_id, row_number, row):
    record, errors = validate_row(row)
    inserted = updated = 0
    if errors:
        conn.executemany(
            "INSERT INTO import_errors (import_id, row_number, field, message) VALUES (?, ?, ?, ?)",
            [(import_id, row_number, field, message) for field, message in errors],
        )
    else:
        existed = conn.execute(
            "SELECT 1 FROM customers WHERE email = ?", (record["email"],)
        ).fetchone()
        conn.execute(UPSERT_SQL, record)
        inserted, updated = (0, 1) if existed else (1, 0)

    conn.execute(
        """UPDATE imports SET processed_rows = processed_rows + 1,
               inserted_rows = inserted_rows + ?,
               updated_rows = updated_rows + ?,
               failed_rows = failed_rows + ?
           WHERE id = ?""",
        (inserted, updated, 1 if errors else 0, import_id),
    )
    conn.commit()


def finish_import(conn, import_id, status, fatal_error=None):
    conn.execute(
        "UPDATE imports SET status = ?, fatal_error = ?, finished_at = ? WHERE id = ?",
        (status, fatal_error, utcnow(), import_id),
    )
    conn.commit()


def run_import(db_path, import_id, text):
    """Worker body. Runs in a background thread with its own connection."""
    try:
        with db_connection(db_path) as conn:
            conn.execute("UPDATE imports SET status = 'running' WHERE id = ?", (import_id,))
            reader = csv.DictReader(io.StringIO(text))
            missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                finish_import(conn, import_id, "failed", f"missing columns: {', '.join(missing)}")
                return

            rows = list(reader)
            conn.execute("UPDATE imports SET total_rows = ? WHERE id = ?", (len(rows), import_id))
            conn.commit()
            # row_number counts data rows, starting at 1 (the header is not counted).
            for row_number, row in enumerate(rows, start=1):
                process_row(conn, import_id, row_number, row)
            finish_import(conn, import_id, "completed")
    except Exception as exc:  # a crashed worker must still leave a readable status
        with db_connection(db_path) as conn:
            finish_import(conn, import_id, "failed", f"unexpected error: {exc}")


def create_app(db_path=None):
    app = Flask(__name__)
    db_path = db_path or os.environ.get("CUSTOMER_DB", "customers.db")
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
    init_db(db_path)

    @app.post("/imports")
    def start_import():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify(error="send the CSV as multipart field 'file'"), 400
        try:
            text = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            return jsonify(error="file must be UTF-8 encoded"), 400

        import_id = uuid.uuid4().hex
        with db_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO imports (id, status, created_at) VALUES (?, 'queued', ?)",
                (import_id, utcnow()),
            )
        threading.Thread(target=run_import, args=(db_path, import_id, text), daemon=True).start()
        return jsonify(id=import_id, status="queued"), 202

    @app.get("/imports/<import_id>")
    def get_import(import_id):
        limit = max(1, min(request.args.get("limit", 100, type=int), 1000))
        offset = max(0, request.args.get("offset", 0, type=int))
        with db_connection(db_path) as conn:
            job = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id,)).fetchone()
            if job is None:
                return jsonify(error="import not found"), 404
            errors = conn.execute(
                """SELECT row_number, field, message FROM import_errors
                   WHERE import_id = ? ORDER BY id LIMIT ? OFFSET ?""",
                (import_id, limit, offset),
            ).fetchall()
            error_count = conn.execute(
                "SELECT COUNT(*) FROM import_errors WHERE import_id = ?", (import_id,)
            ).fetchone()[0]

        body = dict(job)
        body["error_count"] = error_count
        body["errors"] = [dict(e) for e in errors]
        return jsonify(body)

    return app


if __name__ == "__main__":
    create_app().run(port=8000)
