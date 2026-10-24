# Thumbnail service

A small FastAPI service. You send it an image (as an upload or a URL) and a list of widths. It returns the URLs of thumbnails at those widths, renders the ones it doesn't have yet, and serves them from a disk cache.

## Running

```sh
pip install -r requirements.txt
THUMBNAIL_CACHE_DIR=/var/cache/thumbnails uvicorn service:app --port 8000
pytest
```

The cache directory defaults to `./thumbnail-cache` and is created on first use. Python 3.10+.

## API

### `POST /thumbnails`

Upload a file (multipart):

```sh
curl -F file=@cat.jpg -F widths=100,300 http://localhost:8000/thumbnails
```

Or give a URL (JSON or a form field):

```sh
curl -H 'content-type: application/json' \
     -d '{"url": "https://example.com/cat.jpg", "widths": [100, 300]}' \
     http://localhost:8000/thumbnails
```

You can send `widths` as a comma-separated string, as repeated form fields, or as a JSON list. Each width must be between 1 and 4096, and a request can have at most 10 widths. Response:

```json
{
  "id": "3f2a…",
  "thumbnails": [
    {"requested_width": 100, "width": 100, "height": 75, "url": "http://localhost:8000/thumbnails/3f2a…/100.jpg"},
    {"requested_width": 300, "width": 300, "height": 225, "url": "http://localhost:8000/thumbnails/3f2a…/300.jpg"}
  ]
}
```

Errors: `400` for bad input or a disallowed URL, `413` if the image is too large (bytes or pixels), `415` if the file isn't a supported image, `502` if fetching the URL failed.

### `GET /thumbnails/{id}/{width}.{ext}`

Returns the cached thumbnail. The response has `Cache-Control: immutable` because a given URL always holds the same bytes.

## Behaviour

- **Caching.** Images are keyed by the SHA-256 of their bytes, so the same image gets the same thumbnails whether it was uploaded or fetched. Only widths that aren't on disk yet get rendered. Files are written to a temp file and then renamed, so concurrent requests never see a half-written file. URLs are downloaded again on every POST because the remote content may have changed. If the bytes are the same, nothing is re-rendered.
- **Output.** JPEG, PNG and WebP sources keep their format. GIF, BMP and TIFF become PNG, and animated images use their first frame. Aspect ratio is preserved. Images are never upscaled: if you ask for a width larger than the source, you get the source size, and `width`/`height` in the response report the real size. EXIF orientation is applied, and EXIF metadata (e.g. GPS) is removed.
- **Limits** (fields on `Settings`): 20 MiB per image, 40 megapixels, 4 images decoding at once. Pixel limits are checked from the image header before any decoding.
- **URL fetching (SSRF protection).** Only `http`/`https` URLs are allowed, and URLs with credentials are rejected. Every address the host resolves to must be public: loopback, private, link-local, cloud metadata, multicast and reserved ranges are refused, including IPv4-mapped IPv6. The connection goes to the IP that was checked, so DNS rebinding can't swap it. The `Host` header and TLS SNI/certificate checks still use the real hostname. Up to 3 redirects are followed, and each one is checked again. Proxy environment variables are ignored.

## Not included

- No cache eviction. The cache grows until you prune it, e.g. with a cron job that deletes old directories.
- No authentication or rate limiting. Put the service behind a reverse proxy for those.
- URLs in the response are built from the request's host. Behind a proxy, run uvicorn with `--proxy-headers --forwarded-allow-ips=…` so the URLs come out right.
