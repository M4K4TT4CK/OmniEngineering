import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import http from "node:http";
import Database from "better-sqlite3";

export const EVENT_TYPES = ["payment.succeeded", "payment.failed", "refund.created"] as const;
export type EventType = (typeof EVENT_TYPES)[number];

export interface PaymentEvent {
  id: string;
  type: EventType;
  order_id: string;
  amount: number; // integer, minor currency units (e.g. cents)
}

export type OrderStatus = "pending" | "failed" | "paid" | "partially_refunded" | "refunded";

export interface OrderView {
  order_id: string;
  status: OrderStatus;
  amount_paid: number;
  amount_refunded: number;
  failed_attempts: number;
  updated_at: string;
}

export interface AppOptions {
  dbPath: string;
  webhookSecret: string;
  maxBodyBytes?: number;
}

export const SIGNATURE_HEADER = "x-signature";
const MAX_ID_LENGTH = 255;

export function sign(body: string | Buffer, secret: string): string {
  return createHmac("sha256", secret).update(body).digest("hex");
}

/** Constant-time check of `X-Signature: <hex>` (an optional `sha256=` prefix is accepted). */
export function verifySignature(body: Buffer, header: unknown, secret: string): boolean {
  if (typeof header !== "string") return false;
  const provided = header.startsWith("sha256=") ? header.slice("sha256=".length) : header;
  if (!/^[0-9a-fA-F]{64}$/.test(provided)) return false;
  const expected = createHmac("sha256", secret).update(body).digest();
  return timingSafeEqual(expected, Buffer.from(provided, "hex"));
}

function isId(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= MAX_ID_LENGTH;
}

/** Returns the validated event, or a string describing why the payload is invalid. */
export function parseEvent(body: Buffer): PaymentEvent | string {
  let data: unknown;
  try {
    data = JSON.parse(body.toString("utf8"));
  } catch {
    return "body is not valid JSON";
  }
  if (typeof data !== "object" || data === null || Array.isArray(data)) return "body must be a JSON object";
  const { id, type, order_id, amount } = data as Record<string, unknown>;
  if (!isId(id)) return "id must be a non-empty string";
  if (typeof type !== "string" || !(EVENT_TYPES as readonly string[]).includes(type)) {
    return `type must be one of ${EVENT_TYPES.join(", ")}`;
  }
  if (!isId(order_id)) return "order_id must be a non-empty string";
  if (typeof amount !== "number" || !Number.isSafeInteger(amount) || amount <= 0) {
    return "amount must be a positive integer (minor units)";
  }
  return { id, type: type as EventType, order_id, amount };
}

/**
 * Status is derived from accumulated totals rather than from the last event seen, so the
 * result is the same regardless of the order in which the provider delivers events.
 */
export function deriveStatus(amountPaid: number, amountRefunded: number, failedAttempts: number): OrderStatus {
  if (amountPaid > 0) {
    if (amountRefunded >= amountPaid) return "refunded";
    if (amountRefunded > 0) return "partially_refunded";
    return "paid";
  }
  return failedAttempts > 0 ? "failed" : "pending";
}

export type ApplyResult = "applied" | "duplicate" | "conflict";

interface OrderRow {
  id: string;
  amount_paid: number;
  amount_refunded: number;
  failed_attempts: number;
  updated_at: string;
}

