import request from "supertest";
import { beforeEach, describe, expect, it } from "vitest";
import { createApp, csvCell } from "../src/app.js";
import { createAdminKey, type DB, openDb } from "../src/db.js";

let db: DB;
let app: ReturnType<typeof createApp>;
let key: string;

function seed(db: DB) {
  db.exec(`
    INSERT INTO customers (id, name, email) VALUES
      (1, 'Alice', 'alice@example.com'),
      (2, '=HYPERLINK("http://evil")', 'bob@example.com'),
      (3, 'Smith, Carol', 'carol@example.com');
    INSERT INTO products (id, name, sku, price_cents) VALUES
      (1, 'Widget', 'W-1', 1000),
      (2, 'Gadget', 'G-1', 2500),
      (3, 'Doohickey', 'D-1', 500);
    INSERT INTO orders (id, customer_id, status, created_at) VALUES
      (1, 1, 'paid',      '2024-01-01T10:00:00.000Z'),
      (2, 2, 'shipped',   '2024-01-02T12:00:00.000Z'),
      (3, 1, 'cancelled', '2024-01-02T13:00:00.000Z'),
      (4, 3, 'delivered', '2024-01-08T09:00:00.000Z'),
      (5, 1, 'paid',      '2024-02-15T23:59:59.000Z'),
      (6, 2, 'pending',   '2024-02-20T00:00:00.000Z');
    INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents) VALUES
      (1, 1, 2, 1000),
      (2, 2, 1, 2500),
      (3, 2, 4, 2500),
      (4, 3, 3, 500),
      (4, 1, 1, 1000),
      (5, 2, 2, 2500),
      (6, 1, 1, 1000);
  `);
}

beforeEach(() => {
  db = openDb(":memory:");
  seed(db);
  key = createAdminKey(db, "test-admin");
  app = createApp(db);
});

const get = (path: string) => request(app).get(path).set("X-API-Key", key);

describe("authentication", () => {
  it("allows the health check without a key", async () => {
    await request(app).get("/health").expect(200, { status: "ok" });
  });

  it("rejects requests without an API key", async () => {
    const res = await request(app).get("/reports/top-products");
    expect(res.status).toBe(401);
  });

  it("rejects an unknown API key", async () => {
    const res = await request(app).get("/reports/top-products").set("X-API-Key", "adm_wrong");
    expect(res.status).toBe(401);
  });

  it("rejects a deactivated admin", async () => {
    db.prepare("UPDATE admin_users SET active = 0").run();
    const res = await get("/reports/top-products");
    expect(res.status).toBe(401);
  });

  it("does not store the plaintext key", () => {
    const stored = db.prepare("SELECT api_key_hash FROM admin_users").pluck().get();
    expect(stored).not.toBe(key);
    expect(stored).toMatch(/^[0-9a-f]{64}$/);
  });
});

describe("GET /reports/sales", () => {
  it("groups by day and excludes non-revenue statuses", async () => {
    const res = await get("/reports/sales?from=2024-01-01&to=2024-02-29&groupBy=day").expect(200);
    expect(res.body.periods).toEqual([
      { period: "2024-01-01", orders: 1, units: 2, revenue_cents: 2000 },
      { period: "2024-01-02", orders: 1, units: 1, revenue_cents: 2500 },
      { period: "2024-01-08", orders: 1, units: 4, revenue_cents: 2500 },
      { period: "2024-02-15", orders: 1, units: 2, revenue_cents: 5000 },
    ]);
    expect(res.body.totals).toEqual({ orders: 4, units: 9, revenue_cents: 12000 });
  });

  it("groups by ISO week (Monday start)", async () => {
    const res = await get("/reports/sales?from=2024-01-01&to=2024-02-29&groupBy=week").expect(200);
    expect(res.body.periods.map((p: { period: string; revenue_cents: number }) => [p.period, p.revenue_cents])).toEqual([
      ["2024-01-01", 4500],
      ["2024-01-08", 2500],
      ["2024-02-12", 5000],
    ]);
  });

  it("groups by month", async () => {
    const res = await get("/reports/sales?from=2024-01-01&to=2024-02-29&groupBy=month").expect(200);
    expect(res.body.periods).toEqual([
      { period: "2024-01", orders: 3, units: 7, revenue_cents: 7000 },
      { period: "2024-02", orders: 1, units: 2, revenue_cents: 5000 },
    ]);
  });

  it("treats 'to' as inclusive of the whole day", async () => {
    const res = await get("/reports/sales?from=2024-02-15&to=2024-02-15").expect(200);
    expect(res.body.totals.revenue_cents).toBe(5000);
  });

  it.each([
    ["missing range", "/reports/sales"],
    ["bad groupBy", "/reports/sales?from=2024-01-01&to=2024-01-31&groupBy=year"],
    ["impossible date", "/reports/sales?from=2024-02-30&to=2024-03-01"],
    ["badly formatted date", "/reports/sales?from=01/01/2024&to=2024-03-01"],
    ["from after to", "/reports/sales?from=2024-03-01&to=2024-01-01"],
    ["repeated parameter", "/reports/sales?from=2024-01-01&from=2024-01-02&to=2024-03-01"],
  ])("returns 400 for %s", async (_name, path) => {
    const res = await get(path);
    expect(res.status).toBe(400);
    expect(res.body.error).toBeTypeOf("string");
  });
});

