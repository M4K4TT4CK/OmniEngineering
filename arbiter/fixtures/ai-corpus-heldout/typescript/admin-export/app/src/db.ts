import Database from "better-sqlite3";
import { createHash, randomBytes } from "node:crypto";

export type DB = Database.Database;

export const ORDER_STATUSES = [
  "pending",
  "paid",
  "shipped",
  "delivered",
  "cancelled",
  "refunded",
] as const;
export type OrderStatus = (typeof ORDER_STATUSES)[number];

/** Orders in these statuses count towards revenue in reports. */
export const REVENUE_STATUSES: readonly OrderStatus[] = ["paid", "shipped", "delivered"];

const statusList = ORDER_STATUSES.map((s) => `'${s}'`).join(", ");

// created_at is ISO-8601 UTC text, e.g. 2024-01-31T14:05:00.000Z.
// Money is stored as integer cents to avoid floating point rounding.
const SCHEMA = `
CREATE TABLE IF NOT EXISTS customers (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  email      TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS products (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  sku         TEXT NOT NULL UNIQUE,
  price_cents INTEGER NOT NULL CHECK (price_cents >= 0)
);

CREATE TABLE IF NOT EXISTS orders (
  id          INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  status      TEXT NOT NULL CHECK (status IN (${statusList})),
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
  id               INTEGER PRIMARY KEY,
  order_id         INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  product_id       INTEGER NOT NULL REFERENCES products(id),
  quantity         INTEGER NOT NULL CHECK (quantity > 0),
  unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0)
);

CREATE TABLE IF NOT EXISTS admin_users (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  api_key_hash TEXT NOT NULL UNIQUE,
  active       INTEGER NOT NULL DEFAULT 1,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_customer   ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_status     ON orders(status);
CREATE INDEX IF NOT EXISTS idx_items_order       ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_items_product     ON order_items(product_id);
`;

export function openDb(file: string): DB {
  const db = new Database(file);
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");
  db.exec(SCHEMA);
  return db;
}

/**
 * API keys are random 256-bit tokens, so a plain SHA-256 is sufficient (no
 * salt/KDF needed) and lets us look a key up by its hash. Only the hash is stored.
 */
export function hashApiKey(key: string): string {
  return createHash("sha256").update(key, "utf8").digest("hex");
}

/** Creates an admin user and returns the plaintext API key (shown once). */
export function createAdminKey(db: DB, name: string): string {
  const key = "adm_" + randomBytes(32).toString("base64url");
  db.prepare("INSERT INTO admin_users (name, api_key_hash) VALUES (?, ?)").run(
    name,
    hashApiKey(key),
  );
  return key;
}
