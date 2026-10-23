# CSV customer import

A small Sinatra service that imports customers from an uploaded CSV file in the background. Rows are upserted into SQLite by email, and bad rows are recorded with their line numbers while the rest of the file is still imported.

## Files

- `app.rb` — HTTP endpoints (Sinatra).
- `importer.rb` — schema, row validation, and the import job.
- `import_test.rb` — Minitest tests for the importer and the HTTP API.
- `Gemfile` — dependencies.

## Setup

Requires Ruby 3.1+.

```sh
bundle install
```

## Run

```sh
ruby app.rb                       # listens on port 4567
CSV_IMPORT_DB=/var/lib/import.sqlite3 CSV_IMPORT_UPLOADS=/var/lib/uploads ruby app.rb
```

Environment variables:

- `CSV_IMPORT_DB` — SQLite database path (default `customers.sqlite3`).
- `CSV_IMPORT_UPLOADS` — directory where uploaded CSVs are kept (default `uploads`).

## API

Start an import. The file goes in multipart field `file`:

```sh
curl -F "file=@customers.csv" http://localhost:4567/imports
# 202 {"id": "3f1c…", "status_url": "/imports/3f1c…"}
```

Check progress and errors:

```sh
curl "http://localhost:4567/imports/3f1c…"
curl "http://localhost:4567/imports/3f1c…?limit=50&offset=50"
```

The response contains `status` (`queued`, `running`, `completed`, or `failed`), `total_rows`, `processed_rows`, `inserted`, `updated`, `failed`, `fatal_error`, `created_at`, `finished_at`, `errors_total`, and `errors` (a page of `{line, message}` objects). `line` is the physical line in the file, so the header is line 1. `limit` defaults to 100 and is capped at 1000.

## CSV format

Required header: `name,email,phone,country,signup_date`

| Column        | Rule                                              |
|---------------|---------------------------------------------------|
| `name`        | required                                          |
| `email`       | required; trimmed and lowercased; the upsert key  |
| `phone`       | optional; digits, spaces, `()-.`, and a leading `+` |
| `country`     | required; 2-letter code, uppercased               |
| `signup_date` | required; `YYYY-MM-DD` and a real calendar date   |

A row that fails any rule is skipped and recorded in `errors`. A valid row inserts a new customer or updates the existing one with the same email. If the file is missing a required column or is not parseable CSV, the whole import is marked `failed` with `fatal_error` set.

## Tests

```sh
bundle exec ruby import_test.rb
```

## Limitations

- Imports run in threads inside the web process. A restart loses in-flight jobs, and the import stays `running` in the database. A real deployment would move this to a queue and a worker.
- The whole CSV is read into memory before processing.
- There is no authentication, and uploads are not size-limited.
- Two rows with the same email in one file are applied in order, so the later row wins.
