# Customer CSV import

A small Java 17 service that imports customers from CSV into SQLite. It uses the JDK's built-in
HTTP server, `sqlite-jdbc`, and Jackson, with no web framework.

## Running

```sh
mvn test                 # run the tests
mvn compile exec:java    # start on :8080 with ./customers.db
PORT=9000 DB_PATH=/tmp/c.db mvn compile exec:java
```

## API

### `POST /imports`

Upload the CSV as the raw request body, or as `multipart/form-data` with a part named `file`:

```sh
curl -i --data-binary @customers.csv -H 'Content-Type: text/csv' localhost:8080/imports
curl -i -F file=@customers.csv localhost:8080/imports
```

The response is `202 Accepted` with a `Location` header:

```json
{"importId":"6f1c…","status":"QUEUED","statusUrl":"/imports/6f1c…"}
```

Error responses: `400` for an empty upload or a bad multipart body, `413` for uploads over 20 MB,
and `503` (with `Retry-After`) when 10 imports are already waiting in the queue.

### `GET /imports/{id}?errorsOffset=0&errorsLimit=100`

```json
{
  "importId": "6f1c…", "status": "COMPLETED", "filename": "customers.csv",
  "totalRows": 3, "processedRows": 3, "insertedRows": 1, "updatedRows": 0, "failedRows": 2,
  "failureReason": null,
  "createdAt": "…", "startedAt": "…", "finishedAt": "…",
  "errorCount": 2, "errorsOffset": 0, "errorsLimit": 100,
  "errors": [
    {"line": 3, "field": "email", "message": "email is not a valid email address"},
    {"line": 4, "field": null, "message": "expected 5 fields but found 2"}
  ]
}
```

`status` is one of `QUEUED`, `RUNNING`, `COMPLETED`, or `FAILED`. `FAILED` means the whole file
could not be processed, for example because it is not UTF-8 or a header column is missing. The
reason is in `failureReason`. Rows with problems do not fail the import. They are counted in
`failedRows` and listed in `errors` (in file order, paged, `errorsLimit` ≤ 1000). `line` is the
physical line in the file where the record starts. Unknown ids return `404`.

## CSV format and validation

- UTF-8 (a BOM is allowed), RFC 4180 quoting, and LF, CRLF or CR line endings. Blank lines are skipped.
- A header row is required. It must contain `name`, `email`, `phone`, `country` and `signup_date`
  in any order. Header names are case-insensitive and `Signup Date` / `signup-date` also match.
  Extra columns are ignored.
- `name`: required, at most 200 characters.
- `email`: required and must look like an address. It is lowercased and used as the unique key.
- `phone`: optional. If present it must have 7–15 digits and may also contain `+ - . ( )` and spaces.
- `country`: required ISO 3166-1 alpha-2 code (case-insensitive, stored uppercase).
- `signup_date`: required, `YYYY-MM-DD`, and not in the future (UTC).
- A valid row inserts a customer, or overwrites every field of the existing customer with the
  same email. A blank phone clears the stored phone. If an email appears twice in one file, the
  later row wins.

## Design notes

- **Files:** `App` handles HTTP and minimal multipart parsing, `ImportService` handles the job,
  validation and SQLite, and `Csv` is the parser.
- **Background work:** a single worker thread runs imports in order, because SQLite allows only
  one writer at a time. Rows are committed in batches of 500, and progress is updated with each
  batch. That means an import that fails partway keeps the rows it had already committed.
- **Concurrency:** the database uses WAL mode, so status reads don't block the import. The
  worker uses `BEGIN IMMEDIATE` to avoid write-upgrade conflicts.
- **Memory:** uploads are kept in memory until processed. That is bounded by the 20 MB upload
  limit and the 10-deep queue.
- **Restarts:** because uploads are only in memory, imports still `QUEUED` or `RUNNING` when
  the process stops are marked `FAILED` on the next startup.
