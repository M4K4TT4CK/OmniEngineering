# File Upload Service

A small Flask service that stores uploaded files on local disk. Thumbnails use Pillow.

## Setup and run

```
pip install -r requirements.txt
UPLOAD_DIR=./uploads python app.py
```

Configuration (environment variables):

| Variable           | Default        | Meaning                          |
|--------------------|----------------|----------------------------------|
| `UPLOAD_DIR`       | `uploads`      | Directory where files are stored |
| `MAX_UPLOAD_BYTES` | `52428800`     | Maximum request size (50 MB)     |
| `HOST` / `PORT`    | `127.0.0.1` / `5000` | Bind address               |

## Endpoints

| Method & path                      | Description |
|------------------------------------|-------------|
| `POST /files`                      | Multipart upload, field `file`. Returns 201 with name and size; 409 if the name exists unless `?overwrite=true`. |
| `GET /files`                       | JSON list of `{name, size, uploaded_at}` (size in bytes, ISO-8601 UTC time). |
| `GET /files/<name>`                | Download the file. 404 if missing. |
| `DELETE /files/<name>`             | Delete the file and its thumbnails. 204 on success. |
| `GET /files/<name>/thumbnail`      | PNG thumbnail, `?size=` max edge in pixels (default 128, max 1024). Cached on disk. 415 if not an image. |

Example:

```
curl -F file=@photo.jpg localhost:5000/files
curl localhost:5000/files
curl -o thumb.png "localhost:5000/files/photo.jpg/thumbnail?size=200"
```

## Notes

- Uploaded names are sanitised with `secure_filename` (so `../x` becomes `x`); the stored name is returned in the response.
- Thumbnails are cached under `<UPLOAD_DIR>/.thumbnails/` and are removed when the file is deleted or overwritten.
- There is no authentication; put it behind a proxy or add auth before exposing it.
- Uploaded time is the file's modification time.

## Tests

```
pytest
```
