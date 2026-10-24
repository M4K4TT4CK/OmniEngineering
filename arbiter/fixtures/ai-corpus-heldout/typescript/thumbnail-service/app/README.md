# Thumbnail service

An HTTP service that turns an uploaded image, or an image at a URL, into thumbnails at the widths you ask for. Thumbnails are generated on the first request that needs them, cached on local disk, and served over HTTP after that.

Built with Node.js 20+, TypeScript, Express, Multer, and sharp (libvips).

## API

### `POST /thumbnails`

Send exactly one image source plus a list of widths.

Multipart upload:

```
curl -F image=@cat.jpg -F widths=100,320 http://localhost:3000/thumbnails
```

JSON with a URL:

```
curl -H 'Content-Type: application/json' \
     -d '{"url": "https://example.com/cat.jpg", "widths": [100, 320]}' \
     http://localhost:3000/thumbnails
```

Response (`201`):

```json
{
  "id": "3f1c…",
  "thumbnails": [
    { "width": 100, "url": "http://localhost:3000/thumbnails/3f1c…/100.webp" },
    { "width": 320, "url": "http://localhost:3000/thumbnails/3f1c…/320.webp" }
  ]
}
```

- `widths` is an array, or a comma-separated string. Each value must be an integer from 1 to 4096. Duplicates are dropped. At most 10 distinct widths per request.
- Errors come back as `{ "error": "..." }` with status 400 (bad input), 413 (too large), 502 (URL fetch failed), or 500.

### `GET /thumbnails/:id/:width.webp`

Serves a cached thumbnail as `image/webp` with a one-year immutable cache header. Returns `404` if that thumbnail has not been generated.

## Running

```
npm install
npm start            # listens on $PORT (default 3000), caches in $CACHE_DIR (default ./cache)
npm test
npm run typecheck
```

## How it works

- **Cache layout:** `<cacheDir>/<id>/original` holds the source image and `<cacheDir>/<id>/<width>.webp` holds each thumbnail. The `id` is the SHA-256 of the uploaded bytes, or of the URL string for URL sources. The same image sent twice maps to the same id and nothing is regenerated.
- **Generation:** sharp auto-rotates by EXIF orientation, then resizes to the requested width, keeping the aspect ratio. Images are never enlarged, so a thumbnail requested wider than the source comes out at the source width.
- **Atomic writes:** files are written to a temp name and renamed into place. Concurrent requests for the same thumbnail share one generation job.
- **URL sources:** only `http` and `https` are accepted. Hosts that resolve to loopback, private, link-local, or otherwise non-public addresses are refused unless `allowPrivateAddresses` is set. Redirects are refused. Downloads are capped at 20 MB and time out after 10 s. The address check runs before the cache lookup.

## Limitations

- **DNS rebinding:** the address check resolves the host once and fetch resolves it again. A hostile DNS server could return a public address for the check and a private one for the fetch. Closing that gap needs a custom `lookup` that pins the checked address.
- **No eviction:** the cache grows without bound. Clear it by hand or add a size- or age-based sweep.
- **Single process:** the in-flight dedupe lives in memory, so it does not coordinate across multiple instances sharing one cache directory.
- **Upload size:** uploads over 20 MB are rejected by Multer with 413.