export function openStore(dbPath: string) {
  const db = new Database(dbPath);
  db.pragma("journal_mode = WAL");
  db.pragma("busy_timeout = 5000");
  db.exec(`
    CREATE TABLE IF NOT EXISTS orders (
      id              TEXT PRIMARY KEY,
      amount_paid     INTEGER NOT NULL DEFAULT 0,
      amount_refunded INTEGER NOT NULL DEFAULT 0,
      failed_attempts INTEGER NOT NULL DEFAULT 0,
      created_at      TEXT NOT NULL,
      updated_at      TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS processed_events (
      event_id    TEXT PRIMARY KEY,
      fingerprint TEXT NOT NULL,
      order_id    TEXT NOT NULL,
      type        TEXT NOT NULL,
      amount      INTEGER NOT NULL,
      received_at TEXT NOT NULL
    );
  `);

  const selectEvent = db.prepare<[string], { fingerprint: string }>(
    "SELECT fingerprint FROM processed_events WHERE event_id = ?",
  );
  const insertEvent = db.prepare(
    "INSERT INTO processed_events (event_id, fingerprint, order_id, type, amount, received_at) VALUES (?, ?, ?, ?, ?, ?)",
  );
  const ensureOrder = db.prepare(
    "INSERT INTO orders (id, created_at, updated_at) VALUES (?, ?, ?) ON CONFLICT(id) DO NOTHING",
  );
  const addPaid = db.prepare("UPDATE orders SET amount_paid = amount_paid + ?, updated_at = ? WHERE id = ?");
  const addRefunded = db.prepare("UPDATE orders SET amount_refunded = amount_refunded + ?, updated_at = ? WHERE id = ?");
  const addFailure = db.prepare("UPDATE orders SET failed_attempts = failed_attempts + 1, updated_at = ? WHERE id = ?");
  const selectOrder = db.prepare<[string], OrderRow>(
    "SELECT id, amount_paid, amount_refunded, failed_attempts, updated_at FROM orders WHERE id = ?",
  );

  // Recording the event id and mutating the order happen in one transaction, so an event is
  // either fully applied and remembered, or not at all (and a provider retry will apply it).
  const applyTx = db.transaction((event: PaymentEvent): ApplyResult => {
    const fingerprint = createHash("sha256")
      .update(JSON.stringify([event.type, event.order_id, event.amount]))
      .digest("hex");
    const existing = selectEvent.get(event.id);
    if (existing) return existing.fingerprint === fingerprint ? "duplicate" : "conflict";

    const now = new Date().toISOString();
    insertEvent.run(event.id, fingerprint, event.order_id, event.type, event.amount, now);
    ensureOrder.run(event.order_id, now, now);
    switch (event.type) {
      case "payment.succeeded":
        addPaid.run(event.amount, now, event.order_id);
        break;
      case "refund.created":
        addRefunded.run(event.amount, now, event.order_id);
        break;
      case "payment.failed":
        addFailure.run(now, event.order_id);
        break;
    }
    return "applied";
  });

  return {
    db,
    // IMMEDIATE takes the write lock up front, so concurrent writers (including other
    // processes sharing the file) cannot both pass the "already processed?" check.
    applyEvent: (event: PaymentEvent): ApplyResult => applyTx.immediate(event),
    getOrder(orderId: string): OrderView | undefined {
      const row = selectOrder.get(orderId);
      if (!row) return undefined;
      return {
        order_id: row.id,
        status: deriveStatus(row.amount_paid, row.amount_refunded, row.failed_attempts),
        amount_paid: row.amount_paid,
        amount_refunded: row.amount_refunded,
        failed_attempts: row.failed_attempts,
        updated_at: row.updated_at,
      };
    },
    close: () => db.close(),
  };
}

export type Store = ReturnType<typeof openStore>;

class BodyTooLargeError extends Error {}

async function readBody(req: http.IncomingMessage, limit: number): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > limit) throw new BodyTooLargeError();
    chunks.push(chunk as Buffer);
  }
  return Buffer.concat(chunks);
}

function send(res: http.ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(payload) });
  res.end(payload);
}

export function createApp(options: AppOptions) {
  if (!options.webhookSecret) throw new Error("webhookSecret is required");
  const secret = options.webhookSecret;
  const maxBodyBytes = options.maxBodyBytes ?? 64 * 1024;
  const store = openStore(options.dbPath);

  async function handleWebhook(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
    let body: Buffer;
    try {
      body = await readBody(req, maxBodyBytes);
    } catch (err) {
      if (err instanceof BodyTooLargeError) {
        res.setHeader("connection", "close");
        return send(res, 413, { error: "payload too large" });
      }
      throw err;
    }

    // Authenticate the exact bytes received before parsing anything.
    if (!verifySignature(body, req.headers[SIGNATURE_HEADER], secret)) {
      return send(res, 401, { error: "invalid signature" });
    }

    const event = parseEvent(body);
    if (typeof event === "string") return send(res, 400, { error: event });

    const result = store.applyEvent(event);
    if (result === "conflict") {
      return send(res, 409, { error: "event id already used with a different payload", event_id: event.id });
    }
    // Duplicates get a 2xx so the provider stops retrying.
    return send(res, 200, { event_id: event.id, result });
  }

  function handleGetOrder(orderId: string, res: http.ServerResponse): void {
    const order = store.getOrder(orderId);
    if (!order) return send(res, 404, { error: "order not found" });
    send(res, 200, order);
  }

  const server = http.createServer((req, res) => {
    const { pathname } = new URL(req.url ?? "/", "http://localhost");

    const route = async (): Promise<void> => {
      if (pathname === "/webhooks/payments") {
        if (req.method !== "POST") return send(res, 405, { error: "method not allowed" });
        return handleWebhook(req, res);
      }
      const match = /^\/orders\/([^/]+)$/.exec(pathname);
      if (match) {
        if (req.method !== "GET") return send(res, 405, { error: "method not allowed" });
        let orderId: string;
        try {
          orderId = decodeURIComponent(match[1]);
        } catch {
          return send(res, 400, { error: "invalid order id" });
        }
        return handleGetOrder(orderId, res);
      }
      send(res, 404, { error: "not found" });
    };

    route().catch((err) => {
      console.error("request failed", err);
      // A 5xx makes the provider retry; the transaction was rolled back, so nothing was applied.
      if (!res.headersSent) send(res, 500, { error: "internal error" });
      else res.destroy();
    });
  });

  server.on("close", () => store.close());

  return { server, store };
}
