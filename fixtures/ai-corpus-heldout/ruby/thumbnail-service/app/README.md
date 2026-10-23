# Thumbnail service

A small Sinatra app that makes image thumbnails with [libvips](https://www.libvips.org/).
Clients send an image (file upload or URL) and a list of widths. The service
returns a URL for each thumbnail. Each thumbnail is generated the first time
it's requested, cached on local disk, and served from that cache.

## Setup

Requires Ruby 3.1+ and libvips 8.x (`apt install libvips42` / `brew install vips`).

```sh
bundle install
bundle exec rackup -p 9292          # start the server
bundle exec ruby app_test.rb        # run the tests
```

## API

### `POST /thumbnails`

Multipart upload:

```sh
curl -F image=@cat.jpg -F widths=100,300 http://localhost:9292/thumbnails
```

From a URL (form fields or JSON):

```sh
curl -H 'Content-Type: application/json' \
     -d '{"url": "https://example.com/cat.jpg", "widths": [100, 300]}' \
     http://localhost:9292/thumbnails
```

`widths` can be a comma-separated string, a repeated `widths[]` field, or a
JSON array. Response:

```json
{
  "id": "3f1c…",
  "thumbnails": [
    {"width": 100, "url": "http://localhost:9292/thumbnails/3f1c…/100.jpg"},
    {"width": 300, "url": "http://localhost:9292/thumbnails/3f1c…/300.jpg"}
  ]
}
```

Errors come back as JSON `{"error": "..."}` with status 400 (bad input),
413 (too large), 415 (unsupported type) or 422 (undecodable image, or the
URL could not be fetched).

### `GET /thumbnails/:id/:width.:ext`

Serves a cached thumbnail with long-lived `immutable` cache headers.

## Behaviour

- **Caching**: the cache is content-addressed: `CACHE_DIR/<sha256 of source>/<width>.<ext>`.
  The same image sent again, whether uploaded or fetched by URL, reuses the
  existing files. Files are written to a temp file and then renamed into place,
  so concurrent requests never serve a half-written thumbnail.
- **Formats**: JPEG, PNG, GIF and WebP, detected from file contents. Thumbnails
  keep the source format. Animated GIFs are reduced to their first frame.
- **Resizing**: aspect ratio is kept, EXIF orientation is applied, and images
  are never upscaled. A width larger than the source gives an image at the
  source size. EXIF/XMP/IPTC metadata (e.g. GPS location) is removed.
- **URL fetching**: only `http`/`https`. Hostnames are resolved once, and the
  request is refused if the host resolves to a loopback, private, link-local
  or other non-public address. The connection then goes to that same resolved
  address, which blocks DNS rebinding. Redirects (max 3) are checked the same
  way. Downloads have timeouts and a size cap.

## Configuration (environment variables)

| Variable          | Default        | Meaning                                       |
|-------------------|----------------|-----------------------------------------------|
| `CACHE_DIR`       | `./cache`      | Where thumbnails are stored                   |
| `MAX_IMAGE_BYTES` | `10485760`     | Max upload / download size                    |
| `MAX_PIXELS`      | `50000000`     | Max source width × height (decompression bombs) |
| `MAX_WIDTH`       | `4096`         | Largest allowed thumbnail width               |
| `MAX_WIDTHS`      | `10`           | Max widths per request                        |
| `PUBLIC_BASE_URL` | request host   | Base for returned URLs, e.g. `https://img.example.com` |

## Production notes

- Set `PUBLIC_BASE_URL`. Otherwise returned URLs are built from the
  client-supplied `Host` header.
- Uploads with a `Content-Length` header that's too large are rejected up
  front. Chunked uploads are only checked after Rack has buffered them, so also
  cap body size at your reverse proxy.
- Nothing evicts old entries from the cache. Clean up `CACHE_DIR` with cron
  (e.g. by access time) if disk space matters.
- There's no authentication or rate limiting. Put these in front of the
  service if it's exposed publicly.
