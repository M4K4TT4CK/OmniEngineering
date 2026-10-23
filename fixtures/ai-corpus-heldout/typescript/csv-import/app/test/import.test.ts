import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import request from "supertest";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createApp } from "../src/app.js";
import { openDatabase, type DB } from "../src/db.js";
import { Importer, validateRow, type RawRow } from "../src/importer.js";

const HEADER = "name,email,phone,country,signup_date";

let db: DB;
let importer: Importer;
let app: ReturnType<typeof createApp>;

beforeEach(() => {
  db = openDatabase(":memory:");
  importer = new Importer(db);
  app = createApp(importer, { maxFileBytes: 1024 * 1024 });
});

afterEach(() => db.close());

async function runImport(csv: string, query = "") {
  const res = await request(app).post("/imports").attach("file", Buffer.from(csv), "customers.csv");
  expect(res.status).toBe(202);
  expect(res.headers.location).toBe(`/imports/${res.body.id}`);
  await importer.waitFor(res.body.id);
  const status = await request(app).get(`/imports/${res.body.id}${query}`);
  expect(status.status).toBe(200);
  return status.body;
}

function customers() {
  return db.prepare("SELECT name, email, phone, country, signup_date FROM customers ORDER BY email").all();
}

describe("POST /imports + GET /imports/:id", () => {
  it("imports valid rows", async () => {
    const result = await runImport(
      [
        HEADER,
        "Ada Lovelace,ADA@example.com,+44 20 7946 0958,gb,2024-01-15",
        '"Hopper, Grace",grace@example.com,,US,',
      ].join("\r\n") + "\r\n",
    );

    expect(result.status).toBe("completed");
    expect(result.error).toBeNull();
    expect(result.progress).toMatchObject({ percent: 100, rowsProcessed: 2, inserted: 2, updated: 0, failed: 0 });
    expect(result.errors).toMatchObject({ total: 0, items: [] });
    expect(customers()).toEqual([
      { name: "Ada Lovelace", email: "ada@example.com", phone: "+44 20 7946 0958", country: "GB", signup_date: "2024-01-15" },
      { name: "Hopper, Grace", email: "grace@example.com", phone: null, country: "US", signup_date: null },
    ]);
  });

  it("updates existing customers by email, case-insensitively, keeping values for blank cells", async () => {
    await runImport(`${HEADER}\nAda,ada@example.com,5551234567,GB,2024-01-15\n`);
    const result = await runImport(`${HEADER}\nAda Lovelace,Ada@Example.com,,FR,\n`);

    expect(result.progress).toMatchObject({ inserted: 0, updated: 1, failed: 0 });
    expect(customers()).toEqual([
      { name: "Ada Lovelace", email: "ada@example.com", phone: "5551234567", country: "FR", signup_date: "2024-01-15" },
    ]);
  });

  it("records per-row errors with spreadsheet row numbers and still imports the good rows", async () => {
    const result = await runImport(
      [
        HEADER,
        ",not-an-email,123,USA,2024-02-30", // row 2
        "Good,good@example.com,,,", // row 3
        "", // row 4: blank lines are skipped but still counted
        "Short,short@example.com", // row 5
      ].join("\n"),
    );

    expect(result.status).toBe("completed");
    expect(result.progress).toMatchObject({ rowsProcessed: 3, inserted: 1, failed: 2 });
    expect(result.errors.items).toEqual([
      { row: 2, field: "name", message: "is required" },
      { row: 2, field: "email", message: "is not a valid email address" },
      { row: 2, field: "phone", message: expect.stringContaining("7-15 digits") },
      { row: 2, field: "country", message: expect.stringContaining("ISO 3166-1") },
      { row: 2, field: "signup_date", message: expect.stringContaining("YYYY-MM-DD") },
      { row: 5, field: null, message: "expected 5 columns but found 2" },
    ]);
    expect(customers()).toHaveLength(1);
  });

  it("accepts reordered, differently cased headers and extra columns", async () => {
    const result = await runImport("Email,Signup Date,Notes,Country,Name,Phone\nx@example.com,2023-05-01,vip,de,X,\n");
    expect(result.progress).toMatchObject({ inserted: 1, failed: 0 });
    expect(customers()).toEqual([
      { name: "X", email: "x@example.com", phone: null, country: "DE", signup_date: "2023-05-01" },
    ]);
  });

  it("paginates errors", async () => {
    const rows = Array.from({ length: 5 }, (_, i) => `Bad ${i},bad${i},,,`);
    const result = await runImport([HEADER, ...rows].join("\n"), "?errorsLimit=2&errorsOffset=2");
    expect(result.errors.total).toBe(5);
    expect(result.errors.items.map((e: { row: number }) => e.row)).toEqual([4, 5]);

    const bad = await request(app).get(`/imports/${result.id}?errorsLimit=abc`);
    expect(bad.status).toBe(400);
  });

  it("processes files larger than one batch", async () => {
    const rows = Array.from({ length: 1234 }, (_, i) => `Person ${i},p${i}@example.com,,,2020-01-01`);
    const result = await runImport([HEADER, ...rows].join("\n"));
    expect(result.progress).toMatchObject({ rowsProcessed: 1234, inserted: 1234, percent: 100 });
  });

  it("fails the import when required columns are missing", async () => {
    const result = await runImport("name,email\nA,a@example.com\n");
    expect(result.status).toBe("failed");
    expect(result.error).toBe("missing required column(s): phone, country, signup_date");
    expect(customers()).toEqual([]);
  });

  it("fails the import on malformed CSV", async () => {
    const result = await runImport(`${HEADER}\nA,a@example.com,,,\n"B,b@example.com,,,\n`);
    expect(result.status).toBe("failed");
    expect(result.error).toMatch(/^invalid CSV: /);
    expect(result.finishedAt).not.toBeNull();
  });

  it("rejects requests without a file, empty files and oversized files", async () => {
    expect((await request(app).post("/imports")).status).toBe(400);
    expect((await request(app).post("/imports").attach("file", Buffer.alloc(0), "e.csv")).status).toBe(400);
    expect((await request(app).post("/imports").attach("other", Buffer.from(HEADER), "x.csv")).status).toBe(400);
    const big = Buffer.alloc(1024 * 1024 + 1, "a");
    expect((await request(app).post("/imports").attach("file", big, "big.csv")).status).toBe(413);
  });

  it("returns 404 for unknown imports", async () => {
    expect((await request(app).get("/imports/does-not-exist")).status).toBe(404);
  });

  it("reports pending status before the background job runs", async () => {
    const res = await request(app).post("/imports").attach("file", Buffer.from(`${HEADER}\n`), "c.csv");
    const status = importer.getStatus(res.body.id);
    expect(status?.status).toMatch(/pending|running|completed/);
    await importer.waitFor(res.body.id);
    expect(importer.getStatus(res.body.id)?.status).toBe("completed");
  });
});

