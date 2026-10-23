import io
import ipaddress
import socket

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import service
from service import Settings, create_app

PUBLIC_IP = "93.184.216.34"


def image_bytes(size=(200, 100), fmt="PNG", mode="RGB", color=(255, 0, 0), **save_options):
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format=fmt, **save_options)
    return buf.getvalue()


def fake_resolver(mapping):
    def resolve(host, port):
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            pass
        if host not in mapping:
            raise socket.gaierror("unknown host")
        return mapping[host]

    return resolve


@pytest.fixture
def make_client(tmp_path):
    def factory(**overrides):
        settings = Settings(cache_dir=tmp_path / "cache", **overrides)
        return TestClient(create_app(settings)), settings

    return factory


@pytest.fixture
def client(make_client):
    return make_client()[0]


def upload(client, data, widths, filename="image.png"):
    return client.post("/thumbnails", files={"file": (filename, data, "application/octet-stream")}, data={"widths": widths})


# --- uploads and serving ---------------------------------------------------


def test_upload_returns_urls_that_serve_thumbnails(client):
    response = upload(client, image_bytes((200, 100)), "50,100")
    assert response.status_code == 200
    thumbs = response.json()["thumbnails"]
    assert [(t["width"], t["height"]) for t in thumbs] == [(50, 25), (100, 50)]

    for thumb in thumbs:
        served = client.get(thumb["url"])
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/png"
        assert "immutable" in served.headers["cache-control"]
        assert Image.open(io.BytesIO(served.content)).size == (thumb["width"], thumb["height"])


def test_widths_as_repeated_fields_are_deduplicated(client):
    response = upload(client, image_bytes(), ["40", "40,80"])
    assert response.status_code == 200
    assert [t["requested_width"] for t in response.json()["thumbnails"]] == [40, 80]


def test_jpeg_stays_jpeg_and_is_never_upscaled(client):
    response = upload(client, image_bytes((120, 60), fmt="JPEG"), "60,500", filename="a.jpg")
    assert response.status_code == 200
    small, large = response.json()["thumbnails"]
    assert small["url"].endswith("/60.jpg") and (small["width"], small["height"]) == (60, 30)
    assert large["url"].endswith("/500.jpg") and (large["width"], large["height"]) == (120, 60)
    served = client.get(large["url"])
    assert served.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(served.content)).size == (120, 60)


def test_gif_is_converted_to_png(client):
    response = upload(client, image_bytes((40, 40), fmt="GIF", mode="P", color=1), "20")
    assert response.status_code == 200
    assert response.json()["thumbnails"][0]["url"].endswith("/20.png")


def test_transparent_palette_png_keeps_alpha(client):
    data = image_bytes((40, 40), mode="P", color=0, transparency=0)
    thumb = upload(client, data, "20").json()["thumbnails"][0]
    assert Image.open(io.BytesIO(client.get(thumb["url"]).content)).mode == "RGBA"


def test_exif_orientation_is_applied(client):
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees: the 100x50 image displays as 50x100
    data = image_bytes((100, 50), fmt="JPEG", exif=exif.tobytes())
    thumb = upload(client, data, "25").json()["thumbnails"][0]
    assert (thumb["width"], thumb["height"]) == (25, 50)
    served = Image.open(io.BytesIO(client.get(thumb["url"]).content))
    assert served.size == (25, 50)
    assert served.getexif().get(0x0112) is None


def test_cached_thumbnails_are_not_regenerated(client, monkeypatch):
    data = image_bytes()
    first = upload(client, data, "50").json()

    def fail(*args, **kwargs):
        raise AssertionError("thumbnail was regenerated")

    monkeypatch.setattr(service, "_save_atomic", fail)
    second = upload(client, data, "50").json()
    assert second == first


def test_only_missing_widths_are_rendered(client, monkeypatch):
    data = image_bytes()
    upload(client, data, "50")
    saved = []
    original = service._save_atomic
    monkeypatch.setattr(service, "_save_atomic", lambda img, path, *a: (saved.append(path.name), original(img, path, *a)))
    assert upload(client, data, "50,70").status_code == 200
    assert saved == ["70.png"]


# --- input validation -------------------------------------------------------


@pytest.mark.parametrize("widths", ["", "0", "-5", "abc", "10.5", "100,", "5000", ",".join(map(str, range(1, 12)))])
def test_invalid_widths_are_rejected(client, widths):
    assert upload(client, image_bytes(), widths).status_code == 400


def test_widths_are_required(client):
    response = client.post("/thumbnails", files={"file": ("a.png", image_bytes(), "image/png")})
    assert response.status_code == 400


def test_json_widths_must_be_integers(client):
    response = client.post("/thumbnails", json={"url": "http://example.com/a.png", "widths": [True]})
    assert response.status_code == 400


def test_requires_exactly_one_source(client):
    assert client.post("/thumbnails", data={"widths": "50"}).status_code == 400
    both = client.post(
        "/thumbnails",
        files={"file": ("a.png", image_bytes(), "image/png")},
        data={"widths": "50", "url": "http://example.com/a.png"},
    )
    assert both.status_code == 400


def test_non_image_is_rejected(client):
    assert upload(client, b"definitely not an image", "50").status_code == 415


def test_unsupported_content_type(client):
    response = client.post("/thumbnails", content=b"x", headers={"content-type": "text/plain"})
    assert response.status_code == 415


