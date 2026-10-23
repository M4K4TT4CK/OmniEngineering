import { randomUUID } from "node:crypto";
import { Readable } from "node:stream";
import { CsvError, parse } from "csv-parse";
import type { DB } from "./db.js";

export const COLUMNS = ["name", "email", "phone", "country", "signup_date"] as const;
export type Column = (typeof COLUMNS)[number];
export type RawRow = Record<Column, string>;

export interface Customer {
  name: string;
  email: string;
  phone: string | null;
  country: string | null;
  signupDate: string | null;
}

export interface FieldError {
  field: Column | null;
  message: string;
}

export type ValidationResult = { ok: true; customer: Customer } | { ok: false; errors: FieldError[] };

const BATCH_SIZE = 500;
const CHUNK_BYTES = 64 * 1024;
const MAX_STORED_ERRORS = 1000;

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PHONE_RE = /^\+?[0-9 ().\/-]+$/;
const COUNTRY_RE = /^[A-Z]{2}$/;
const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

export function validateRow(raw: RawRow, now = new Date()): ValidationResult {
  const errors: FieldError[] = [];

  const name = raw.name.trim();
  if (!name) errors.push({ field: "name", message: "is required" });
  else if (name.length > 200) errors.push({ field: "name", message: "must be at most 200 characters" });

  const email = raw.email.trim().toLowerCase();
  if (!email) errors.push({ field: "email", message: "is required" });
  else if (email.length > 254 || !EMAIL_RE.test(email)) errors.push({ field: "email", message: "is not a valid email address" });

  const phone = raw.phone.trim();
  if (phone) {
    const digits = phone.replace(/\D/g, "").length;
    if (!PHONE_RE.test(phone) || digits < 7 || digits > 15) {
      errors.push({ field: "phone", message: "must contain 7-15 digits and only + ( ) . / - or spaces" });
    }
  }

  const country = raw.country.trim().toUpperCase();
  if (country && !COUNTRY_RE.test(country)) {
    errors.push({ field: "country", message: "must be a 2-letter ISO 3166-1 country code" });
  }

  const signupDate = raw.signup_date.trim();
  if (signupDate) {
    // One day of slack so customers in timezones ahead of UTC aren't rejected.
    const latest = new Date(now.getTime() + 86_400_000).toISOString().slice(0, 10);
    if (!isCalendarDate(signupDate)) errors.push({ field: "signup_date", message: "must be a valid date in YYYY-MM-DD format" });
    else if (signupDate > latest) errors.push({ field: "signup_date", message: "is in the future" });
  }

  if (errors.length > 0) return { ok: false, errors };
  return {
    ok: true,
    customer: { name, email, phone: phone || null, country: country || null, signupDate: signupDate || null },
  };
}

function isCalendarDate(value: string): boolean {
  const m = DATE_RE.exec(value);
  if (!m) return false;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  if (y < 1900) return false;
  const date = new Date(Date.UTC(y, mo - 1, d));
  return date.getUTCFullYear() === y && date.getUTCMonth() === mo - 1 && date.getUTCDate() === d;
}

/** Accepts e.g. "Signup Date", "signup-date" and "signup_date" for the same column. */
function normalizeHeader(header: string): string {
  return header.trim().toLowerCase().replace(/[\s-]+/g, "_");
}

// ---------------------------------------------------------------------------
// Import jobs
// ---------------------------------------------------------------------------

/** A problem with the file itself; the message is shown to the API caller. */
export class ImportError extends Error {}

interface ParsedRow {
  row: number;
  values: string[];
}

interface Job {
  id: string;
  columns: Record<Column, number> | null;
  columnCount: number;
  processed: number;
  inserted: number;
  updated: number;
  failed: number;
  storedErrors: number;
  errorsTruncated: boolean;
}

