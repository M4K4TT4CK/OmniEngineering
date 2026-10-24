# File Upload Service

A small Sinatra service for uploading, listing, downloading, deleting files and generating image thumbnails. Files are stored on local disk.

## Requirements

- Ruby 3.1+
- ImageMagick (`convert` or `magick` on PATH), needed only for thumbnails

## Setup and run

    bundle install
    bundle exec rackup -p 9292

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `UPLOAD_DIR` | `./uploads` | Storage directory (created if missing) |
| `MAX_UPLOAD_BYTES` | `26214400` (25 MB) | Maximum upload size |
| `THUMBNAIL_SIZE` | `200x200` | Max thumbnail bounding box (aspect ratio kept, never upscaled) |

## API

| Method & path | Description |
|---|---|
| `POST /files` | Multipart upload, field `file`. `201` with file info; `409` if the name exists; `413` if too large; `400` for invalid names |
| `GET /files` | JSON list: `name`, `size` (bytes), `uploaded_at` (ISO 8601 UTC), `thumbnail` (bool) |
| `GET /files/:name` | Download as attachment; `404` if missing |
| `DELETE /files/:name` | Delete file and its thumbnail; `204` |
| `POST /files/:name/thumbnail` | Generate a PNG thumbnail for an image; `201`, or `415` for non-images, `422` for unreadable images |
| `GET /files/:name/thumbnail` | Fetch the generated thumbnail; `404` if not generated |

Example:

    curl -F file=@photo.jpg localhost:9292/files
    curl -X POST localhost:9292/files/photo.jpg/thumbnail
    curl -o thumb.png localhost:9292/files/photo.jpg/thumbnail

## Notes

- File names must match `[A-Za-z0-9_][A-Za-z0-9_. -]*` (max 255 chars, no `..`). Any directory part of an uploaded filename is stripped.
- Upload time is the file's modification time on disk.
- Thumbnails are stored in `UPLOAD_DIR/.thumbnails`.
- There is no authentication; put the service behind a proxy or add auth before exposing it.

## Tests

    bundle exec ruby test_app.rb

The thumbnail lifecycle test is skipped if ImageMagick isn't installed.
