# File Upload Service

A small Spring Boot (Java 17, Maven) service that stores uploaded files on local disk.

## Run

    mvn spring-boot:run
    mvn test

Configuration (`src/main/resources/application.properties`, or override on the command line):

| Property | Default | Meaning |
|---|---|---|
| `app.storage-dir` | `uploads` | Directory for stored files (created if missing) |
| `app.thumbnail-size` | `128` | Max thumbnail width/height in pixels |
| `spring.servlet.multipart.max-file-size` | `50MB` | Upload size limit |

Example: `mvn spring-boot:run -Dspring-boot.run.arguments=--app.storage-dir=/data/files`

## API

| Method & path | Description |
|---|---|
| `POST /files` | Upload (multipart form field `file`). `201` with name/size/time; `409` if the name already exists |
| `GET /files` | List files as JSON: `name`, `size` (bytes), `uploadedAt` (file modification time, ISO-8601) |
| `GET /files/{name}` | Download a file |
| `DELETE /files/{name}` | Delete a file (and its thumbnail). `204` |
| `POST /files/{name}/thumbnail` | Generate a PNG thumbnail of an image and return it. `415` if not an image |
| `GET /files/{name}/thumbnail` | Fetch a previously generated thumbnail (`404` if none) |

    curl -F file=@photo.jpg localhost:8080/files
    curl localhost:8080/files
    curl -X POST localhost:8080/files/photo.jpg/thumbnail -o thumb.png

## Notes

- File names are used as-is; names containing `/`, `\`, or equal to `.`, `..`, or `thumbnails` are rejected with `400` to prevent path traversal.
- Thumbnails are kept in `<storage-dir>/thumbnails` and are not shown in the listing.
- There is no authentication; put the service behind a gateway before exposing it.