interface ImportRecord {
  id: string;
  status: "pending" | "running" | "completed" | "failed";
  filename: string | null;
  file_size: number;
  bytes_processed: number;
  processed_rows: number;
  inserted_rows: number;
  updated_rows: number;
  failed_rows: number;
  errors_truncated: number;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

function prepareStatements(db: DB) {
  return {
    createImport: db.prepare(
      `INSERT INTO imports (id, status, filename, file_size, created_at) VALUES (?, 'pending', ?, ?, ?)`,
    ),
    markRunning: db.prepare(`UPDATE imports SET status = 'running', started_at = ? WHERE id = ?`),
    progress: db.prepare(
      `UPDATE imports SET processed_rows = ?, inserted_rows = ?, updated_rows = ?, failed_rows = ?,
         errors_truncated = ?, bytes_processed = ? WHERE id = ?`,
    ),
    finish: db.prepare(`UPDATE imports SET status = ?, error = ?, finished_at = ? WHERE id = ?`),
    findCustomer: db.prepare(`SELECT id FROM customers WHERE email = ?`),
    insertCustomer: db.prepare(
      `INSERT INTO customers (name, email, phone, country, signup_date, created_at, updated_at)
       VALUES (@name, @email, @phone, @country, @signupDate, @now, @now)`,
    ),
    // Blank optional cells keep the existing value instead of erasing it.
    updateCustomer: db.prepare(
      `UPDATE customers SET name = @name, phone = COALESCE(@phone, phone), country = COALESCE(@country, country),
         signup_date = COALESCE(@signupDate, signup_date), updated_at = @now
       WHERE id = @id`,
    ),
    insertError: db.prepare(`INSERT INTO import_errors (import_id, row_number, field, message) VALUES (?, ?, ?, ?)`),
    getImport: db.prepare(`SELECT * FROM imports WHERE id = ?`),
    countErrors: db.prepare(`SELECT COUNT(*) AS n FROM import_errors WHERE import_id = ?`),
    listErrors: db.prepare(
      `SELECT row_number AS row, field, message FROM import_errors WHERE import_id = ? ORDER BY id LIMIT ? OFFSET ?`,
    ),
  };
}

export class Importer {
  private readonly stmts: ReturnType<typeof prepareStatements>;
  private readonly running = new Map<string, Promise<void>>();

  constructor(private readonly db: DB) {
    this.stmts = prepareStatements(db);
  }

  /** Records the import and schedules it to run in the background. Returns the import id. */
  start(file: Buffer, filename: string | null): string {
    const id = randomUUID();
    this.stmts.createImport.run(id, filename?.slice(0, 255) ?? null, file.length, new Date().toISOString());

    const job = new Promise<void>((resolve) => setImmediate(resolve))
      .then(() => this.run(id, file))
      .finally(() => this.running.delete(id));
    this.running.set(id, job);
    return id;
  }

  /** Resolves once the given import has finished (immediately if it isn't running). */
  async waitFor(id: string): Promise<void> {
    await this.running.get(id);
  }

  getStatus(id: string, errorsLimit = 100, errorsOffset = 0) {
    const rec = this.stmts.getImport.get(id) as ImportRecord | undefined;
    if (!rec) return undefined;

    const { n } = this.stmts.countErrors.get(id) as { n: number };
    const items = this.stmts.listErrors.all(id, errorsLimit, errorsOffset) as {
      row: number;
      field: string | null;
      message: string;
    }[];

    return {
      id: rec.id,
      status: rec.status,
      filename: rec.filename,
      error: rec.error,
      createdAt: rec.created_at,
      startedAt: rec.started_at,
      finishedAt: rec.finished_at,
      progress: {
        percent: rec.file_size > 0 ? Math.floor((rec.bytes_processed / rec.file_size) * 100) : 100,
        bytesProcessed: rec.bytes_processed,
        totalBytes: rec.file_size,
        rowsProcessed: rec.processed_rows,
        inserted: rec.inserted_rows,
        updated: rec.updated_rows,
        failed: rec.failed_rows,
      },
      errors: {
        total: n,
        // True when more rows failed than we keep error details for.
        truncated: rec.errors_truncated === 1,
        limit: errorsLimit,
        offset: errorsOffset,
        items,
      },
    };
  }

  private async run(id: string, file: Buffer): Promise<void> {
    try {
      this.stmts.markRunning.run(new Date().toISOString(), id);
      await this.process(id, file);
      this.stmts.finish.run("completed", null, new Date().toISOString(), id);
    } catch (err) {
      let message: string;
      if (err instanceof ImportError) message = err.message;
      else if (err instanceof CsvError) message = `invalid CSV: ${err.message}`;
      else {
        console.error(`import ${id} failed`, err);
        message = "internal error while importing";
      }
      try {
        this.stmts.finish.run("failed", message, new Date().toISOString(), id);
      } catch (finishErr) {
        console.error(`could not record failure of import ${id}`, finishErr);
      }
    }
  }

