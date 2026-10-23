"""Personal notes REST API: FastAPI + SQLite (stdlib sqlite3)."""
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from collections import deque
from typing import Callable, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

TOKEN_TTL_SECONDS = 24 * 3600
RATE_LIMIT = 60
RATE_WINDOW = 60.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id, updated_at);
"""


# ---------- password / token helpers ----------

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$")
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest.hex(), digest_hex)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# Used to spend equal time on unknown usernames, so login timing doesn't reveal them.
_DUMMY_HASH = hash_password("dummy-password")


class RateLimiter:
    """In-memory sliding-window limiter, keyed by an arbitrary string."""

    def __init__(self, limit: int, window: float, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = self.clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                retry = max(1, int(hits[0] + self.window - now) + 1)
                raise HTTPException(429, "Rate limit exceeded",
                                    headers={"Retry-After": str(retry)})
            hits.append(now)
            if len(self._hits) > 10000:  # drop idle keys
                for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
                    del self._hits[k]


# ---------- schemas ----------

class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=128)


class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=10000)


class NoteUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    body: Optional[str] = Field(default=None, max_length=10000)


def note_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in ("id", "title", "body", "created_at", "updated_at")}


# ---------- app factory ----------

def create_app(db_path: Optional[str] = None, rate_limit: int = RATE_LIMIT,
               clock: Callable[[], float] = time.monotonic) -> FastAPI:
    db_path = db_path or os.environ.get("NOTES_DB", "notes.db")
    limiter = RateLimiter(rate_limit, RATE_WINDOW, clock)

    init = sqlite3.connect(db_path)
    init.execute("PRAGMA journal_mode=WAL")
    init.executescript(SCHEMA)
    init.close()

    app = FastAPI(title="Notes API")

    def get_db():
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def ip_limit(request: Request) -> None:
        limiter.check("ip:" + (request.client.host if request.client else "unknown"))

    def current_user(authorization: Optional[str] = Header(default=None),
                     db: sqlite3.Connection = Depends(get_db),
                     request: Request = None) -> int:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            ip_limit(request)
            raise HTTPException(401, "Missing or malformed bearer token",
                                headers={"WWW-Authenticate": "Bearer"})
        row = db.execute("SELECT user_id, expires_at FROM tokens WHERE token_hash = ?",
                         (hash_token(token.strip()),)).fetchone()
        if row is None or row["expires_at"] < time.time():
            ip_limit(request)
            raise HTTPException(401, "Invalid or expired token",
                                headers={"WWW-Authenticate": "Bearer"})
        limiter.check(f"user:{row['user_id']}")
        return row["user_id"]

    def issue_token(db: sqlite3.Connection, user_id: int) -> dict:
        token = secrets.token_urlsafe(32)
        db.execute("DELETE FROM tokens WHERE expires_at < ?", (time.time(),))
        db.execute("INSERT INTO tokens (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                   (hash_token(token), user_id, time.time() + TOKEN_TTL_SECONDS))
        db.commit()
        return {"token": token, "token_type": "bearer", "expires_in": TOKEN_TTL_SECONDS}

    # ----- auth -----

    @app.post("/signup", status_code=201, dependencies=[Depends(ip_limit)])
    def signup(creds: Credentials, db: sqlite3.Connection = Depends(get_db)):
        try:
            cur = db.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (creds.username, hash_password(creds.password), time.time()))
            db.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Username already taken")
        return issue_token(db, cur.lastrowid)

    @app.post("/login", dependencies=[Depends(ip_limit)])
    def login(creds: LoginIn, db: sqlite3.Connection = Depends(get_db)):
        row = db.execute("SELECT id, password_hash FROM users WHERE username = ?",
                         (creds.username,)).fetchone()
        ok = verify_password(creds.password, row["password_hash"] if row else _DUMMY_HASH)
        if not row or not ok:
            raise HTTPException(401, "Invalid username or password")
        return issue_token(db, row["id"])

    # ----- notes -----

    @app.post("/notes", status_code=201)
    def create_note(note: NoteIn, user_id: int = Depends(current_user),
                    db: sqlite3.Connection = Depends(get_db)):
        now = time.time()
        cur = db.execute(
            "INSERT INTO notes (user_id, title, body, created_at, updated_at) VALUES (?,?,?,?,?)",
            (user_id, note.title, note.body, now, now))
        db.commit()
        row = db.execute("SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)).fetchone()
        return note_dict(row)

    @app.get("/notes")
    def list_notes(q: Optional[str] = Query(default=None, max_length=200),
                   limit: int = Query(default=50, ge=1, le=200),
                   offset: int = Query(default=0, ge=0),
                   user_id: int = Depends(current_user),
                   db: sqlite3.Connection = Depends(get_db)):
        sql, params = "SELECT * FROM notes WHERE user_id = ?", [user_id]
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            sql += " AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')"
            params += [f"%{escaped}%"] * 2
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        return [note_dict(r) for r in db.execute(sql, params)]

    def get_owned(db, note_id: int, user_id: int) -> sqlite3.Row:
        row = db.execute("SELECT * FROM notes WHERE id = ? AND user_id = ?",
                         (note_id, user_id)).fetchone()
        if row is None:  # 404 for other users' notes too, so ids don't leak
            raise HTTPException(404, "Note not found")
        return row

    @app.get("/notes/{note_id}")
    def read_note(note_id: int, user_id: int = Depends(current_user),
                  db: sqlite3.Connection = Depends(get_db)):
        return note_dict(get_owned(db, note_id, user_id))

    @app.patch("/notes/{note_id}")
    def update_note(note_id: int, patch: NoteUpdate, user_id: int = Depends(current_user),
                    db: sqlite3.Connection = Depends(get_db)):
        row = get_owned(db, note_id, user_id)
        title = patch.title if patch.title is not None else row["title"]
        body = patch.body if patch.body is not None else row["body"]
        db.execute("UPDATE notes SET title = ?, body = ?, updated_at = ? WHERE id = ?",
                   (title, body, time.time(), note_id))
        db.commit()
        return note_dict(get_owned(db, note_id, user_id))

    @app.delete("/notes/{note_id}", status_code=204)
    def delete_note(note_id: int, user_id: int = Depends(current_user),
                    db: sqlite3.Connection = Depends(get_db)):
        get_owned(db, note_id, user_id)
        db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        db.commit()
        return Response(status_code=204)

    return app


app = create_app() if os.environ.get("NOTES_AUTOCREATE", "1") == "1" else None
