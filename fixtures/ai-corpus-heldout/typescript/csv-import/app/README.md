# Customer CSV import

A small Express + SQLite service that imports customers from an uploaded CSV in the background.

## Files

- `src/db.ts` – opens SQLite (better-sqlite3) and creates the schema
- `src/importer.ts` – row validation and the background import job
- `src/app.ts` – HTTP endpoints
- `src/server.ts` – entry point
- `test/import.test.ts` – tests (Vitest + Supertest)

## Running

```sh
npm install
npm test
npm run dev                      # or: npm run build && npm start
```

Environment variables: `PORT` (default `3000`), `DB_PATH` (default `customers.db`),
`MAX_FILE_BYTES` (default 20 MiB).

## API

### `POST /imports`

Upload the CSV as `multipart/form-data` in the field `file`. Returns `202 Accepted`:

```sh
curl -F file=@customers.csv http://localhost:3000/imports
# {"id":"6f1c…","status":"pending","statusUrl":"/imports/6f1c…"}
```

`400` if the file is missing or empty, `413` if it is too large.

### `GET /imports/:id`

Progress and per-row errors. Errors are paginated with `?errorsLimit=` (default 100, max 1000)
and `?errorsOffset=`.

```json
{
  "id": "6f1c…",
  "status": "completed",
  "error": null,
  "progress": { "percent": 100, "bytesProcessed": 5120, "totalBytes": 5120,
                "rowsProcessed": 120, "inserted": 100, "updated": 18, "failed": 2 },
  "errors": { "total": 2, "truncated": false, "limit": 100, "offset": 0,
              "items": [{ "row": 7, "field": "email", "message": "is not a valid email address" }] }
}
```

`status` is `pending`, `running`, `completed` or `failed`. `failed` means the file as a whole
could not be processed (missing columns, malformed CSV, server restart); `error` explains why.
Invalid rows don't fail the import; they're listed in `errors`.

## CSV format

The header row must contain `name`, `email`, `phone`, `country` and `signup_date`, in any order.
Matching ignores case, and spaces or hyphens count as underscores (`Signup Date` works). Extra
columns are ignored.

| Column        | Rule                                                              |
|---------------|-------------------------------------------------------------------|
| `name`        | required, max 200 characters                                      |
| `email`       | required, valid address; lower-cased and used as the unique key   |
| `phone`       | optional; 7–15 digits, may contain `+ ( ) . / -` and spaces       |
| `country`     | optional; ISO 3166-1 alpha-2 code (`us` is stored as `US`)        |
| `signup_date` | optional; `YYYY-MM-DD`, a real date from 1900 on, not in the future |

## Behaviour

- **Upsert by email.** A new email inserts a customer; an existing one (case-insensitive) is
  updated. Blank optional cells **keep** the stored value, so a partial file can't erase data.
  If an email appears twice in one file, the later row wins.
- **Row numbers** match spreadsheet rows: the header is row 1 and blank lines count but are
  skipped.
- **Batches.** Rows are committed in transactions of 500 and progress is saved after each batch,
  so `GET` shows live progress. If the import fails partway through, rows already committed stay
  committed.
- **Error cap.** At most 1,000 error entries are kept per import. Past that, `errors.truncated` is
  `true`; `progress.failed` still counts every failed row.
- **Restarts.** Jobs run in-process. On startup, any import still marked `pending`/`running` is
  marked `failed` with the error `interrupted by server restart`.
- The upload is held in memory, so keep `MAX_FILE_BYTES` modest. For very large files or
  multiple instances, use a disk/object store and a real job queue.
