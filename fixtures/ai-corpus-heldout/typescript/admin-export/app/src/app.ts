import express, { type NextFunction, type Request, type Response } from "express";
import { type DB, hashApiKey, ORDER_STATUSES, type OrderStatus, REVENUE_STATUSES } from "./db.js";

export class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

// ---------------------------------------------------------------------------
// Query parameter parsing. Everything user-supplied is validated here; values
// reach SQL only as bound parameters, and identifiers (sort column, grouping)
// only via fixed whitelists.
// ---------------------------------------------------------------------------

function param(req: Request, name: string): string | undefined {
  const value = req.query[name];
  if (value === undefined || value === "") return undefined;
  if (typeof value !== "string") {
    throw new HttpError(400, `Query parameter '${name}' must be given once`);
  }
  return value;
}

function parseDate(name: string, value: string): string {
  const d = new Date(`${value}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== value) {
    throw new HttpError(400, `'${name}' must be a valid date in YYYY-MM-DD format`);
  }
  return value;
}

function nextDay(date: string): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}

function parseEnum<T extends string>(name: string, value: string | undefined, allowed: readonly T[], fallback: T): T {
  if (value === undefined) return fallback;
  if (!(allowed as readonly string[]).includes(value)) {
    throw new HttpError(400, `'${name}' must be one of: ${allowed.join(", ")}`);
  }
  return value as T;
}

function parsePositiveInt(name: string, value: string, max = Number.MAX_SAFE_INTEGER): number {
  const n = Number(value);
  if (!/^\d+$/.test(value) || n < 1 || n > max) {
    throw new HttpError(400, `'${name}' must be an integer between 1 and ${max}`);
  }
  return n;
}

interface Filter {
  clauses: string[];
  params: unknown[];
}

/** Adds an inclusive [from, to] date filter on o.created_at. */
function addDateRange(req: Request, filter: Filter, required: boolean): void {
  const fromRaw = param(req, "from");
  const toRaw = param(req, "to");
  if (required && (fromRaw === undefined || toRaw === undefined)) {
    throw new HttpError(400, "'from' and 'to' are required");
  }
  const from = fromRaw === undefined ? undefined : parseDate("from", fromRaw);
  const to = toRaw === undefined ? undefined : parseDate("to", toRaw);
  if (from && to && from > to) throw new HttpError(400, "'from' must not be after 'to'");
  if (from) {
    filter.clauses.push("o.created_at >= ?");
    filter.params.push(from);
  }
  if (to) {
    filter.clauses.push("o.created_at < ?");
    filter.params.push(nextDay(to));
  }
}

function addRevenueStatuses(filter: Filter): void {
  filter.clauses.push(`o.status IN (${REVENUE_STATUSES.map(() => "?").join(", ")})`);
  filter.params.push(...REVENUE_STATUSES);
}

function where(filter: Filter): string {
  return filter.clauses.length ? `WHERE ${filter.clauses.join(" AND ")}` : "";
}

// Weeks start on Monday and are labelled by that Monday's date.
const PERIOD_EXPR = {
  day: "date(o.created_at)",
  week: "date(o.created_at, 'weekday 0', '-6 days')",
  month: "strftime('%Y-%m', o.created_at)",
} as const;
type Period = keyof typeof PERIOD_EXPR;

const SORT_COLUMNS = {
  id: "o.id",
  created_at: "o.created_at",
  status: "o.status",
  customer_id: "o.customer_id",
  customer_name: "c.name",
  customer_email: "c.email",
  item_count: "item_count",
  total_cents: "total_cents",
} as const;
type SortColumn = keyof typeof SORT_COLUMNS;

const EXPORT_COLUMNS = [
  "id",
  "created_at",
  "status",
  "customer_id",
  "customer_name",
  "customer_email",
  "item_count",
  "total_cents",
] as const;

// ---------------------------------------------------------------------------
// CSV
// ---------------------------------------------------------------------------

/**
 * Escapes a CSV cell. Text starting with a spreadsheet formula trigger is
 * prefixed with an apostrophe so that e.g. a customer named "=HYPERLINK(...)"
 * is not executed when the export is opened in Excel/Sheets.
 */
export function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  let s = String(value);
  if (typeof value === "string" && /^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function toCsv(columns: readonly string[], rows: Record<string, unknown>[]): string {
  const lines = [columns.join(",")];
  for (const row of rows) lines.push(columns.map((c) => csvCell(row[c])).join(","));
  return lines.join("\r\n") + "\r\n";
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export function createApp(db: DB) {
  const app = express();
  app.disable("x-powered-by");

  const findAdmin = db.prepare<[string], { id: number; name: string }>(
    "SELECT id, name FROM admin_users WHERE api_key_hash = ? AND active = 1",
  );

  function requireAdmin(req: Request, res: Response, next: NextFunction) {
    const key = req.get("x-api-key");
    if (!key) {
      res.status(401).json({ error: "Missing API key (X-API-Key header)" });
      return;
    }
    const admin = findAdmin.get(hashApiKey(key));
    if (!admin) {
      res.status(401).json({ error: "Invalid API key" });
      return;
    }
    res.locals.admin = admin;
    next();
  }

  app.get("/health", (_req, res) => {
    res.json({ status: "ok" });
  });

  app.use(requireAdmin);

  // Sales totals for a date range, grouped by day, week or month.
  app.get("/reports/sales", (req, res) => {
    const groupBy = parseEnum<Period>("groupBy", param(req, "groupBy"), ["day", "week", "month"], "day");
    const filter: Filter = { clauses: [], params: [] };
    addDateRange(req, filter, true);
    addRevenueStatuses(filter);

    const rows = db
      .prepare(
        `SELECT ${PERIOD_EXPR[groupBy]} AS period,
                COUNT(DISTINCT o.id) AS orders,
                COALESCE(SUM(oi.quantity), 0) AS units,
                COALESCE(SUM(oi.quantity * oi.unit_price_cents), 0) AS revenue_cents
           FROM orders o
           LEFT JOIN order_items oi ON oi.order_id = o.id
           ${where(filter)}
          GROUP BY period
          ORDER BY period`,
      )
      .all(...filter.params) as { orders: number; units: number; revenue_cents: number }[];

    const totals = rows.reduce(
      (t, r) => ({
        orders: t.orders + r.orders,
        units: t.units + r.units,
        revenue_cents: t.revenue_cents + r.revenue_cents,
      }),
      { orders: 0, units: 0, revenue_cents: 0 },
    );
    res.json({ from: param(req, "from"), to: param(req, "to"), groupBy, totals, periods: rows });
  });

  // Top products by revenue; date range optional (defaults to all time).
  app.get("/reports/top-products", (req, res) => {
    const limitRaw = param(req, "limit");
    const limit = limitRaw === undefined ? 10 : parsePositiveInt("limit", limitRaw, 100);
    const filter: Filter = { clauses: [], params: [] };
    addDateRange(req, filter, false);
    addRevenueStatuses(filter);

    const rows = db
      .prepare(
        `SELECT p.id, p.name, p.sku,
                SUM(oi.quantity) AS units,
                SUM(oi.quantity * oi.unit_price_cents) AS revenue_cents
           FROM order_items oi
           JOIN orders o   ON o.id = oi.order_id
           JOIN products p ON p.id = oi.product_id
           ${where(filter)}
          GROUP BY p.id
          ORDER BY revenue_cents DESC, p.id ASC
          LIMIT ?`,
      )
      .all(...filter.params, limit);
    res.json({ products: rows });
  });

  // Order export (CSV or JSON) with filters and caller-chosen sorting.
  app.get("/orders/export", (req, res) => {
    const format = parseEnum("format", param(req, "format"), ["json", "csv"] as const, "json");
    const sort = parseEnum<SortColumn>(
      "sort",
      param(req, "sort"),
      Object.keys(SORT_COLUMNS) as SortColumn[],
      "created_at",
    );
    const order = parseEnum("order", param(req, "order"), ["asc", "desc"] as const, "asc");
    const direction = order === "desc" ? "DESC" : "ASC";

    const filter: Filter = { clauses: [], params: [] };
    addDateRange(req, filter, false);

    const statusRaw = param(req, "status");
    if (statusRaw !== undefined) {
      const statuses = statusRaw.split(",").map((s) => parseEnum<OrderStatus>("status", s.trim(), ORDER_STATUSES, "pending"));
      filter.clauses.push(`o.status IN (${statuses.map(() => "?").join(", ")})`);
      filter.params.push(...statuses);
    }

    const customerRaw = param(req, "customerId");
    if (customerRaw !== undefined) {
      filter.clauses.push("o.customer_id = ?");
      filter.params.push(parsePositiveInt("customerId", customerRaw));
    }

    const rows = db
      .prepare(
        `SELECT o.id, o.created_at, o.status, o.customer_id,
                c.name  AS customer_name,
                c.email AS customer_email,
                COALESCE(SUM(oi.quantity), 0) AS item_count,
                COALESCE(SUM(oi.quantity * oi.unit_price_cents), 0) AS total_cents
           FROM orders o
           JOIN customers c ON c.id = o.customer_id
           LEFT JOIN order_items oi ON oi.order_id = o.id
           ${where(filter)}
          GROUP BY o.id
          ORDER BY ${SORT_COLUMNS[sort]} ${direction}, o.id ${direction}`,
      )
      .all(...filter.params) as Record<string, unknown>[];

    if (format === "csv") {
      res.set("Content-Type", "text/csv; charset=utf-8");
      res.set("Content-Disposition", 'attachment; filename="orders.csv"');
      res.send(toCsv(EXPORT_COLUMNS, rows));
    } else {
      res.json({ count: rows.length, orders: rows });
    }
  });

  app.use((_req, res) => {
    res.status(404).json({ error: "Not found" });
  });

  app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
    if (err instanceof HttpError) {
      res.status(err.status).json({ error: err.message });
      return;
    }
    console.error(err);
    res.status(500).json({ error: "Internal server error" });
  });

  return app;
}
