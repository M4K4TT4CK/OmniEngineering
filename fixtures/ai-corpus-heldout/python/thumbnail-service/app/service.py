"""Image thumbnail service.

POST /thumbnails takes an uploaded image or an image URL plus a list of widths,
renders any thumbnails that are not already on disk, and returns their URLs.
GET /thumbnails/{id}/{width}.{ext} serves them from the disk cache.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import ipaddress
import json
import os
import re
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageOps
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

# Decoders we are willing to run on untrusted input.
INPUT_FORMATS = ("JPEG", "PNG", "GIF", "WEBP", "BMP", "TIFF")
# Source format -> (output format, extension). Everything else is written as PNG.
OUTPUT_FORMATS = {"JPEG": ("JPEG", "jpg"), "PNG": ("PNG", "png"), "WEBP": ("WEBP", "webp")}
OUTPUT_MODES = {"JPEG": {"L", "RGB"}, "PNG": {"L", "LA", "RGB", "RGBA"}, "WEBP": {"RGB", "RGBA"}}
# exif=b"" keeps metadata such as GPS position out of the thumbnails.
SAVE_OPTIONS = {
    "JPEG": {"quality": 85, "optimize": True, "exif": b""},
    "PNG": {"optimize": True, "exif": b""},
    "WEBP": {"quality": 80, "exif": b""},
}
MEDIA_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}

ORIENTATION_TAG = 0x0112
SWAPPED_ORIENTATIONS = {5, 6, 7, 8}  # EXIF orientations that rotate by 90 degrees

DIGEST_RE = re.compile(r"[0-9a-f]{64}")
FILENAME_RE = re.compile(r"[1-9][0-9]{0,5}\.(jpg|png|webp)")
WIDTH_RE = re.compile(r"[0-9]{1,6}")

FORM_OVERHEAD_BYTES = 64 * 1024  # multipart boundaries and the other form fields


def resolve_host(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


@dataclass
class Settings:
    cache_dir: Path
    max_image_bytes: int = 20 * 1024 * 1024
    max_pixels: int = 40_000_000
    max_width: int = 4096
    max_widths: int = 10
    max_concurrent_renders: int = 4
    fetch_timeout: float = 10.0
    max_redirects: int = 3
    resolver: Callable[[str, int], list[str]] = resolve_host
    transport: httpx.BaseTransport | None = None  # tests inject httpx.MockTransport


class BodySizeLimit:
    """Rejects request bodies over max_bytes, with or without a Content-Length header."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            response = JSONResponse({"detail": "request body too large"}, status_code=413)
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Raised inside the endpoint, so FastAPI turns it into a 413 response.
                    raise HTTPException(413, "request body too large")
            return message

        await self.app(scope, limited_receive, send)


def parse_widths(raw, settings: Settings) -> list[int]:
    """Accepts a list of ints (JSON) or strings such as "100,200" (form fields)."""
    if raw is None or raw == "" or raw == []:
        raise HTTPException(400, "'widths' is required")
    items = raw if isinstance(raw, list) else [raw]

    widths: list[int] = []
    for item in items:
        if isinstance(item, int) and not isinstance(item, bool):
            widths.append(item)
        elif isinstance(item, str):
            for part in item.split(","):
                part = part.strip()
                if not WIDTH_RE.fullmatch(part):
                    raise HTTPException(400, f"invalid width: {part!r}")
                widths.append(int(part))
        else:
            raise HTTPException(400, "widths must be integers")

    widths = list(dict.fromkeys(widths))
    if len(widths) > settings.max_widths:
        raise HTTPException(400, f"at most {settings.max_widths} widths per request")
    for width in widths:
        if not 1 <= width <= settings.max_width:
            raise HTTPException(400, f"widths must be between 1 and {settings.max_width}")
    return widths


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        ip = ip.ipv4_mapped or ip.sixtofour or ip
    return ip.is_global and not ip.is_multicast


