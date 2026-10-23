# CSV customer import

A small Go service that imports customers from a CSV file into SQLite in the background.

- `POST /imports` accepts a multipart upload in the `file` field and returns `202` with `{"import_id": N}`.
- `GET /imports/{id}` reports progress: status, total/processed/inserted/updated/failed row counts, and any fatal error.
- `GET /imports/{id}/errors?limit=100&offset=0` returns per-row errors, ordered by row. `limit` defaults to 100 and is capped at 1000.

## File format

The first row must be a header. These five columns are required, in any order, case-insensitive:

| column        | rules                                                       |
|---------------|-------------------------------------------------------------|
| `name`        | required, max 200 characters                                |
| `email`       | required, valid address; stored lowercased; the match key   |
| `phone`       | optional; digits, spaces, `+ - ( )`; max 32 characters      |
| `country`     | required; 2-letter ISO 3166 code; stored uppercased         |
| `signup_date` | required; `YYYY-MM-DD`                                      |

Rows that fail validation are skipped and recorded in the error list. Their row numbers start at 1 for the first data row, so the header is not counted. A missing required column, an empty file, or a database failure stops the import and sets `fatal_error`.

If the email already exists, the existing customer is updated. Otherwise a new customer is inserted.

## Running

```sh
go mod tidy          # resolves dependencies and writes go.sum
go run . -addr :8080 -db customers.db

curl -F file=@customers.csv localhost:8080/imports
curl localhost:8080/imports/1
curl 'localhost:8080/imports/1/errors?limit=50'
```

## Testing

```sh
go test ./...
```

The tests cover inserts and updates by email, row-level validation errors, error pagination, missing columns, and the HTTP endpoints.

## Design notes

- The SQLite driver is `modernc.org/sqlite`, which is pure Go, so no C toolchain is needed.
- The database connection pool is limited to one connection, because SQLite allows only one writer at a time.
- Each row is written in its own transaction. Progress is saved after every row, so polling shows live counts. A crash mid-import leaves the import in `running` with no recovery. Restarting the import would reprocess the file, and the upsert-by-email makes that safe.
- Uploads are capped at 64 MiB and written to a temp file, so the import can run after the request returns. The temp file is removed when the import ends.
- Imports are not persisted across restarts or queued. Each import runs in its own goroutine.
