import io

import pytest
from PIL import Image

from app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app({"UPLOAD_DIR": str(tmp_path / "uploads"), "TESTING": True, "MAX_CONTENT_LENGTH": 1024 * 1024})
    return app.test_client()


def upload(client, name, data, **kwargs):
    return client.post("/files", data={"file": (io.BytesIO(data), name)}, content_type="multipart/form-data", **kwargs)


def png_bytes(size=(400, 200)):
    buf = io.BytesIO()
    Image.new("RGB", size, "red").save(buf, format="PNG")
    return buf.getvalue()


def test_upload_and_download(client):
    r = upload(client, "hello.txt", b"hello")
    assert r.status_code == 201
    assert r.get_json() == {"name": "hello.txt", "size": 5}
    r = client.get("/files/hello.txt")
    assert r.status_code == 200
    assert r.data == b"hello"


def test_upload_requires_file(client):
    assert client.post("/files", data={}, content_type="multipart/form-data").status_code == 400


def test_upload_duplicate_conflicts_unless_overwrite(client):
    upload(client, "a.txt", b"one")
    assert upload(client, "a.txt", b"two").status_code == 409
    assert client.get("/files/a.txt").data == b"one"
    assert upload(client, "a.txt", b"three", query_string={"overwrite": "true"}).status_code == 201
    assert client.get("/files/a.txt").data == b"three"


def test_upload_sanitizes_name(client, tmp_path):
    r = upload(client, "../../evil.txt", b"x")
    assert r.status_code == 201
    assert r.get_json()["name"] == "evil.txt"
    assert not (tmp_path / "evil.txt").exists()
    assert (tmp_path / "uploads" / "evil.txt").exists()


def test_upload_too_large(client):
    assert upload(client, "big.bin", b"x" * (1024 * 1024 + 1)).status_code == 413


def test_list_files(client):
    assert client.get("/files").get_json() == []
    upload(client, "b.txt", b"12")
    upload(client, "a.txt", b"1234")
    items = client.get("/files").get_json()
    assert [i["name"] for i in items] == ["a.txt", "b.txt"]
    assert [i["size"] for i in items] == [4, 2]
    assert "T" in items[0]["uploaded_at"]


def test_list_excludes_thumbnail_dir(client):
    upload(client, "pic.png", png_bytes())
    client.get("/files/pic.png/thumbnail")
    assert [i["name"] for i in client.get("/files").get_json()] == ["pic.png"]


def test_download_missing_and_invalid_names(client):
    assert client.get("/files/nope.txt").status_code == 404
    assert client.get("/files/.thumbnails").status_code == 404
    assert client.get("/files/..%2Fsecret").status_code == 404


def test_delete(client):
    upload(client, "a.txt", b"x")
    assert client.delete("/files/a.txt").status_code == 204
    assert client.get("/files/a.txt").status_code == 404
    assert client.delete("/files/a.txt").status_code == 404


def test_thumbnail(client):
    upload(client, "pic.png", png_bytes((400, 200)))
    r = client.get("/files/pic.png/thumbnail?size=100")
    assert r.status_code == 200
    assert r.mimetype == "image/png"
    assert Image.open(io.BytesIO(r.data)).size == (100, 50)
    # second request is served from cache and is identical
    assert client.get("/files/pic.png/thumbnail?size=100").data == r.data


def test_thumbnail_default_size_does_not_upscale(client):
    upload(client, "small.png", png_bytes((20, 10)))
    r = client.get("/files/small.png/thumbnail")
    assert Image.open(io.BytesIO(r.data)).size == (20, 10)


def test_thumbnail_errors(client):
    upload(client, "a.txt", b"not an image")
    upload(client, "pic.png", png_bytes())
    assert client.get("/files/a.txt/thumbnail").status_code == 415
    assert client.get("/files/missing.png/thumbnail").status_code == 404
    assert client.get("/files/pic.png/thumbnail?size=abc").status_code == 400
    assert client.get("/files/pic.png/thumbnail?size=0").status_code == 400
    assert client.get("/files/pic.png/thumbnail?size=999999").status_code == 400


def test_overwrite_invalidates_thumbnail(client):
    upload(client, "pic.png", png_bytes((400, 200)))
    client.get("/files/pic.png/thumbnail?size=100")
    upload(client, "pic.png", png_bytes((200, 400)), query_string={"overwrite": "1"})
    r = client.get("/files/pic.png/thumbnail?size=100")
    assert Image.open(io.BytesIO(r.data)).size == (50, 100)


def test_delete_removes_thumbnails(client, tmp_path):
    upload(client, "pic.png", png_bytes())
    client.get("/files/pic.png/thumbnail")
    client.delete("/files/pic.png")
    assert not (tmp_path / "uploads" / ".thumbnails" / "pic.png").exists()
