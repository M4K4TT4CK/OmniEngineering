import Database from "better-sqlite3";

export type DB = Database.Database;

export function openDatabase(path = ":memory:"): DB {
  const db = new Database(path);
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");

  db.exec(`
    CREATE TABLE IF NOT EXISTS customers (
      id          INTEGER PRIMARY KEY,
      email       TEXT NOT NULL UNIQUE,
      name        TEXT NOT NULL,
      phone       TEXT,
      country     TEXT,
      signup_date TEXT,
      created_at  TEXT NOT NULL,
      updated_at  TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS imports (
      id               TEXT PRIMARY KEY,
      status           TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
      filename         TEXT,
      file_size        INTEGER NOT NULL,
      bytes_processed  INTEGER NOT NULL DEFAULT 0,
      processed_rows   INTEGER NOT NULL DEFAULT 0,
      inserted_rows    INTEGER NOT NULL DEFAULT 0,
      updated_rows     INTEGER NOT NULL DEFAULT 0,
      failed_rows      INTEGER NOT NULL DEFAULT 0,
      errors_truncated INTEGER NOT NULL DEFAULT 0,
      error            TEXT,
      created_at       TEXT NOT NULL,
      started_at       TEXT,
      finished_at      TEXT
    );

    CREATE TABLE IF NOT EXISTS import_errors (
      id         INTEGER PRIMARY KEY,
      import_id  TEXT NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
      row_number INTEGER NOT NULL,
      field      TEXT,
      message    TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS import_errors_by_import ON import_errors (import_id, id);
  `);

  // Jobs run in-process, so any import still pending/running from a previous
  // process can never finish. Mark it failed rather than leaving it "running" forever.
  db.prepare(
    `UPDATE imports SET status = 'failed', error = 'interrupted by server restart', finished_at = ?
     WHERE status IN ('pending', 'running')`,
  ).run(new Date().toISOString());

  return db;
}
