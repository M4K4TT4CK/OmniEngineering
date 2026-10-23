import json

import pytest
from fastapi.testclient import TestClient

from app import create_app, sign

SECRET = "shh"
TOKEN = "admin-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeForwarder:
    def __init__(self, status=200, exc=None):
        self.status, self.exc, self.calls = status, exc, []

    def __call__(self, url, body, headers):
        self.calls.append((url, body, headers))
        if self.exc:
            raise self.exc
        return self.status


@pytest.fixture
def fwd():
    return FakeForwarder()


@pytest.fixture
def client(tmp_path, fwd):
    app = create_app(
        db_path=str(tmp_path / "t.db"),
        secret=SECRET,
        api_token=TOKEN,
        callbacks={"m1": "https://merchant.example/hook"},
        forwarder=fwd,
    )
    return TestClient(app)


def post(client, event, secret=SECRET, raw=None):
    body = raw if raw is not None else json.dumps(event).encode()
    return client.post("/webhooks", content=body, headers={"X-Signature": sign(secret, body)})


def event(id="evt_1", type="payment.succeeded", merchant="m1"):
    return {"id": id, "type": type, "merchant_id": merchant, "amount": 100}


def test_missing_signature_rejected(client):
    assert client.post("/webhooks", json=event()).status_code == 401


def test_bad_signature_rejected_and_not_stored(client, fwd):
    assert post(client, event(), secret="wrong").status_code == 401
    assert client.get("/merchants/m1/events", headers=AUTH).json()["events"] == []
    assert fwd.calls == []


def test_sha256_prefix_accepted(client):
    body = json.dumps(event()).encode()
    r = client.post("/webhooks", content=body, headers={"X-Signature": "sha256=" + sign(SECRET, body)})
    assert r.status_code == 200


def test_invalid_json_rejected(client):
    assert post(client, None, raw=b"not json").status_code == 400


def test_missing_fields_rejected(client):
    assert post(client, {"id": "x"}).status_code == 400
    assert post(client, [1, 2]).status_code == 400


def test_success_event_stored_and_forwarded(client, fwd):
    body = json.dumps(event()).encode()
    r = client.post("/webhooks", content=body, headers={"X-Signature": sign(SECRET, body)})
    assert r.json() == {"status": "received"}
    assert len(fwd.calls) == 1
    url, sent, headers = fwd.calls[0]
    assert url == "https://merchant.example/hook"
    assert sent == body
    assert headers["X-Signature"] == sign(SECRET, body)

    (stored,) = client.get("/merchants/m1/events", headers=AUTH).json()["events"]
    assert stored["id"] == "evt_1"
    assert stored["forward_status"] == "delivered"
    assert stored["payload"]["amount"] == 100


def test_non_success_event_stored_not_forwarded(client, fwd):
    post(client, event(type="payment.failed"))
    assert fwd.calls == []
    (stored,) = client.get("/merchants/m1/events", headers=AUTH).json()["events"]
    assert stored["forward_status"] == "none"


def test_merchant_without_callback_not_forwarded(client, fwd):
    assert post(client, event(merchant="other")).status_code == 200
    assert fwd.calls == []
    assert len(client.get("/merchants/other/events", headers=AUTH).json()["events"]) == 1


def test_duplicate_event_not_restored_or_reforwarded(client, fwd):
    assert post(client, event()).json()["status"] == "received"
    assert post(client, event()).json()["status"] == "duplicate"
    assert len(fwd.calls) == 1
    assert len(client.get("/merchants/m1/events", headers=AUTH).json()["events"]) == 1


def test_forward_http_error_recorded(tmp_path):
    fwd = FakeForwarder(status=500)
    c = TestClient(create_app(str(tmp_path / "a.db"), SECRET, TOKEN, {"m1": "http://x.test/h"}, fwd))
    assert post(c, event()).status_code == 200  # provider still gets a 200
    (stored,) = c.get("/merchants/m1/events", headers=AUTH).json()["events"]
    assert stored["forward_status"] == "failed"
    assert stored["forward_detail"] == "HTTP 500"


def test_forward_exception_recorded(tmp_path):
    fwd = FakeForwarder(exc=TimeoutError())
    c = TestClient(create_app(str(tmp_path / "a.db"), SECRET, TOKEN, {"m1": "http://x.test/h"}, fwd))
    assert post(c, event()).status_code == 200
    (stored,) = c.get("/merchants/m1/events", headers=AUTH).json()["events"]
    assert stored["forward_status"] == "failed"
    assert stored["forward_detail"] == "TimeoutError"


def test_list_requires_auth(client):
    assert client.get("/merchants/m1/events").status_code == 401
    assert client.get("/merchants/m1/events", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_list_newest_first_limit_and_isolation(client):
    for i in range(5):
        post(client, event(id=f"e{i}", type="payment.failed"))
    post(client, event(id="other", merchant="m2"))
    events = client.get("/merchants/m1/events?limit=3", headers=AUTH).json()["events"]
    assert [e["id"] for e in events] == ["e4", "e3", "e2"]
    assert client.get("/merchants/m1/events?limit=0", headers=AUTH).status_code == 422


def test_config_validation(tmp_path):
    with pytest.raises(ValueError):
        create_app(str(tmp_path / "a.db"), "", TOKEN, {})
    with pytest.raises(ValueError):
        create_app(str(tmp_path / "a.db"), SECRET, TOKEN, {"m1": "file:///etc/passwd"})
