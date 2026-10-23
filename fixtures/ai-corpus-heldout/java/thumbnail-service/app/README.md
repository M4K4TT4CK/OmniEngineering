# Thumbnail service

A small HTTP service that produces image thumbnails at requested widths. Thumbnails are
rendered the first time a width is requested for a given source, cached on local disk, and
served over HTTP from that cache. Built on the JDK's `com.sun.net.httpserver`, `ImageIO`, and
`java.net.http`, with Jackson for JSON.

## Requirements

- JDK 17+
- Maven 3.8+ (for the build and tests)

## Build and run

```sh
mvn test                                              # run the tests
mvn compile exec:java -Dexec.mainClass=ThumbnailService \
    -Dexec.args="8080 thumbnail-cache"                # port, cache directory
```

## API

### Create thumbnails

Upload an image as the raw body, with the widths as a query parameter:

```sh
curl -X POST "http://localhost:8080/thumbnails?widths=100,400" \
     -H "Content-Type: image/jpeg" --data-binary @photo.jpg
```

Or give a URL in a JSON body:

```sh
curl -X POST http://localhost:8080/thumbnails \
     -H "Content-Type: application/json" \
     -d '{"url": "https://example.com/photo.jpg", "widths": [100, 400]}'
```

Response (`200`):

```json
{
  "id": "3f2a...e9",
  "thumbnails": [
    {"width": 100, "url": "http://localhost:8080/thumbnails/3f2a...e9/100.png"},
    {"width": 400, "url": "http://localhost:8080/thumbnails/3f2a...e9/400.png"}
  ]
}
```

Thumbnails keep the source aspect ratio and are PNG.

### Fetch a thumbnail

`GET /thumbnails/{id}/{width}.png` returns the cached PNG, or `404` if that width has not been
created yet.

### Errors

Client errors return `400` with `{"error": "..."}`: bad widths (must be 1-4096, at most 10 per
request), unsupported or unreadable images, images over 20 MB or 25 megapixels, non-http(s)
URLs, and URLs that fail to fetch.

## Design

- **Source ID.** For uploads, the SHA-256 of the image bytes. For URLs, the SHA-256 of the URL
  string. Identical inputs share a cache entry.
- **Cache layout.** `{cacheDir}/{id}/{width}.png`. Files are written to a temp file and moved into
  place atomically, so readers never see a partial thumbnail.
- **Generation.** A POST renders only the widths that are missing. If every requested width is
  cached, the source is not re-read or re-fetched.
- **URL fetching.** Only `http` and `https` are accepted. Redirects are not followed. Unless the
  service is constructed with `allowPrivateHosts = true`, the host must not resolve to a loopback,
  site-local, link-local, or multicast address. The `main` method always runs with this check on.

## Limitations

- No cache eviction or expiry. A URL source that changes upstream keeps its old thumbnails until
  the cache directory is cleared.
- The private-address check resolves the host once before fetching. It does not stop DNS
  rebinding, and it does not cover IPv6 unique-local (`fc00::/7`) addresses. Do not expose the
  service to untrusted clients without a network-level egress restriction.
- No authentication or rate limiting.
- Upscaling is allowed, so a small source can produce a 4096-pixel-wide thumbnail.
- Thumbnails are generated synchronously in the request.

## Tests

`ThumbnailServiceTest` starts the service on an ephemeral port and exercises it over HTTP. It
covers uploads, URL sources with a local origin server, the disk cache, the private-host check,
validation errors, and 404 handling.