describe("restart recovery", () => {
  it("marks imports left running by a previous process as failed", () => {
    const dir = mkdtempSync(join(tmpdir(), "csv-import-"));
    try {
      const path = join(dir, "test.db");
      const first = openDatabase(path);
      first
        .prepare("INSERT INTO imports (id, status, file_size, created_at) VALUES ('x', 'running', 10, ?)")
        .run(new Date().toISOString());
      first.close();

      const second = openDatabase(path);
      expect(new Importer(second).getStatus("x")).toMatchObject({
        status: "failed",
        error: "interrupted by server restart",
      });
      second.close();
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe("validateRow", () => {
  const row = (overrides: Partial<RawRow>): RawRow => ({
    name: "A",
    email: "a@example.com",
    phone: "",
    country: "",
    signup_date: "",
    ...overrides,
  });
  const now = new Date("2025-06-15T12:00:00Z");

  it("accepts real calendar dates up to one day ahead", () => {
    expect(validateRow(row({ signup_date: "2024-02-29" }), now).ok).toBe(true);
    expect(validateRow(row({ signup_date: "2025-06-16" }), now).ok).toBe(true);
  });

  it.each(["2023-02-29", "2024-13-01", "15/06/2024", "1899-12-31"])("rejects invalid date %s", (d) => {
    expect(validateRow(row({ signup_date: d }), now)).toMatchObject({ ok: false, errors: [{ field: "signup_date" }] });
  });

  it("rejects future dates", () => {
    expect(validateRow(row({ signup_date: "2025-06-17" }), now)).toMatchObject({
      ok: false,
      errors: [{ field: "signup_date", message: "is in the future" }],
    });
  });

  it("normalizes email and country, trims values", () => {
    expect(validateRow(row({ name: "  Bo ", email: " Bo@Example.COM ", country: " se " }), now)).toEqual({
      ok: true,
      customer: { name: "Bo", email: "bo@example.com", phone: null, country: "SE", signupDate: null },
    });
  });

  it.each(["123", "abc-defg-hij", "+1 (555) 123-4567 ext 9", "1234567890123456"])("rejects phone %s", (p) => {
    expect(validateRow(row({ phone: p }), now).ok).toBe(false);
  });
});