def _pin_to_public_ip(url: str, settings: Settings) -> tuple[str, dict, dict]:
    """Resolves the URL's host once, refuses non-public addresses, and returns a
    request target that connects to the checked IP, so a second DNS lookup cannot
    redirect the connection to an internal host."""
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        raise HTTPException(400, "invalid URL")
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "only http and https URLs are supported")
    if parsed.userinfo:
        raise HTTPException(400, "URLs containing credentials are not supported")
    host = parsed.raw_host.decode("ascii")
    if not host:
        raise HTTPException(400, "URL has no host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        addresses = settings.resolver(host, port)
    except OSError:
        raise HTTPException(400, "could not resolve the URL's host")
    if not addresses or not all(_is_public(address) for address in addresses):
        raise HTTPException(400, "URL must point to a public internet address")

    ip = addresses[0]
    ip_host = f"[{ip}]" if ":" in ip else ip
    target = f"{parsed.scheme}://{ip_host}:{port}{parsed.raw_path.decode('ascii')}"
    headers = {"Host": parsed.netloc.decode("ascii")}
    # TLS certificates are still verified against the original hostname.
    extensions = {"sni_hostname": host} if parsed.scheme == "https" else {}
    return target, headers, extensions


def _read_limited(response: httpx.Response, limit: int) -> bytes:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "remote image is too large")
    body = bytearray()
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) > limit:
            raise HTTPException(413, "remote image is too large")
    return bytes(body)


def fetch_url(url: str, settings: Settings) -> bytes:
    for _ in range(settings.max_redirects + 1):
        target, headers, extensions = _pin_to_public_ip(url, settings)
        # A fresh client per hop: pooled connections are keyed by IP, and must not
        # be reused for a different hostname.
        with httpx.Client(
            transport=settings.transport, timeout=settings.fetch_timeout, trust_env=False
        ) as client:
            try:
                with client.stream("GET", target, headers=headers, extensions=extensions) as response:
                    if response.is_redirect:
                        try:
                            url = str(httpx.URL(url).join(response.headers["location"]))
                        except (KeyError, httpx.InvalidURL):
                            raise HTTPException(502, "remote server sent an invalid redirect")
                        continue
                    if response.status_code != 200:
                        raise HTTPException(502, f"remote server returned HTTP {response.status_code}")
                    return _read_limited(response, settings.max_image_bytes)
            except httpx.HTTPError as exc:
                raise HTTPException(502, "could not fetch the image") from exc
    raise HTTPException(502, "too many redirects")


def _fit(src_width: int, src_height: int, width: int) -> tuple[int, int]:
    """Thumbnails keep the aspect ratio and are never upscaled."""
    if width >= src_width:
        return src_width, src_height
    return width, max(1, round(src_height * width / src_width))


def _prepare(image: Image.Image, fmt: str) -> tuple[Image.Image, bytes | None]:
    image.load()
    source_mode = image.mode
    icc_profile = image.info.get("icc_profile")
    image = ImageOps.exif_transpose(image)
    if image.mode not in OUTPUT_MODES[fmt]:
        has_alpha = "A" in image.mode or "transparency" in image.info
        image = image.convert("RGBA" if has_alpha and fmt != "JPEG" else "RGB")
    # A colour profile only remains valid if the pixel data was not converted.
    return image, icc_profile if image.mode == source_mode else None


