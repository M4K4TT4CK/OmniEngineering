# Thumbnail service

A small Go HTTP service that generates image thumbnails and caches them on local disk.
It uses the standard library plus `golang.org/x/image` (CatmullRom resampling and WebP decoding).

## Running

```sh
go mod tidy          # fetches golang.org/x/image and writes go.sum
go test ./...
go run . -addr :8080 -cache-dir ./cache -base-url http://localhost:8080
```

Flags: `-addr`, `-cache-dir`, `-base-url` (the public prefix used in the URLs the service returns),
and `-max-upload-mb` (default 20).

## API

### `POST /thumbnails`

Upload a file (multipart):

```sh
curl -F file=@photo.jpg -F widths=100,300 http://localhost:8080/thumbnails
```

Or pass a URL (JSON, or a multipart `url` field):

```sh
curl -H 'Content-Type: application/json' \
     -d '{"url":"https://example.com/photo.jpg","widths":[100,300]}' \
     http://localhost:8080/thumbnails
```

Response:

```json
{"thumbnails":[
  {"width":100,"url":"http://localhost:8080/thumbnails/<sha256>_100.jpg"},
  {"width":300,"url":"http://localhost:8080/thumbnails/<sha256>_300.jpg"}
]}
```

Errors return `{"error": "..."}` with status 400 (bad input or disallowed URL), 413 (too large),
415 (not a supported image), 422 (too many pixels), or 502 (remote fetch failed).

### `GET /thumbnails/{name}`

Serves a cached thumbnail. Responses are marked `immutable` because names are content-addressed.

## Behaviour

- Input formats: JPEG, PNG, GIF (first frame), WebP. JPEG sources produce JPEG thumbnails; everything
  else produces PNG.
- The aspect ratio is preserved. Images are never upscaled, so asking for a width larger than the
  source gives you a thumbnail at the source width.
- The cache key is the SHA-256 of the source bytes, so the same image always maps to the same URLs no
  matter where it came from. Thumbnails are generated on the first request for each width and reused
  after that. URL sources are still downloaded on every request, because the remote content can change.
- Files are written to a temp file and then renamed, so readers never see a partial thumbnail.
  The cache has no eviction.

## Limits and safety

- Widths must be between 1 and 4096, and a request may ask for at most 10 distinct widths.
- Source images are limited to 20 MiB and 50 megapixels. Dimensions are checked from the image header
  before decoding, which protects against decompression bombs. At most 4 decode/resize jobs run at once.
- URL fetching only allows `http`/`https`, ignores proxy environment variables, follows at most 3
  redirects, and has timeouts. Every IP the service connects to is checked at dial time, and loopback,
  private, link-local, CGNAT, NAT64/6to4, and other non-public ranges are refused. This also covers
  redirects and DNS rebinding.
- `GET` only serves file names that match `<64 hex>_<width>.(jpg|png)`, which prevents path traversal.
