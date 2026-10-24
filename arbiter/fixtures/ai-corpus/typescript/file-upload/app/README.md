# File Upload Service

A small Express + TypeScript service that stores uploaded files on local disk and can generate image thumbnails (via `sharp`).

## Setup

```sh
npm install
npm run dev        # run with tsx
# or
npm run build && npm start
npm test           # vitest + supertest
```

## Configuration (environment variables)

| Variable        | Default     | Description                          |
| --------------- | ----------- | ------------------------------------ |
| `UPLOAD_DIR`    | `./uploads` | Directory where files are stored     |
| `PORT`          | `3000`      | HTTP port                            |
| `MAX_FILE_SIZE` | 50 MiB      | Maximum upload size in bytes         |

## Endpoints

| Method & path                | Description                                                                 |
| ---------------------------- | --------------------------------------------------------------------------- |
| `POST /files`                | Multipart upload, field `file`. `201` with `{name, size}`; `409` if the name exists; `413` if too large |
| `GET /files`                 | List files: `[{name, size, uploadedAt}]`                                    |
| `GET /files/:name`           | Download a file                                                             |
| `DELETE /files/:name`        | Delete a file (and its thumbnail); `204`                                    |
| `POST /files/:name/thumbnail`| Generate a PNG thumbnail (fits in 200×200); `415` if not an image          |
| `GET /files/:name/thumbnail` | Fetch the generated thumbnail; `404` if not generated yet                   |

```sh
curl -F file=@photo.jpg localhost:3000/files
curl localhost:3000/files
curl -X POST localhost:3000/files/photo.jpg/thumbnail
curl -o thumb.png localhost:3000/files/photo.jpg/thumbnail
```

## Notes

- Files are stored under their uploaded name. Names with path separators or a leading `.` are rejected (prevents path traversal), and existing files are never overwritten.
- Thumbnails live in `<UPLOAD_DIR>/.thumbnails/` and are excluded from listings.
- "Upload time" is the file's creation time on disk, falling back to modification time where unsupported.
- No authentication is included; put the service behind a gateway before exposing it.