def _save_atomic(image: Image.Image, path: Path, fmt: str, icc_profile: bytes | None) -> None:
    """Writes via a temporary file so concurrent requests never see a partial file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            image.save(f, format=fmt, icc_profile=icc_profile, **SAVE_OPTIONS[fmt])
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def make_thumbnails(data: bytes, widths: list[int], settings: Settings):
    """Returns (id, extension, [(requested width, width, height)]), rendering only
    the thumbnails that are not cached yet. Images are keyed by content hash."""
    digest = hashlib.sha256(data).hexdigest()
    out_dir = settings.cache_dir / digest

    try:
        # Image.open only parses the header, so the size checks run before decoding.
        image = Image.open(io.BytesIO(data), formats=INPUT_FORMATS)
        src_width, src_height = image.size
        if src_width < 1 or src_height < 1:
            raise HTTPException(415, "image has no pixels")
        if src_width * src_height > settings.max_pixels:
            raise HTTPException(413, "image dimensions are too large")
        fmt, ext = OUTPUT_FORMATS.get(image.format, ("PNG", "png"))
        if image.getexif().get(ORIENTATION_TAG) in SWAPPED_ORIENTATIONS:
            src_width, src_height = src_height, src_width

        thumbs = [(width, *_fit(src_width, src_height, width)) for width in widths]
        missing = [t for t in thumbs if not (out_dir / f"{t[0]}.{ext}").is_file()]
        if missing:
            base, icc_profile = _prepare(image, fmt)
    except HTTPException:
        raise
    except Image.DecompressionBombError as exc:
        raise HTTPException(413, "image dimensions are too large") from exc
    except Exception as exc:  # Pillow raises many exception types for malformed files
        raise HTTPException(415, "file is not a supported image (JPEG, PNG, GIF, WebP, BMP, TIFF)") from exc

    if missing:
        out_dir.mkdir(parents=True, exist_ok=True)
        for requested, width, height in missing:
            thumb = base if base.size == (width, height) else base.resize((width, height), Image.Resampling.LANCZOS)
            _save_atomic(thumb, out_dir / f"{requested}.{ext}", fmt, icc_profile)
    return digest, ext, thumbs


async def _read_input(request: Request, settings: Settings):
    """Returns (url, uploaded bytes, raw widths); at most one of url/bytes is set."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()

    if content_type == "application/json":
        try:
            payload = json.loads(await request.body())
        except ValueError:
            raise HTTPException(400, "request body is not valid JSON")
        if not isinstance(payload, dict):
            raise HTTPException(400, "JSON body must be an object")
        url = payload.get("url")
        if url is not None and not isinstance(url, str):
            raise HTTPException(400, "'url' must be a string")
        return url or None, None, payload.get("widths")

    if content_type in ("multipart/form-data", "application/x-www-form-urlencoded"):
        async with request.form(max_files=1, max_fields=50) as form:
            url = form.get("url")
            upload = form.get("file")
            if url is not None and not isinstance(url, str):
                raise HTTPException(400, "'url' must be a text field")
            if upload is not None and not isinstance(upload, UploadFile):
                raise HTTPException(400, "'file' must be a file upload")
            data = None
            if upload is not None:
                data = await upload.read(settings.max_image_bytes + 1)
                if len(data) > settings.max_image_bytes:
                    raise HTTPException(413, "uploaded image is too large")
            return url or None, data, form.getlist("widths")

    raise HTTPException(415, "send multipart/form-data or application/json")


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Thumbnail service")
    app.add_middleware(BodySizeLimit, max_bytes=settings.max_image_bytes + FORM_OVERHEAD_BYTES)
    # Bounds the memory used by images being decoded at the same time.
    render_slots = asyncio.Semaphore(settings.max_concurrent_renders)

    @app.post("/thumbnails")
    async def create_thumbnails(request: Request):
        url, data, raw_widths = await _read_input(request, settings)
        if (url is None) == (data is None):
            raise HTTPException(400, "provide exactly one of 'file' or 'url'")
        widths = parse_widths(raw_widths, settings)

        if data is None:
            data = await run_in_threadpool(fetch_url, url, settings)
        async with render_slots:
            digest, ext, thumbs = await run_in_threadpool(make_thumbnails, data, widths, settings)

        return {
            "id": digest,
            "thumbnails": [
                {
                    "requested_width": requested,
                    "width": width,
                    "height": height,
                    "url": str(request.url_for("get_thumbnail", digest=digest, filename=f"{requested}.{ext}")),
                }
                for requested, width, height in thumbs
            ],
        }

    @app.get("/thumbnails/{digest}/{filename}", name="get_thumbnail")
    async def get_thumbnail(digest: str, filename: str):
        match = FILENAME_RE.fullmatch(filename)
        if not DIGEST_RE.fullmatch(digest) or not match:
            raise HTTPException(404, "not found")
        path = settings.cache_dir / digest / filename
        if not path.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(
            path,
            media_type=MEDIA_TYPES[match.group(1)],
            # Content-addressed: a given URL always holds the same bytes.
            headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
        )

    return app


app = create_app(Settings(cache_dir=Path(os.environ.get("THUMBNAIL_CACHE_DIR", "thumbnail-cache"))))
