import pytest
from fastapi.testclient import TestClient

import app as notes_app

PASSWORD = "correct horse battery"


@pytest.fixture
def client(tmp_path):
    return TestClient(notes_app.create_app(str(tmp_path / "test.db")))


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def signup(client, org="Acme", email="admin@acme.test"):
    r = client.post("/signup", json={"org_name": org, "email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    return r.json()


def add_member(client, admin_token, email, role):
    r = client.post("/invitations", json={"email": email, "role": role}, headers=auth(admin_token))
    assert r.status_code == 201, r.text
    r = client.post("/invitations/accept", json={"token": r.json()["invite_token"], "password": PASSWORD})
    assert r.status_code == 201, r.text
    return r.json()


def create_note(client, token, title="Hello", body="world"):
    return client.post("/notes", json={"title": title, "body": body}, headers=auth(token))


# --- auth ------------------------------------------------------------------

def test_signup_and_login(client):
    signup(client)
    r = client.post("/login", json={"email": "ADMIN@acme.test", "password": PASSWORD})
    assert r.status_code == 200
    me = client.get("/me", headers=auth(r.json()["token"])).json()
    assert me["email"] == "admin@acme.test" and me["role"] == "admin"


def test_login_rejects_bad_credentials(client):
    signup(client)
    assert client.post("/login", json={"email": "admin@acme.test", "password": "wrong"}).status_code == 401
    assert client.post("/login", json={"email": "nobody@acme.test", "password": PASSWORD}).status_code == 401


def test_duplicate_signup_email_conflicts(client):
    signup(client)
    r = client.post("/signup", json={"org_name": "Other", "email": "admin@acme.test", "password": PASSWORD})
    assert r.status_code == 409


def test_short_password_rejected(client):
    r = client.post("/signup", json={"org_name": "Acme", "email": "a@acme.test", "password": "short"})
    assert r.status_code == 422


def test_missing_or_invalid_token(client):
    assert client.get("/notes").status_code == 401
    assert client.get("/notes", headers=auth("bogus")).status_code == 401


def test_logout_revokes_token(client):
    token = signup(client)["token"]
    assert client.post("/logout", headers=auth(token)).status_code == 204
    assert client.get("/me", headers=auth(token)).status_code == 401


def test_expired_token_rejected(client, monkeypatch):
    token = signup(client)["token"]
    real_now = notes_app.now()
    monkeypatch.setattr(notes_app, "now", lambda: real_now + notes_app.TOKEN_TTL_SECONDS + 1)
    assert client.get("/me", headers=auth(token)).status_code == 401


# --- roles -----------------------------------------------------------------

def test_role_permissions_on_notes(client):
    admin = signup(client)["token"]
    editor = add_member(client, admin, "ed@acme.test", "editor")["token"]
    viewer = add_member(client, admin, "vi@acme.test", "viewer")["token"]

    for token in (admin, editor):
        assert create_note(client, token).status_code == 201

    assert create_note(client, viewer).status_code == 403
    note_id = create_note(client, editor).json()["id"]
    assert client.patch(f"/notes/{note_id}", json={"title": "x"}, headers=auth(viewer)).status_code == 403
    assert client.delete(f"/notes/{note_id}", headers=auth(viewer)).status_code == 403

    # Viewers can still read.
    assert client.get(f"/notes/{note_id}", headers=auth(viewer)).status_code == 200
    assert len(client.get("/notes", headers=auth(viewer)).json()) == 3


def test_only_admins_manage_users(client):
    admin = signup(client)["token"]
    editor = add_member(client, admin, "ed@acme.test", "editor")
    viewer = add_member(client, admin, "vi@acme.test", "viewer")

    for token in (editor["token"], viewer["token"]):
        assert client.post("/invitations", json={"email": "x@acme.test", "role": "admin"}, headers=auth(token)).status_code == 403
        assert client.patch(f"/users/{viewer['user_id']}/role", json={"role": "admin"}, headers=auth(token)).status_code == 403
        assert client.get("/users", headers=auth(token)).status_code == 403


def test_role_change_takes_effect_immediately(client):
    admin = signup(client)["token"]
    viewer = add_member(client, admin, "vi@acme.test", "viewer")
    assert create_note(client, viewer["token"]).status_code == 403

    r = client.patch(f"/users/{viewer['user_id']}/role", json={"role": "editor"}, headers=auth(admin))
    assert r.status_code == 200 and r.json()["role"] == "editor"
    assert create_note(client, viewer["token"]).status_code == 201

    client.patch(f"/users/{viewer['user_id']}/role", json={"role": "viewer"}, headers=auth(admin))
    assert create_note(client, viewer["token"]).status_code == 403


def test_invalid_role_rejected(client):
    admin = signup(client)["token"]
    r = client.post("/invitations", json={"email": "x@acme.test", "role": "owner"}, headers=auth(admin))
    assert r.status_code == 422


def test_cannot_remove_last_admin(client):
    admin = signup(client)
    r = client.patch(f"/users/{admin['user_id']}/role", json={"role": "viewer"}, headers=auth(admin["token"]))
    assert r.status_code == 409

    second = add_member(client, admin["token"], "admin2@acme.test", "admin")
    r = client.patch(f"/users/{admin['user_id']}/role", json={"role": "viewer"}, headers=auth(second["token"]))
    assert r.status_code == 200


# --- invitations -----------------------------------------------------------

def test_invitation_is_single_use(client):
    admin = signup(client)["token"]
    invite = client.post("/invitations", json={"email": "new@acme.test", "role": "editor"}, headers=auth(admin)).json()
    payload = {"token": invite["invite_token"], "password": PASSWORD}
    assert client.post("/invitations/accept", json=payload).status_code == 201
    assert client.post("/invitations/accept", json=payload).status_code == 400


def test_invalid_invitation_token(client):
    r = client.post("/invitations/accept", json={"token": "nope", "password": PASSWORD})
    assert r.status_code == 400


def test_cannot_invite_existing_user(client):
    admin = signup(client)["token"]
    signup(client, org="Other", email="taken@other.test")
    r = client.post("/invitations", json={"email": "taken@other.test", "role": "viewer"}, headers=auth(admin))
    assert r.status_code == 409


def test_invited_user_joins_inviting_org(client):
    admin = signup(client)
    member = add_member(client, admin["token"], "m@acme.test", "viewer")
    assert member["org_id"] == admin["org_id"]
    emails = [u["email"] for u in client.get("/users", headers=auth(admin["token"])).json()]
    assert emails == ["admin@acme.test", "m@acme.test"]


# --- tenant isolation ------------------------------------------------------

def test_notes_isolated_between_orgs(client):
    acme = signup(client)["token"]
    globex = signup(client, org="Globex", email="admin@globex.test")["token"]
    note_id = create_note(client, acme, "secret plan", "acme only").json()["id"]

    assert client.get("/notes", headers=auth(globex)).json() == []
    assert client.get("/notes?q=secret", headers=auth(globex)).json() == []
    assert client.get(f"/notes/{note_id}", headers=auth(globex)).status_code == 404
    assert client.patch(f"/notes/{note_id}", json={"title": "pwned"}, headers=auth(globex)).status_code == 404
    assert client.delete(f"/notes/{note_id}", headers=auth(globex)).status_code == 404

    note = client.get(f"/notes/{note_id}", headers=auth(acme)).json()
    assert note["title"] == "secret plan"


def test_admin_cannot_manage_users_of_other_org(client):
    acme = signup(client)
    globex = signup(client, org="Globex", email="admin@globex.test")
    r = client.patch(f"/users/{acme['user_id']}/role", json={"role": "viewer"}, headers=auth(globex["token"]))
    assert r.status_code == 404
    users = client.get("/users", headers=auth(globex["token"])).json()
    assert [u["email"] for u in users] == ["admin@globex.test"]


# --- notes CRUD & search ---------------------------------------------------

def test_note_crud(client):
    token = signup(client)["token"]
    note = create_note(client, token, "Groceries", "milk").json()

    r = client.patch(f"/notes/{note['id']}", json={"body": "milk, eggs"}, headers=auth(token))
    assert r.status_code == 200
    assert r.json()["title"] == "Groceries" and r.json()["body"] == "milk, eggs"

    assert client.patch(f"/notes/{note['id']}", json={}, headers=auth(token)).status_code == 422
    assert client.delete(f"/notes/{note['id']}", headers=auth(token)).status_code == 204
    assert client.get(f"/notes/{note['id']}", headers=auth(token)).status_code == 404
    assert client.delete(f"/notes/{note['id']}", headers=auth(token)).status_code == 404


def test_search_matches_title_and_body_case_insensitively(client):
    token = signup(client)["token"]
    create_note(client, token, "Meeting notes", "discuss budget")
    create_note(client, token, "Shopping", "Buy BUDGET planner")
    create_note(client, token, "Holiday", "beach")

    titles = {n["title"] for n in client.get("/notes", params={"q": "budget"}, headers=auth(token)).json()}
    assert titles == {"Meeting notes", "Shopping"}


def test_search_treats_wildcards_literally(client):
    token = signup(client)["token"]
    create_note(client, token, "Discount", "50% off")
    create_note(client, token, "Plain", "nothing here")

    assert [n["title"] for n in client.get("/notes", params={"q": "%"}, headers=auth(token)).json()] == ["Discount"]
    assert client.get("/notes", params={"q": "_"}, headers=auth(token)).json() == []


def test_list_pagination(client):
    token = signup(client)["token"]
    for i in range(5):
        create_note(client, token, f"n{i}")
    page = client.get("/notes", params={"limit": 2, "offset": 1}, headers=auth(token)).json()
    assert [n["title"] for n in page] == ["n3", "n2"]
