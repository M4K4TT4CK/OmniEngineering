"""Multi-tenant notes service (FastAPI + SQLite).

Run with:  uvicorn app:create_app --factory
"""

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from typing import Annotated, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AfterValidator, BaseModel, Field

Role = Literal["admin", "editor", "viewer"]
WRITE_ROLES = {"admin", "editor"}

TOKEN_TTL_SECONDS = 24 * 3600
INVITE_TTL_SECONDS = 7 * 24 * 3600
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS orgs (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    org_id        INTEGER NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'editor', 'viewer')),
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS users_org ON users(org_id);
CREATE TABLE IF NOT EXISTS tokens (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS invitations (
    token_hash  TEXT PRIMARY KEY,
    org_id      INTEGER NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('admin', 'editor', 'viewer')),
    invited_by  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY,
    org_id      INTEGER NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    author_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_org ON notes(org_id, id);
"""


# --- persistence -----------------------------------------------------------

def connect(db_path: str) -> sqlite3.Connection:
    # Autocommit mode; multi-statement writes use transaction() explicitly.
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(db_path: str) -> None:
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# --- crypto helpers --------------------------------------------------------

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


# Used so that logins for unknown emails take as long as for known ones.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def hash_token(token: str) -> str:
    # Tokens are high-entropy random values, so a plain SHA-256 is sufficient.
    return hashlib.sha256(token.encode()).hexdigest()


def now() -> int:
    return int(time.time())


# --- request / response models ---------------------------------------------

def _normalise_email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not EMAIL_RE.match(value):
        raise ValueError("invalid email address")
    return value


Email = Annotated[str, AfterValidator(_normalise_email)]


class Credentials(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=1024)


class SignupIn(BaseModel):
    org_name: str = Field(min_length=1, max_length=200)
    email: Email
    password: str = Field(min_length=8, max_length=1024)


class InviteIn(BaseModel):
    email: Email
    role: Role


class AcceptInviteIn(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=1024)


class RoleIn(BaseModel):
    role: Role


class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(default="", max_length=100_000)


class NotePatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    body: Optional[str] = Field(default=None, max_length=100_000)


def user_out(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "org_id": row["org_id"], "email": row["email"], "role": row["role"]}


def note_out(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in ("id", "org_id", "author_id", "title", "body", "created_at", "updated_at")}


def like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# --- dependencies ----------------------------------------------------------

bearer = HTTPBearer(auto_error=False)


def get_db(request: Request):
    conn = connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: sqlite3.Connection = Depends(get_db),
) -> sqlite3.Row:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "invalid or missing token", headers={"WWW-Authenticate": "Bearer"}
    )
    if creds is None:
        raise unauthorized
    # Role and org are read fresh on every request, so role changes apply immediately.
    row = db.execute(
        """SELECT u.* FROM tokens t JOIN users u ON u.id = t.user_id
           WHERE t.token_hash = ? AND t.expires_at > ?""",
        (hash_token(creds.credentials), now()),
    ).fetchone()
    if row is None:
        raise unauthorized
    return row


def require_writer(user: sqlite3.Row = Depends(current_user)) -> sqlite3.Row:
    if user["role"] not in WRITE_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "editor or admin role required")
    return user


def require_admin(user: sqlite3.Row = Depends(current_user)) -> sqlite3.Row:
    if user["role"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return user


def issue_token(db: sqlite3.Connection, user_id: int) -> dict:
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO tokens (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (hash_token(token), user_id, now() + TOKEN_TTL_SECONDS),
    )
    return {"token": token, "token_type": "bearer", "expires_in": TOKEN_TTL_SECONDS}


def get_note_in_org(db: sqlite3.Connection, note_id: int, org_id: int) -> sqlite3.Row:
    row = db.execute("SELECT * FROM notes WHERE id = ? AND org_id = ?", (note_id, org_id)).fetchone()
    if row is None:
        # Same response for "missing" and "belongs to another org" to avoid leaking ids.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "note not found")
    return row


# --- application -----------------------------------------------------------

def create_app(db_path: Optional[str] = None) -> FastAPI:
    app = FastAPI(title="Tenant Notes")
    app.state.db_path = db_path or os.environ.get("NOTES_DB", "notes.db")
    init_db(app.state.db_path)

    # Auth & accounts

    @app.post("/signup", status_code=201)
    def signup(body: SignupIn, db: sqlite3.Connection = Depends(get_db)):
        """Create a new organisation with the caller as its first admin."""
        pw_hash = hash_password(body.password)
        try:
            with transaction(db):
                org_id = db.execute(
                    "INSERT INTO orgs (name, created_at) VALUES (?, ?)", (body.org_name, now())
                ).lastrowid
                user_id = db.execute(
                    "INSERT INTO users (org_id, email, password_hash, role, created_at) VALUES (?, ?, ?, 'admin', ?)",
                    (org_id, body.email, pw_hash, now()),
                ).lastrowid
                token = issue_token(db, user_id)
        except sqlite3.IntegrityError:
            raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
        return {"org_id": org_id, "user_id": user_id, **token}

    @app.post("/login")
    def login(body: Credentials, db: sqlite3.Connection = Depends(get_db)):
        row = db.execute("SELECT * FROM users WHERE email = ?", (body.email,)).fetchone()
        ok = verify_password(body.password, row["password_hash"] if row else _DUMMY_HASH)
        if row is None or not ok:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
        db.execute("DELETE FROM tokens WHERE user_id = ? AND expires_at <= ?", (row["id"], now()))
        return issue_token(db, row["id"])

    @app.post("/logout", status_code=204)
    def logout(
        creds: HTTPAuthorizationCredentials = Depends(bearer),
        user: sqlite3.Row = Depends(current_user),
        db: sqlite3.Connection = Depends(get_db),
    ):
        db.execute("DELETE FROM tokens WHERE token_hash = ?", (hash_token(creds.credentials),))

    @app.get("/me")
    def me(user: sqlite3.Row = Depends(current_user)):
        return user_out(user)

    # User administration (admin only, scoped to the admin's org)

    @app.get("/users")
    def list_users(admin: sqlite3.Row = Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
        rows = db.execute("SELECT * FROM users WHERE org_id = ? ORDER BY id", (admin["org_id"],)).fetchall()
        return [user_out(r) for r in rows]

    @app.post("/invitations", status_code=201)
    def invite(body: InviteIn, admin: sqlite3.Row = Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
        if db.execute("SELECT 1 FROM users WHERE email = ?", (body.email,)).fetchone():
            raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
        token = secrets.token_urlsafe(32)
        db.execute(
            "INSERT INTO invitations (token_hash, org_id, email, role, invited_by, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (hash_token(token), admin["org_id"], body.email, body.role, admin["id"], now() + INVITE_TTL_SECONDS),
        )
        # In production this token would be emailed to the invitee rather than returned.
        return {"email": body.email, "role": body.role, "invite_token": token, "expires_in": INVITE_TTL_SECONDS}

    @app.post("/invitations/accept", status_code=201)
    def accept_invite(body: AcceptInviteIn, db: sqlite3.Connection = Depends(get_db)):
        pw_hash = hash_password(body.password)
        token_hash = hash_token(body.token)
        try:
            with transaction(db):
                inv = db.execute(
                    "SELECT * FROM invitations WHERE token_hash = ? AND expires_at > ?", (token_hash, now())
                ).fetchone()
                if inv is None:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid or expired invitation")
                # Single use: consume the invitation and any others for the same email.
                db.execute("DELETE FROM invitations WHERE email = ?", (inv["email"],))
                user_id = db.execute(
                    "INSERT INTO users (org_id, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
                    (inv["org_id"], inv["email"], pw_hash, inv["role"], now()),
                ).lastrowid
                token = issue_token(db, user_id)
        except sqlite3.IntegrityError:
            raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
        return {"user_id": user_id, "org_id": inv["org_id"], "role": inv["role"], **token}

    @app.patch("/users/{user_id}/role")
    def change_role(
        user_id: int,
        body: RoleIn,
        admin: sqlite3.Row = Depends(require_admin),
        db: sqlite3.Connection = Depends(get_db),
    ):
        with transaction(db):
            target = db.execute(
                "SELECT * FROM users WHERE id = ? AND org_id = ?", (user_id, admin["org_id"])
            ).fetchone()
            if target is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
            if target["role"] == "admin" and body.role != "admin":
                admins = db.execute(
                    "SELECT COUNT(*) FROM users WHERE org_id = ? AND role = 'admin'", (admin["org_id"],)
                ).fetchone()[0]
                if admins <= 1:
                    raise HTTPException(status.HTTP_409_CONFLICT, "organisation must keep at least one admin")
            db.execute("UPDATE users SET role = ? WHERE id = ?", (body.role, user_id))
            updated = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return user_out(updated)

    # Notes (always scoped to the caller's org)

    @app.get("/notes")
    def list_notes(
        q: Optional[str] = Query(default=None, max_length=200, description="case-insensitive text search"),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        user: sqlite3.Row = Depends(current_user),
        db: sqlite3.Connection = Depends(get_db),
    ):
        sql = "SELECT * FROM notes WHERE org_id = ?"
        params: list = [user["org_id"]]
        if q:
            pattern = like_pattern(q)
            sql += " AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')"
            params += [pattern, pattern]
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        return [note_out(r) for r in db.execute(sql, params).fetchall()]

    @app.get("/notes/{note_id}")
    def get_note(note_id: int, user: sqlite3.Row = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
        return note_out(get_note_in_org(db, note_id, user["org_id"]))

    @app.post("/notes", status_code=201)
    def create_note(body: NoteIn, user: sqlite3.Row = Depends(require_writer), db: sqlite3.Connection = Depends(get_db)):
        ts = now()
        note_id = db.execute(
            "INSERT INTO notes (org_id, author_id, title, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user["org_id"], user["id"], body.title, body.body, ts, ts),
        ).lastrowid
        return note_out(get_note_in_org(db, note_id, user["org_id"]))

    @app.patch("/notes/{note_id}")
    def update_note(
        note_id: int,
        body: NotePatch,
        user: sqlite3.Row = Depends(require_writer),
        db: sqlite3.Connection = Depends(get_db),
    ):
        changes = body.model_dump(exclude_none=True)
        if not changes:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "nothing to update")
        with transaction(db):
            note = get_note_in_org(db, note_id, user["org_id"])
            db.execute(
                "UPDATE notes SET title = ?, body = ?, updated_at = ? WHERE id = ? AND org_id = ?",
                (changes.get("title", note["title"]), changes.get("body", note["body"]), now(), note_id, user["org_id"]),
            )
        return note_out(get_note_in_org(db, note_id, user["org_id"]))

    @app.delete("/notes/{note_id}", status_code=204)
    def delete_note(note_id: int, user: sqlite3.Row = Depends(require_writer), db: sqlite3.Connection = Depends(get_db)):
        cur = db.execute("DELETE FROM notes WHERE id = ? AND org_id = ?", (note_id, user["org_id"]))
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "note not found")

    return app
