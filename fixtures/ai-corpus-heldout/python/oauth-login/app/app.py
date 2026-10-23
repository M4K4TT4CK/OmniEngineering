"""Minimal Flask app implementing "Log in with GitHub" via OAuth 2.0.

Routes:
    /           home page
    /login      redirect to GitHub's authorize endpoint
    /callback   exchange code for token, upsert user, start session
    /profile    show the logged-in user's details
    /logout     (POST) end the session
"""

import base64
import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlencode, urlsplit

import requests
from flask import (
    Flask,
    abort,
    current_app,
    g,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)
from markupsafe import Markup

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
HTTP_TIMEOUT = 10  # seconds

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    github_id   INTEGER NOT NULL UNIQUE,
    login       TEXT NOT NULL,
    name        TEXT,
    email       TEXT,
    avatar_url  TEXT,
    html_url    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    with sqlite3.connect(app.config["DATABASE"]) as conn:
        conn.executescript(SCHEMA)


def upsert_user(profile):
    """Create or update the local user for a GitHub profile; return its id."""
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        """
        INSERT INTO users (github_id, login, name, email, avatar_url, html_url,
                           created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(github_id) DO UPDATE SET
            login = excluded.login,
            name = excluded.name,
            email = excluded.email,
            avatar_url = excluded.avatar_url,
            html_url = excluded.html_url,
            updated_at = excluded.updated_at
        """,
        (
            profile["id"],
            profile["login"],
            profile.get("name"),
            profile.get("email"),
            profile.get("avatar_url"),
            profile.get("html_url"),
            now,
            now,
        ),
    )
    db.commit()
    row = db.execute(
        "SELECT id FROM users WHERE github_id = ?", (profile["id"],)
    ).fetchone()
    return row["id"]


def get_user(user_id):
    return get_db().execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()


# --------------------------------------------------------------------------
# GitHub API
# --------------------------------------------------------------------------

class OAuthError(Exception):
    pass


