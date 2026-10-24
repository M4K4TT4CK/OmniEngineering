# CSV customer import

A small Flask service that imports customers from an uploaded CSV in the background
and reports progress and per-row errors. Storage is SQLite.

## Files

- `app.py` — the Flask app, validation, and the background import worker.
- `test_import.py` — pytest tests.
- `requirements.txt` — dependencies.

## CSV format

The header row must contain these columns (extra columns are ignored):

| column        | rules                                              |
|---------------|----------------------------------------------------|
| `name`        | required                                           |
| `email`       | required; lowercased and trimmed; the customer key |
| `phone`       | optional; digits, spaces, `-`, `()`, leading `+`  |
| `country`     | required; 2-letter ISO code (e.g. `US`)            |
| `signup_date` | required; `YYYY-MM-DD`                             |

Rows that fail validation are recorded as errors and skipped; the rest of the file is still imported.
If an email appears more than once, the later row updates the earlier one.

## API

### Start an import

```
POST /imports
Content-Type: multipart/form-data   (field name: file)
```

Returns `202` with `{"id": "...", "status": "queued"}`. The file is parsed in a background thread.

### Check progress and errors

```
GET /imports/<id>?limit=100&offset=0
```

Returns the import's `status` (`queued`, `running`, `completed`, `failed`), row counters
(`total_rows`, `processed_rows`, `inserted_rows`, `updated_rows`, `failed_rows`), `fatal_error`
if the whole file was rejected (for example, missing columns), and a page of `errors`
(`row_number`, `field`, `message`) along with `error_count`. `row_number` counts data rows from 1.

## Running

```
pip install -r requirements.txt
flask --app app run --port 8000
```

The database path comes from the `CUSTOMER_DB` environment variable (default `customers.db`).

## Testing

```
pytest
```

## Limitations

- Each import runs in its own thread with no concurrency limit, and the file is held in memory.
- Two imports that touch the same email at the same time can race on the insert-or-update check.
  The upsert itself is atomic, so the database stays consistent, but the inserted/updated split may be off.
- Imports are not resumed after a server restart; an import interrupted mid-run stays `running`.