describe("GET /reports/top-products", () => {
  it("ranks products by revenue", async () => {
    const res = await get("/reports/top-products").expect(200);
    expect(res.body.products.map((p: { name: string; revenue_cents: number }) => [p.name, p.revenue_cents])).toEqual([
      ["Gadget", 7500],
      ["Widget", 3000],
      ["Doohickey", 1500],
    ]);
  });

  it("respects limit and date range", async () => {
    const res = await get("/reports/top-products?limit=1&from=2024-01-08&to=2024-01-31").expect(200);
    expect(res.body.products).toEqual([{ id: 3, name: "Doohickey", sku: "D-1", units: 3, revenue_cents: 1500 }]);
  });

  it("rejects an out-of-range limit", async () => {
    expect((await get("/reports/top-products?limit=0")).status).toBe(400);
    expect((await get("/reports/top-products?limit=1000")).status).toBe(400);
    expect((await get("/reports/top-products?limit=5;DROP")).status).toBe(400);
  });
});

describe("GET /orders/export", () => {
  const ids = (res: request.Response) => res.body.orders.map((o: { id: number }) => o.id);

  it("exports all orders as JSON sorted by created_at by default", async () => {
    const res = await get("/orders/export").expect(200);
    expect(res.body.count).toBe(6);
    expect(ids(res)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(res.body.orders[3]).toEqual({
      id: 4,
      created_at: "2024-01-08T09:00:00.000Z",
      status: "delivered",
      customer_id: 3,
      customer_name: "Smith, Carol",
      customer_email: "carol@example.com",
      item_count: 4,
      total_cents: 2500,
    });
  });

  it("filters by status (comma separated)", async () => {
    expect(ids(await get("/orders/export?status=paid"))).toEqual([1, 5]);
    expect(ids(await get("/orders/export?status=paid,pending"))).toEqual([1, 5, 6]);
  });

  it("filters by customer and date range", async () => {
    expect(ids(await get("/orders/export?customerId=1"))).toEqual([1, 3, 5]);
    expect(ids(await get("/orders/export?from=2024-01-02&to=2024-01-08"))).toEqual([2, 3, 4]);
    expect(ids(await get("/orders/export?customerId=1&status=paid&from=2024-02-01"))).toEqual([5]);
  });

  it("sorts by a caller-chosen column and direction", async () => {
    expect(ids(await get("/orders/export?sort=total_cents&order=desc"))).toEqual([3, 5, 4, 2, 1, 6]);
    // "=" sorts before "A" in SQLite's BINARY collation.
    expect(ids(await get("/orders/export?sort=customer_name"))).toEqual([2, 6, 1, 3, 5, 4]);
  });

  it("rejects sort columns outside the whitelist (no SQL injection)", async () => {
    for (const sort of ["password", "o.id; DROP TABLE orders", "(SELECT 1)", "id desc"]) {
      const res = await get(`/orders/export?sort=${encodeURIComponent(sort)}`);
      expect(res.status).toBe(400);
    }
    expect((await get("/orders/export?order=sideways")).status).toBe(400);
    expect(db.prepare("SELECT COUNT(*) FROM orders").pluck().get()).toBe(6);
  });

  it("treats filter values as data, not SQL", async () => {
    expect((await get(`/orders/export?status=${encodeURIComponent("paid' OR '1'='1")}`)).status).toBe(400);
    expect((await get(`/orders/export?customerId=${encodeURIComponent("1 OR 1=1")}`)).status).toBe(400);
  });

  it("exports CSV with escaping and formula neutralisation", async () => {
    const res = await get("/orders/export?format=csv&sort=id").expect(200);
    expect(res.headers["content-type"]).toMatch(/^text\/csv/);
    expect(res.headers["content-disposition"]).toContain("orders.csv");
    const lines = res.text.trimEnd().split("\r\n");
    expect(lines[0]).toBe("id,created_at,status,customer_id,customer_name,customer_email,item_count,total_cents");
    expect(lines).toHaveLength(7);
    expect(lines[1]).toBe("1,2024-01-01T10:00:00.000Z,paid,1,Alice,alice@example.com,2,2000");
    expect(lines[2]).toBe(`2,2024-01-02T12:00:00.000Z,shipped,2,"'=HYPERLINK(""http://evil"")",bob@example.com,1,2500`);
    expect(lines[4]).toBe(`4,2024-01-08T09:00:00.000Z,delivered,3,"Smith, Carol",carol@example.com,4,2500`);
  });

  it("rejects an unknown format", async () => {
    expect((await get("/orders/export?format=xml")).status).toBe(400);
  });
});

describe("csvCell", () => {
  it("escapes special characters", () => {
    expect(csvCell("plain")).toBe("plain");
    expect(csvCell('say "hi"')).toBe('"say ""hi"""');
    expect(csvCell("line\nbreak")).toBe('"line\nbreak"');
    expect(csvCell("+1")).toBe("'+1");
    expect(csvCell("@SUM(A1)")).toBe("'@SUM(A1)");
    expect(csvCell(-5)).toBe("-5");
    expect(csvCell(null)).toBe("");
  });
});