def exchange_code_for_token(code, code_verifier):
    resp = requests.post(
        GITHUB_TOKEN_URL,
        data={
            "client_id": current_app.config["GITHUB_CLIENT_ID"],
            "client_secret": current_app.config["GITHUB_CLIENT_SECRET"],
            "code": code,
            "redirect_uri": url_for("callback", _external=True),
            "code_verifier": code_verifier,
        },
        headers={"Accept": "application/json"},
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        raise OAuthError("token endpoint returned %s" % resp.status_code)
    data = resp.json()
    token = data.get("access_token")
    if not token:
        # GitHub reports errors with a 200 status and an "error" field.
        raise OAuthError(data.get("error", "no access token in response"))
    return token


def fetch_github_user(token):
    resp = requests.get(
        GITHUB_USER_URL,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
        },
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        raise OAuthError("user endpoint returned %s" % resp.status_code)
    profile = resp.json()
    if not isinstance(profile.get("id"), int) or not profile.get("login"):
        raise OAuthError("unexpected user payload")
    return profile


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def safe_next_url(target):
    """Return target only if it is a local path; otherwise None.

    Prevents open redirects such as //evil.com or https://evil.com.
    """
    if not target or not isinstance(target, str):
        return None
    if "\\" in target or any(ord(c) < 32 for c in target):
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    return target


def pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


# --------------------------------------------------------------------------
# Templates (Jinja autoescaping is on for render_template_string)
# --------------------------------------------------------------------------

BASE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>GitHub Login Demo</title></head>
<body>
{% if user %}
  <p>Signed in as <strong>{{ user['login'] }}</strong> |
     <a href="{{ url_for('profile') }}">Profile</a></p>
  <form method="post" action="{{ url_for('logout') }}">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button type="submit">Log out</button>
  </form>
{% else %}
  <p><a href="{{ url_for('login', next=request.path) }}">Log in with GitHub</a></p>
{% endif %}
<hr>
{{ body }}
</body></html>
"""

HOME_BODY = "<h1>Home</h1><p>Welcome to the GitHub OAuth demo.</p>"

PROFILE_HTML = """<h1>Profile</h1>
{% if avatar_url %}<img src="{{ avatar_url }}" alt="avatar" width="80">{% endif %}
<dl>
  <dt>Username</dt><dd>{{ user['login'] }}</dd>
  <dt>Name</dt><dd>{{ user['name'] or '' }}</dd>
  <dt>Email</dt><dd>{{ user['email'] or '' }}</dd>
  <dt>GitHub ID</dt><dd>{{ user['github_id'] }}</dd>
  <dt>GitHub page</dt><dd>{% if html_url %}<a href="{{ html_url }}">{{ html_url }}</a>{% endif %}</dd>
  <dt>First login</dt><dd>{{ user['created_at'] }}</dd>
  <dt>Last updated</dt><dd>{{ user['updated_at'] }}</dd>
</dl>
"""


def render_page(body_template, **ctx):
    user = get_user(session["user_id"]) if "user_id" in session else None
    # The body is already auto-escaped, so mark it safe for the outer template.
    body = Markup(render_template_string(body_template, user=user, **ctx))
    return render_template_string(
        BASE_HTML,
        user=user,
        body=body,
        csrf_token=session.get("csrf_token", ""),
    )


def https_only(url):
    return url if isinstance(url, str) and url.startswith("https://") else None


# --------------------------------------------------------------------------
# App factory
# --------------------------------------------------------------------------

def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY"),
        GITHUB_CLIENT_ID=os.environ.get("GITHUB_CLIENT_ID"),
        GITHUB_CLIENT_SECRET=os.environ.get("GITHUB_CLIENT_SECRET"),
        GITHUB_SCOPE="read:user user:email",
        DATABASE=os.environ.get("DATABASE", "users.db"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "1") == "1",
    )
    if config:
        app.config.update(config)

    for key in ("SECRET_KEY", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"):
        if not app.config.get(key):
            raise RuntimeError("%s must be set" % key)

    init_db(app)
    app.teardown_appcontext(close_db)

    @app.route("/")
    def index():
        return render_page(HOME_BODY)

    @app.route("/login")
    def login():
        next_url = safe_next_url(request.args.get("next")) or url_for("index")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        session["oauth_state"] = state
        session["oauth_verifier"] = verifier
        session["oauth_next"] = next_url
        params = {
            "client_id": app.config["GITHUB_CLIENT_ID"],
            "redirect_uri": url_for("callback", _external=True),
            "scope": app.config["GITHUB_SCOPE"],
            "state": state,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "allow_signup": "true",
        }
        return redirect(GITHUB_AUTHORIZE_URL + "?" + urlencode(params))

    @app.route("/callback")
    def callback():
        expected_state = session.pop("oauth_state", None)
        verifier = session.pop("oauth_verifier", None)
        next_url = safe_next_url(session.pop("oauth_next", None)) or url_for("index")

        if request.args.get("error"):
            return "GitHub authorization was denied or failed.", 400

        state = request.args.get("state", "")
        code = request.args.get("code")
        if (
            not expected_state
            or not verifier
            or not secrets.compare_digest(state, expected_state)
        ):
            return "Invalid OAuth state.", 400
        if not code:
            return "Missing authorization code.", 400

        try:
            token = exchange_code_for_token(code, verifier)
            profile = fetch_github_user(token)
        except (OAuthError, requests.RequestException, ValueError):
            app.logger.exception("GitHub OAuth failed")
            return "Could not complete GitHub login.", 502

        user_id = upsert_user(profile)

        # Start a fresh session to avoid session fixation.
        session.clear()
        session["user_id"] = user_id
        session["csrf_token"] = secrets.token_urlsafe(32)
        return redirect(next_url)

    @app.route("/profile")
    @login_required
    def profile():
        user = get_user(session["user_id"])
        if user is None:
            session.clear()
            return redirect(url_for("login", next=url_for("profile")))
        return render_page(
            PROFILE_HTML,
            avatar_url=https_only(user["avatar_url"]),
            html_url=https_only(user["html_url"]),
        )

    @app.route("/logout", methods=["POST"])
    def logout():
        expected = session.get("csrf_token")
        submitted = request.form.get("csrf_token", "")
        if expected and not secrets.compare_digest(submitted, expected):
            abort(400)
        session.clear()
        return redirect(url_for("index"))

    return app


if __name__ == "__main__":
    create_app().run(debug=False)
