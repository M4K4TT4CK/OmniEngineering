import sqlite3
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

import app as app_module


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


PROFILE = {
    "id": 4242,
    "login": "octocat",
    "name": "The Octocat",
    "email": "octocat@example.com",
    "avatar_url": "https://avatars.githubusercontent.com/u/4242",
    "html_url": "https://github.com/octocat",
}


@pytest.fixture
def app(tmp_path):
    return app_module.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "GITHUB_CLIENT_ID": "cid",
            "GITHUB_CLIENT_SECRET": "csecret",
            "DATABASE": str(tmp_path / "test.db"),
            "SESSION_COOKIE_SECURE": False,
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def github(monkeypatch):
    """Stub GitHub's token and user endpoints; records calls made."""
    state = {"profile": dict(PROFILE), "token_payload": {"access_token": "gho_abc"},
             "calls": []}

    def fake_post(url, data=None, headers=None, timeout=None):
        state["calls"].append(("POST", url, data))
        assert url == app_module.GITHUB_TOKEN_URL
        return FakeResponse(200, state["token_payload"])

    def fake_get(url, headers=None, timeout=None):
        state["calls"].append(("GET", url, headers))
        assert url == app_module.GITHUB_USER_URL
        assert headers["Authorization"] == "Bearer gho_abc"
        return FakeResponse(200, state["profile"])

    monkeypatch.setattr(app_module.requests, "post", fake_post)
    monkeypatch.setattr(app_module.requests, "get", fake_get)
    return state


def start_login(client, next_url=None):
    url = "/login" if next_url is None else "/login?next=" + next_url
    resp = client.get(url)
    assert resp.status_code == 302
    query = parse_qs(urlsplit(resp.headers["Location"]).query)
    return resp, query["state"][0]


def complete_login(client, next_url=None):
    _, state = start_login(client, next_url)
    return client.get("/callback?code=thecode&state=" + state)


def db_rows(app):
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM users").fetchall()
    finally:
        conn.close()


# --- login --------------------------------------------------------------

def test_login_redirects_to_github_with_state_and_pkce(client):
    resp, state = start_login(client)
    location = urlsplit(resp.headers["Location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == \
        app_module.GITHUB_AUTHORIZE_URL
    query = parse_qs(location.query)
    assert query["client_id"] == ["cid"]
    assert query["redirect_uri"] == ["http://localhost/callback"]
    assert query["code_challenge_method"] == ["S256"]
    with client.session_transaction() as sess:
        assert sess["oauth_state"] == state
        assert query["code_challenge"][0] == app_module.pkce_challenge(
            sess["oauth_verifier"])


def test_missing_config_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError):
        app_module.create_app({"DATABASE": str(tmp_path / "x.db"),
                               "GITHUB_CLIENT_ID": "a",
                               "GITHUB_CLIENT_SECRET": "b"})


# --- callback -----------------------------------------------------------

def test_callback_creates_user_and_redirects_to_next(app, client, github):
    resp = complete_login(client, "/profile")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/profile"

    rows = db_rows(app)
    assert len(rows) == 1
    assert rows[0]["github_id"] == 4242
    assert rows[0]["login"] == "octocat"

    method, url, data = github["calls"][0]
    assert data["code"] == "thecode"
    assert data["client_secret"] == "csecret"
    assert data["code_verifier"]

    with client.session_transaction() as sess:
        assert sess["user_id"] == rows[0]["id"]
        assert "oauth_state" not in sess
        assert "oauth_verifier" not in sess


def test_callback_defaults_to_home(client, github):
    resp = complete_login(client)
    assert resp.headers["Location"] == "/"


def test_callback_updates_existing_user(app, client, github):
    complete_login(client)
    client.post("/logout", data={"csrf_token": csrf(client)})

    github["profile"] = dict(PROFILE, login="octocat2", name="Renamed")
    complete_login(client)

    rows = db_rows(app)
    assert len(rows) == 1
    assert rows[0]["login"] == "octocat2"
    assert rows[0]["name"] == "Renamed"


@pytest.mark.parametrize("next_url", [
    "https://evil.example",
    "//evil.example",
    "/\\evil.example",
    "javascript:alert(1)",
    "evil",
])
def test_open_redirect_is_blocked(client, github, next_url):
    resp = complete_login(client, next_url)
    assert resp.headers["Location"] == "/"


def test_callback_rejects_bad_state(client, github):
    start_login(client)
    resp = client.get("/callback?code=thecode&state=wrong")
    assert resp.status_code == 400
    assert github["calls"] == []


def test_callback_rejects_without_login_started(client, github):
    resp = client.get("/callback?code=thecode&state=anything")
    assert resp.status_code == 400


def test_state_cannot_be_replayed(client, github):
    _, state = start_login(client)
    client.get("/callback?code=thecode&state=" + state)
    client.post("/logout", data={"csrf_token": csrf(client)})
    resp = client.get("/callback?code=thecode&state=" + state)
    assert resp.status_code == 400


def test_callback_handles_github_denial(client, github):
    _, state = start_login(client)
    resp = client.get("/callback?error=access_denied&state=" + state)
    assert resp.status_code == 400


def test_callback_handles_token_error(app, client, github):
    github["token_payload"] = {"error": "bad_verification_code"}
    resp = complete_login(client)
    assert resp.status_code == 502
    assert db_rows(app) == []
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_callback_handles_network_error(client, monkeypatch):
    def boom(*a, **kw):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(app_module.requests, "post", boom)
    resp = complete_login(client)
    assert resp.status_code == 502


def test_session_is_reset_on_login(client, github):
    with client.session_transaction() as sess:
        sess["planted"] = "value"
    complete_login(client)
    with client.session_transaction() as sess:
        assert "planted" not in sess


# --- profile / logout ---------------------------------------------------

def csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token", "")


def test_profile_requires_login(client):
    resp = client.get("/profile")
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("/login?next=")


def test_profile_shows_user_details(client, github):
    complete_login(client)
    resp = client.get("/profile")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "octocat" in body
    assert "The Octocat" in body
    assert "octocat@example.com" in body


def test_profile_escapes_html(client, github):
    github["profile"] = dict(PROFILE, name="<script>alert(1)</script>",
                             html_url="javascript:alert(1)")
    complete_login(client)
    body = client.get("/profile").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body
    assert "javascript:" not in body


def test_logout_clears_session(client, github):
    complete_login(client)
    resp = client.post("/logout", data={"csrf_token": csrf(client)})
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert "user_id" not in sess
    assert client.get("/profile").status_code == 302


def test_logout_requires_csrf_token(client, github):
    complete_login(client)
    resp = client.post("/logout", data={"csrf_token": "wrong"})
    assert resp.status_code == 400
    with client.session_transaction() as sess:
        assert "user_id" in sess


def test_logout_rejects_get(client):
    assert client.get("/logout").status_code == 405