  private async process(id: string, file: Buffer): Promise<void> {
    const job: Job = {
      id,
      columns: null,
      columnCount: 0,
      processed: 0,
      inserted: 0,
      updated: 0,
      failed: 0,
      storedErrors: 0,
      errorsTruncated: false,
    };

    // Feed the parser in chunks so parsing is incremental and byte progress is meaningful.
    const parser = Readable.from(chunks(file, CHUNK_BYTES)).pipe(
      parse({ bom: true, info: true, relax_column_count: true }),
    );

    let pending: ParsedRow[] = [];
    let bytes = 0;
    // Counts every record including the header and blank lines, so it matches
    // the row number a spreadsheet shows (header = row 1).
    let row = 0;

    try {
      for await (const { record, info } of parser as AsyncIterable<{ record: string[]; info: { bytes: number } }>) {
        row++;
        bytes = info.bytes;
        if (!job.columns) {
          job.columns = mapHeader(record);
          job.columnCount = record.length;
          continue;
        }
        pending.push({ row, values: record });
        if (pending.length >= BATCH_SIZE) {
          const batch = pending;
          pending = [];
          this.commit(job, batch, bytes);
          // Give HTTP requests (e.g. status polls) a chance to run between batches.
          await new Promise((resolve) => setImmediate(resolve));
        }
      }
    } catch (err) {
      // Rows received before a syntax error are complete records; keep them.
      if (err instanceof CsvError) this.commit(job, pending, bytes);
      throw err;
    }

    if (!job.columns) throw new ImportError("file is empty; expected a header row");
    this.commit(job, pending, file.length);
  }

  /** Validates and upserts one batch of rows in a single transaction, then records progress. */
  private commit(job: Job, batch: ParsedRow[], bytes: number): void {
    const columns = job.columns;
    if (!columns) return;

    this.db.transaction(() => {
      const now = new Date().toISOString();
      for (const { row, values } of batch) {
        if (values.every((v) => v.trim() === "")) continue;
        job.processed++;

        let errors: FieldError[];
        if (values.length !== job.columnCount) {
          errors = [{ field: null, message: `expected ${job.columnCount} columns but found ${values.length}` }];
        } else {
          const raw = Object.fromEntries(COLUMNS.map((c) => [c, values[columns[c]] ?? ""])) as RawRow;
          const result = validateRow(raw);
          if (result.ok) {
            const c = result.customer;
            const existing = this.stmts.findCustomer.get(c.email) as { id: number } | undefined;
            const params = { name: c.name, phone: c.phone, country: c.country, signupDate: c.signupDate, now };
            if (existing) {
              this.stmts.updateCustomer.run({ ...params, id: existing.id });
              job.updated++;
            } else {
              this.stmts.insertCustomer.run({ ...params, email: c.email });
              job.inserted++;
            }
            continue;
          }
          errors = result.errors;
        }

        job.failed++;
        for (const e of errors) {
          if (job.storedErrors >= MAX_STORED_ERRORS) {
            job.errorsTruncated = true;
            break;
          }
          this.stmts.insertError.run(job.id, row, e.field, e.message);
          job.storedErrors++;
        }
      }

      this.stmts.progress.run(
        job.processed,
        job.inserted,
        job.updated,
        job.failed,
        job.errorsTruncated ? 1 : 0,
        bytes,
        job.id,
      );
    })();
  }
}

function mapHeader(record: string[]): Record<Column, number> {
  const headers = record.map(normalizeHeader);
  const missing: string[] = [];
  const columns = {} as Record<Column, number>;
  for (const c of COLUMNS) {
    const first = headers.indexOf(c);
    if (first === -1) missing.push(c);
    else if (headers.indexOf(c, first + 1) !== -1) throw new ImportError(`duplicate column: ${c}`);
    else columns[c] = first;
  }
  if (missing.length > 0) throw new ImportError(`missing required column(s): ${missing.join(", ")}`);
  return columns;
}

function* chunks(buf: Buffer, size: number): Generator<Buffer> {
  for (let i = 0; i < buf.length; i += size) yield buf.subarray(i, i + size);
}
