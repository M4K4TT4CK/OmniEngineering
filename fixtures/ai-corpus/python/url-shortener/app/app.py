"""URL shortener: Flask + SQLite."""
import ipaddress
import re
import secrets
import socket
import sqlite3
import string
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse

from flask import Flask, g, jsonify, redirect, request

ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
RESERVED = {"api"}
CODE_ALPHABET = string.ascii_letters + string.digits
MAX_URL_LEN = 2048
MAX_BODY_BYTES = 256 * 1024
FETCH_TIMEOUT = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    code       TEXT PRIMARY KEY,
    url        TEXT NOT NULL,
    title      TEXT,
    clicks     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
)
"""


class _TitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.done = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "title" and not self.done:
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title" and self.in_title:
            self.in_title = False
            self.done = True

    def handle_data(self, data):
        if self.in_title:
            self.parts.append(data)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # Redirects could point at internal hosts, bypassing the public-host check.
    def redirect_request(self, *args, **kwargs):
        return None


def _is_public_host(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    return all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


def fetch_title(url):
    """Return the page title of `url`, or None if it can't be determined.

    Only public hosts are contacted (SSRF guard) and redirects are not followed.
    Note: DNS rebinding between check and fetch is not fully prevented.
    """
    try:
        host = urlparse(url).hostname
        if not host or not _is_public_host(host):
            return None
        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(url, headers={"User-Agent": "url-shortener/1.0"})
        with opener.open(req, timeout=FETCH_TIMEOUT) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            raw = resp.read(MAX_BODY_BYTES)
        parser = _TitleParser()
        parser.feed(raw.decode(charset, errors="replace"))
        title = " ".join("".join(parser.parts).split())
        return title or None
    except (urllib.error.URLError, OSError, ValueError, LookupError):
        return None


def _valid_url(url):
    if not isinstance(url, str) or len(url) > MAX_URL_LEN:
        return False
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc) and bool(p.hostname)


def create_app(db_path="shortener.db", title_fetcher=fetch_title):
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DB_PATH"])
            g.db.row_factory = sqlite3.Row
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    with sqlite3.connect(db_path) as conn:
        conn.execute(SCHEMA)

    def error(message, status):
        return jsonify(error=message), status

    @app.post("/api/links")
    def create_link():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return error("Body must be a JSON object", 400)
        url = data.get("url")
        alias = data.get("alias")
        if not _valid_url(url):
            return error("'url' must be a valid http(s) URL", 400)
        if alias is not None:
            if not isinstance(alias, str) or not ALIAS_RE.match(alias):
                return error("'alias' must be 1-32 chars of A-Z, a-z, 0-9, '_' or '-'", 400)
            if alias.lower() in RESERVED:
                return error("Alias is reserved", 400)

        title = title_fetcher(url)
        created = datetime.now(timezone.utc).isoformat()
        sql = "INSERT INTO links (code, url, title, created_at) VALUES (?, ?, ?, ?)"

        if alias:
            code = alias
            try:
                with db() as conn:
                    conn.execute(sql, (code, url, title, created))
            except sqlite3.IntegrityError:
                return error("Alias already in use", 409)
        else:
            for _ in range(10):
                code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(7))
                try:
                    with db() as conn:
                        conn.execute(sql, (code, url, title, created))
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                return error("Could not generate a unique code", 500)

        return jsonify(
            code=code,
            short_url=request.host_url + code,
            url=url,
            title=title,
        ), 201

    @app.get("/api/links/<code>/stats")
    def stats(code):
        row = db().execute("SELECT * FROM links WHERE code = ?", (code,)).fetchone()
        if row is None:
            return error("Not found", 404)
        return jsonify(
            code=row["code"],
            url=row["url"],
            title=row["title"],
            clicks=row["clicks"],
            created_at=row["created_at"],
        )

    @app.get("/<code>")
    def follow(code):
        with db() as conn:
            row = conn.execute(
                "UPDATE links SET clicks = clicks + 1 WHERE code = ? RETURNING url",
                (code,),
            ).fetchone()
        if row is None:
            return error("Not found", 404)
        return redirect(row["url"], code=302)

    return app


if __name__ == "__main__":
    create_app().run()
