import pytest

from app import create_app, fetch_title


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "test.db"), title_fetcher=lambda url: "Example Title")
    return app.test_client()


def make(client, **body):
    return client.post("/api/links", json=body)


def test_create_random_code(client):
    r = make(client, url="https://example.com/page")
    assert r.status_code == 201
    body = r.get_json()
    assert len(body["code"]) == 7
    assert body["short_url"].endswith("/" + body["code"])
    assert body["title"] == "Example Title"


def test_create_custom_alias(client):
    r = make(client, url="https://example.com", alias="my-link")
    assert r.status_code == 201
    assert r.get_json()["code"] == "my-link"


def test_duplicate_alias_conflicts(client):
    make(client, url="https://example.com", alias="dup")
    r = make(client, url="https://other.com", alias="dup")
    assert r.status_code == 409


@pytest.mark.parametrize("alias", ["bad alias", "a/b", "", "x" * 33, "api", "API", 5])
def test_invalid_alias(client, alias):
    assert make(client, url="https://example.com", alias=alias).status_code == 400


@pytest.mark.parametrize("url", [None, "", "not a url", "ftp://example.com", "javascript:alert(1)", "http://", 5])
def test_invalid_url(client, url):
    assert make(client, url=url).status_code == 400


def test_non_object_body(client):
    assert client.post("/api/links", json=[1]).status_code == 400
    assert client.post("/api/links", data="nope").status_code == 400


def test_redirect_and_click_count(client):
    code = make(client, url="https://example.com/x?y=1").get_json()["code"]
    for _ in range(3):
        r = client.get("/" + code)
        assert r.status_code == 302
        assert r.headers["Location"] == "https://example.com/x?y=1"
    assert client.get(f"/api/links/{code}/stats").get_json()["clicks"] == 3


def test_stats(client):
    code = make(client, url="https://example.com").get_json()["code"]
    s = client.get(f"/api/links/{code}/stats").get_json()
    assert s["url"] == "https://example.com"
    assert s["title"] == "Example Title"
    assert s["clicks"] == 0
    assert s["created_at"]


def test_title_fetch_failure_still_creates(tmp_path):
    app = create_app(str(tmp_path / "t.db"), title_fetcher=lambda url: None)
    c = app.test_client()
    code = make(c, url="https://example.com").get_json()["code"]
    assert c.get(f"/api/links/{code}/stats").get_json()["title"] is None


def test_unknown_code(client):
    assert client.get("/nope").status_code == 404
    assert client.get("/api/links/nope/stats").status_code == 404


def test_persistence(tmp_path):
    path = str(tmp_path / "p.db")
    c1 = create_app(path, title_fetcher=lambda u: "T").test_client()
    code = make(c1, url="https://example.com").get_json()["code"]
    c2 = create_app(path, title_fetcher=lambda u: "T").test_client()
    assert c2.get("/" + code).status_code == 302


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/", "http://[::1]/"])
def test_fetch_title_refuses_non_public_hosts(url):
    assert fetch_title(url) is None
