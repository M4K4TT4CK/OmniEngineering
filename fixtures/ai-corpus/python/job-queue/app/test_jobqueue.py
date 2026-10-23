import smtplib
import threading
import time

import pytest
from fastapi.testclient import TestClient

import handlers
from api import create_app
from jobqueue import FAILED, SUCCEEDED, JobQueue, JobType, UnknownJobType


def wait_for(jq, job_id, status, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = jq.get(job_id)
        if job["status"] == status:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job did not reach {status}: {jq.get(job_id)}")


@pytest.fixture
def make_queue():
    created = []

    def factory(types, **kwargs):
        kwargs.setdefault("retry_delay", 0)
        jq = JobQueue(types, **kwargs)
        jq.start()
        created.append(jq)
        return jq

    yield factory
    for jq in created:
        jq.stop()


# --- core queue -------------------------------------------------------------

def test_job_succeeds_and_stores_result(make_queue):
    jq = make_queue({"echo": JobType(lambda p: {"got": p["x"]})})
    job_id = jq.submit("echo", {"x": 1})
    job = wait_for(jq, job_id, SUCCEEDED)
    assert job["result"] == {"got": 1}
    assert job["attempts"] == 1


def test_unknown_type_rejected(make_queue):
    jq = make_queue({})
    with pytest.raises(UnknownJobType):
        jq.submit("nope", {})


def test_validation_error_rejected(make_queue):
    def validate(p):
        raise ValueError("bad")

    jq = make_queue({"t": JobType(lambda p: None, validate)})
    with pytest.raises(ValueError):
        jq.submit("t", {})


def test_retries_then_succeeds(make_queue):
    calls = []

    def flaky(p):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("boom")
        return "ok"

    jq = make_queue({"flaky": JobType(flaky)})
    job = wait_for(jq, jq.submit("flaky", {}), SUCCEEDED)
    assert job["attempts"] == 3
    assert job["result"] == "ok"


def test_fails_after_three_retries(make_queue):
    calls = []

    def always_fail(p):
        calls.append(1)
        raise RuntimeError("nope")

    jq = make_queue({"bad": JobType(always_fail)})
    job = wait_for(jq, jq.submit("bad", {}), FAILED)
    assert len(calls) == 4  # initial attempt + 3 retries
    assert job["attempts"] == 4
    assert "nope" in job["error"]


def test_jobs_run_concurrently(make_queue):
    barrier = threading.Barrier(3, timeout=5)

    def meet(p):
        barrier.wait()  # only passes if 3 jobs are running at the same time
        return True

    jq = make_queue({"meet": JobType(meet)}, workers=3, max_retries=0)
    ids = [jq.submit("meet", {}) for _ in range(3)]
    for job_id in ids:
        wait_for(jq, job_id, SUCCEEDED)


def test_get_unknown_job_returns_none(make_queue):
    assert make_queue({}).get("missing") is None


# --- handlers ---------------------------------------------------------------

def test_resize_image(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.delenv("IMAGE_ROOT", raising=False)
    src = tmp_path / "pic.png"
    Image.new("RGB", (400, 200)).save(src)
    result = handlers.resize_image({"path": str(src), "width": 100, "height": 100})
    assert (result["width"], result["height"]) == (100, 50)
    with Image.open(result["output_path"]) as out:
        assert out.size == (100, 50)
    assert Image.open(src).size == (400, 200)


def test_resize_validation(monkeypatch):
    monkeypatch.delenv("IMAGE_ROOT", raising=False)
    with pytest.raises(ValueError):
        handlers.validate_resize({})
    with pytest.raises(ValueError):
        handlers.validate_resize({"path": "a.png", "width": -1})


def test_resize_respects_image_root(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_ROOT", str(tmp_path))
    handlers.validate_resize({"path": "ok.png"})
    with pytest.raises(ValueError):
        handlers.validate_resize({"path": "../escape.png"})
    with pytest.raises(ValueError):
        handlers.validate_resize({"path": "/etc/passwd"})


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.actions = []
        self.sent = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.actions.append("starttls")

    def login(self, user, password):
        self.actions.append(("login", user, password))

    def send_message(self, msg):
        self.sent = msg


def test_send_email(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "mail.example.com")
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("SMTP_USER", "me")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("SMTP_FROM", "app@example.com")
    handlers.send_email({"recipient": "you@example.com", "subject": "Hi", "body": "Hello"})
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("mail.example.com", 2525)
    assert smtp.actions == ["starttls", ("login", "me", "pw")]
    assert smtp.sent["To"] == "you@example.com"
    assert smtp.sent["From"] == "app@example.com"
    assert smtp.sent["Subject"] == "Hi"
    assert smtp.sent.get_content().strip() == "Hello"


def test_send_email_requires_host(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with pytest.raises(RuntimeError):
        handlers.send_email({"recipient": "a@b.c", "subject": "s", "body": "b"})


def test_email_validation():
    with pytest.raises(ValueError):
        handlers.validate_email({"recipient": "a@b.c", "subject": "s"})


# --- HTTP API ---------------------------------------------------------------

@pytest.fixture
def client():
    types = {
        "echo": JobType(lambda p: {"got": p.get("x")}),
        "strict": JobType(lambda p: None, lambda p: (_ for _ in ()).throw(ValueError("bad")) if "x" not in p else None),
    }
    jq = JobQueue(types, workers=2, retry_delay=0)
    with TestClient(create_app(jq)) as c:
        c.jq = jq
        yield c


def test_api_submit_and_poll(client):
    resp = client.post("/jobs", json={"type": "echo", "payload": {"x": 5}})
    assert resp.status_code == 202
    job_id = resp.json()["id"]
    wait_for(client.jq, job_id, SUCCEEDED)
    body = client.get(f"/jobs/{job_id}").json()
    assert body["status"] == "succeeded"
    assert body["result"] == {"got": 5}


def test_api_unknown_type(client):
    assert client.post("/jobs", json={"type": "nope", "payload": {}}).status_code == 400


def test_api_invalid_payload(client):
    assert client.post("/jobs", json={"type": "strict", "payload": {}}).status_code == 422


def test_api_missing_job(client):
    assert client.get("/jobs/does-not-exist").status_code == 404