def test_upload_over_size_limit(make_client):
    client, _ = make_client(max_image_bytes=1000)
    assert upload(client, b"\0" * 5000, "50").status_code == 413


def test_request_body_over_limit_is_rejected_early(make_client):
    client, _ = make_client(max_image_bytes=1000)
    assert upload(client, b"\0" * 200_000, "50").status_code == 413


def test_chunked_body_over_limit_is_rejected(make_client):
    client, _ = make_client(max_image_bytes=1000)
    response = client.post(
        "/thumbnails",
        content=iter([b" " * 40_000] * 3),  # no Content-Length header
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413


def test_too_many_pixels(make_client):
    client, _ = make_client(max_pixels=100 * 100)
    assert upload(client, image_bytes((200, 200)), "50").status_code == 413


# --- URLs ---------------------------------------------------------------------


def url_client(make_client, handler, hosts=None, **overrides):
    resolver = fake_resolver(hosts or {"images.example.com": [PUBLIC_IP]})
    return make_client(resolver=resolver, transport=httpx.MockTransport(handler), **overrides)[0]


def test_fetch_from_url_connects_to_the_checked_ip(make_client):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=image_bytes((200, 100)))

    client = url_client(make_client, handler)
    response = client.post("/thumbnails", json={"url": "https://images.example.com/cat.png?v=2", "widths": [50]})
    assert response.status_code == 200
    assert (response.json()["thumbnails"][0]["height"]) == 25

    (request,) = seen
    assert request.url.host == PUBLIC_IP
    assert request.url.path == "/cat.png" and request.url.query == b"v=2"
    assert request.headers["host"] == "images.example.com"
    assert request.extensions["sni_hostname"] == "images.example.com"


def test_url_and_upload_of_same_image_share_cache(make_client):
    data = image_bytes()
    client = url_client(make_client, lambda request: httpx.Response(200, content=data))
    via_url = client.post("/thumbnails", data={"url": "http://images.example.com/a.png", "widths": "50"})
    via_upload = upload(client, data, "50")
    assert via_url.json() == via_upload.json()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/a.png",
        "http://localhost/a.png",
        "http://10.0.0.5/a.png",
        "http://192.168.1.1/a.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/a.png",
        "http://[::ffff:127.0.0.1]/a.png",
        "http://rebind.example.com/a.png",
        "http://nonexistent.example.com/a.png",
        "ftp://images.example.com/a.png",
        "file:///etc/passwd",
        "http://user:secret@images.example.com/a.png",
        "not a url",
    ],
)
def test_unsafe_urls_are_rejected_without_fetching(make_client, url):
    seen = []
    hosts = {
        "images.example.com": [PUBLIC_IP],
        "localhost": ["127.0.0.1"],
        "rebind.example.com": [PUBLIC_IP, "10.0.0.1"],
    }
    client = url_client(make_client, lambda r: seen.append(r) or httpx.Response(200), hosts)
    response = client.post("/thumbnails", json={"url": url, "widths": [50]})
    assert response.status_code == 400
    assert seen == []


def test_redirects_are_followed_and_rechecked(make_client):
    def handler(request):
        if request.url.path == "/old.png":
            return httpx.Response(301, headers={"location": "/new.png"})
        return httpx.Response(200, content=image_bytes())

    client = url_client(make_client, handler)
    response = client.post("/thumbnails", json={"url": "http://images.example.com/old.png", "widths": [50]})
    assert response.status_code == 200


def test_redirect_to_private_address_is_blocked(make_client):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    client = url_client(make_client, handler)
    response = client.post("/thumbnails", json={"url": "http://images.example.com/a.png", "widths": [50]})
    assert response.status_code == 400
    assert len(seen) == 1


def test_redirect_loop_is_bounded(make_client):
    client = url_client(make_client, lambda r: httpx.Response(302, headers={"location": "/again"}))
    response = client.post("/thumbnails", json={"url": "http://images.example.com/a.png", "widths": [50]})
    assert response.status_code == 502


def test_upstream_error_status(make_client):
    client = url_client(make_client, lambda r: httpx.Response(404))
    response = client.post("/thumbnails", json={"url": "http://images.example.com/a.png", "widths": [50]})
    assert response.status_code == 502


def test_upstream_connection_failure(make_client):
    def handler(request):
        raise httpx.ConnectError("refused")

    client = url_client(make_client, handler)
    response = client.post("/thumbnails", json={"url": "http://images.example.com/a.png", "widths": [50]})
    assert response.status_code == 502


def test_remote_image_over_size_limit(make_client):
    client = url_client(make_client, lambda r: httpx.Response(200, content=b"\0" * 5000), max_image_bytes=1000)
    response = client.post("/thumbnails", json={"url": "http://images.example.com/a.png", "widths": [50]})
    assert response.status_code == 413


# --- serving ------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/thumbnails/not-a-digest/50.png",
        f"/thumbnails/{'a' * 64}/50.png",
        f"/thumbnails/{'a' * 64}/50.txt",
        f"/thumbnails/{'a' * 64}/.tmp",
        f"/thumbnails/{'a' * 64}/..%2F..%2Fservice.py",
    ],
)
def test_unknown_or_malformed_thumbnail_paths_404(client, path):
    assert client.get(path).status_code == 404
