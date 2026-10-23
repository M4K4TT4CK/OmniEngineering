import os

os.environ["NOTES_AUTOCREATE"] = "0"  # don't create notes.db on import

import pytest
from fastapi.testclient import TestClient

import app as app_module


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def client(tmp_path, clock):
    return TestClient(app_module.create_app(str(tmp_path / "test.db"), clock=clock))


def signup(client, name="alice", password="password123"):
    r = client.post("/signup", json={"username": name, "password": password})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_signup_login(client):
    signup(client)
    r = client.post("/login", json={"username": "alice", "password": "password123"})
    assert r.status_code == 200 and r.json()["token"]


def test_signup_duplicate_and_validation(client):
    signup(client)
    assert client.post("/signup", json={"username": "ALICE", "password": "password123"}).status_code == 409
    assert client.post("/signup", json={"username": "bob", "password": "short"}).status_code == 422
    assert client.post("/signup", json={"username": "b o b", "password": "password123"}).status_code == 422


def test_login_failures(client):
    signup(client)
    assert client.post("/login", json={"username": "alice", "password": "wrongpass1"}).status_code == 401
    assert client.post("/login", json={"username": "nobody", "password": "password123"}).status_code == 401


def test_notes_require_auth(client):
    assert client.get("/notes").status_code == 401
    assert client.get("/notes", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_note_crud(client):
    h = signup(client)
    r = client.post("/notes", json={"title": "Groceries", "body": "milk"}, headers=h)
    assert r.status_code == 201
    note = r.json()
    nid = note["id"]

    assert client.get(f"/notes/{nid}", headers=h).json()["title"] == "Groceries"
    assert [n["id"] for n in client.get("/notes", headers=h).json()] == [nid]

    r = client.patch(f"/notes/{nid}", json={"body": "milk, eggs"}, headers=h)
    assert r.status_code == 200
    assert r.json()["body"] == "milk, eggs" and r.json()["title"] == "Groceries"

    assert client.delete(f"/notes/{nid}", headers=h).status_code == 204
    assert client.get(f"/notes/{nid}", headers=h).status_code == 404
    assert client.get("/notes", headers=h).json() == []


def test_note_validation(client):
    h = signup(client)
    assert client.post("/notes", json={"title": ""}, headers=h).status_code == 422
    assert client.post("/notes", json={"body": "x"}, headers=h).status_code == 422


def test_search(client):
    h = signup(client)
    client.post("/notes", json={"title": "Recipe", "body": "pasta with tomato"}, headers=h)
    client.post("/notes", json={"title": "Tomato plants", "body": "water daily"}, headers=h)
    client.post("/notes", json={"title": "Discount", "body": "50% off"}, headers=h)
    client.post("/notes", json={"title": "Other", "body": "nothing"}, headers=h)

    titles = {n["title"] for n in client.get("/notes", params={"q": "TOMATO"}, headers=h).json()}
    assert titles == {"Recipe", "Tomato plants"}
    # LIKE wildcards are treated literally
    assert [n["title"] for n in client.get("/notes", params={"q": "%"}, headers=h).json()] == ["Discount"]
    assert client.get("/notes", params={"q": "_"}, headers=h).json() == []


def test_pagination(client):
    h = signup(client)
    for i in range(5):
        client.post("/notes", json={"title": f"n{i}"}, headers=h)
    assert len(client.get("/notes", params={"limit": 2}, headers=h).json()) == 2
    assert len(client.get("/notes", params={"limit": 10, "offset": 3}, headers=h).json()) == 2


def test_users_cannot_access_each_others_notes(client):
    a = signup(client, "alice")
    b = signup(client, "bobby")
    nid = client.post("/notes", json={"title": "secret", "body": "s"}, headers=a).json()["id"]

    assert client.get(f"/notes/{nid}", headers=b).status_code == 404
    assert client.patch(f"/notes/{nid}", json={"title": "x"}, headers=b).status_code == 404
    assert client.delete(f"/notes/{nid}", headers=b).status_code == 404
    assert client.get("/notes", headers=b).json() == []
    assert client.get("/notes", params={"q": "secret"}, headers=b).json() == []
    assert client.get(f"/notes/{nid}", headers=a).json()["title"] == "secret"


def test_rate_limit_per_user(client, clock):
    a = signup(client, "alice")
    b = signup(client, "bobby")
    ok = [client.get("/notes", headers=a).status_code for _ in range(60)]
    assert set(ok) == {200}

    r = client.get("/notes", headers=a)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1

    # another user is unaffected
    assert client.get("/notes", headers=b).status_code == 200

    # window slides forward
    clock.t += 61
    assert client.get("/notes", headers=a).status_code == 200


def test_rate_limit_on_unauthenticated_requests(client):
    codes = [client.post("/login", json={"username": "x", "password": "y"}).status_code
             for _ in range(61)]
    assert codes[:60] == [401] * 60
    assert codes[60] == 429
