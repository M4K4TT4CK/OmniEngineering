# File Upload Service

A small Go HTTP service (standard library only, Go 1.22+) that stores uploaded files on local disk.

## Run

    go run .
    go test ./...

Configuration via environment variables:

| Variable        | Default     | Meaning                         |
|-----------------|-------------|---------------------------------|
| `UPLOAD_DIR`    | `./uploads` | Storage directory (created)     |
| `ADDR`          | `:8080`     | Listen address                  |
| `MAX_UPLOAD_MB` | `100`       | Maximum size of one upload      |

## API

| Method & path                    | Description                                              |
|----------------------------------|----------------------------------------------------------|
| `POST /files`                    | Upload (multipart form, field `file`). 201, 409 if the name exists, 413 if too large |
| `GET /files`                     | JSON list of `{name, size, uploaded_at}`                 |
| `GET /files/{name}`              | Download (always as an attachment)                       |
| `DELETE /files/{name}`           | Delete the file and its thumbnail. 204                   |
| `POST /files/{name}/thumbnail`   | Generate a PNG thumbnail (max 128px) for a PNG/JPEG/GIF. 415 if not an image |
| `GET /files/{name}/thumbnail`    | Fetch the generated thumbnail                            |

Example:

    curl -F file=@photo.jpg localhost:8080/files
    curl localhost:8080/files
    curl -X POST localhost:8080/files/photo.jpg/thumbnail
    curl -o thumb.png localhost:8080/files/photo.jpg/thumbnail
    curl -OJ localhost:8080/files/photo.jpg
    curl -X DELETE localhost:8080/files/photo.jpg

## Notes

- Uploads are streamed to disk; partial files are removed on failure. Existing files are never overwritten.
- Client-supplied names are reduced to their base name; names starting with `.` are rejected. Thumbnails live in `UPLOAD_DIR/.thumbs`.
- `uploaded_at` is the file's modification time.
- There is no authentication; put the service behind a proxy or add auth before exposing it.
