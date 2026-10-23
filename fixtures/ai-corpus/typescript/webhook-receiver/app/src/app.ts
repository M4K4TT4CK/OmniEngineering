import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import express, { type NextFunction, type Request, type Response } from "express";
import type { Database } from "better-sqlite3";

export interface AppOptions {
  db: Database;
  /** Shared secret used by the payment provider to sign payloads. */
  webhookSecret: string;
  /** Bearer token protecting the merchant and event-listing endpoints. */
  adminToken: string;
  fetchFn?: typeof fetch;
  maxAttempts?: number;
  retryDelayMs?: number;
  callbackTimeoutMs?: number;
}

export const SUCCESS_EVENT_TYPE = "payment.succeeded";

export function initSchema(db: Database): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS merchants (
      id TEXT PRIMARY KEY,
      callback_url TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS events (
      id TEXT PRIMARY KEY,
      merchant_id TEXT NOT NULL,
      type TEXT NOT NULL,
      payload TEXT NOT NULL,
      received_at TEXT NOT NULL,
      delivery_status TEXT NOT NULL,
      delivery_attempts INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS events_merchant_idx ON events (merchant_id, rowid);
  `);
}

/** Hex HMAC-SHA256 of the raw body; accepts an optional "sha256=" prefix. */
export function sign(body: Buffer | string, secret: string): string {
  return createHmac("sha256", secret).update(body).digest("hex");
}

export function verifySignature(body: Buffer, header: string | undefined, secret: string): boolean {
  if (!header) return false;
  const provided = header.trim().replace(/^sha256=/i, "").toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(provided)) return false;
  return timingSafeEqual(Buffer.from(provided, "hex"), Buffer.from(sign(body, secret), "hex"));
}

function tokenMatches(provided: string, expected: string): boolean {
  // Hash first so lengths are equal and comparison is constant-time.
  const a = createHash("sha256").update(provided).digest();
  const b = createHash("sha256").update(expected).digest();
  return timingSafeEqual(a, b);
}

function isHttpUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

export function createApp(opts: AppOptions) {
  const { db, webhookSecret, adminToken } = opts;
  const fetchFn = opts.fetchFn ?? fetch;
  const maxAttempts = opts.maxAttempts ?? 3;
  const retryDelayMs = opts.retryDelayMs ?? 1000;
  const callbackTimeoutMs = opts.callbackTimeoutMs ?? 5000;

  initSchema(db);

  const getMerchant = db.prepare("SELECT id, callback_url FROM merchants WHERE id = ?");
  const upsertMerchant = db.prepare(
    "INSERT INTO merchants (id, callback_url) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET callback_url = excluded.callback_url",
  );
  const insertEvent = db.prepare(
    "INSERT OR IGNORE INTO events (id, merchant_id, type, payload, received_at, delivery_status) VALUES (?, ?, ?, ?, ?, ?)",
  );
  const updateDelivery = db.prepare(
    "UPDATE events SET delivery_status = ?, delivery_attempts = ? WHERE id = ?",
  );
  const listEvents = db.prepare(
    "SELECT id, type, payload, received_at, delivery_status, delivery_attempts FROM events WHERE merchant_id = ? ORDER BY rowid DESC LIMIT ?",
  );

  // In-flight deliveries, tracked so tests (and graceful shutdown) can wait for them.
  const pending = new Set<Promise<void>>();

  async function deliver(eventId: string, url: string, body: string): Promise<void> {
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      let ok = false;
      try {
        const res = await fetchFn(url, {
          method: "POST",
          headers: { "content-type": "application/json", "x-event-id": eventId },
          body,
          redirect: "manual",
          signal: AbortSignal.timeout(callbackTimeoutMs),
        });
        ok = res.ok;
      } catch {
        ok = false;
      }
      if (ok) return void updateDelivery.run("delivered", attempt, eventId);
      updateDelivery.run(attempt === maxAttempts ? "failed" : "pending", attempt, eventId);
      if (attempt < maxAttempts) await new Promise((r) => setTimeout(r, retryDelayMs * attempt));
    }
  }

  function track(p: Promise<void>): void {
    const tracked = p.catch(() => {}).finally(() => pending.delete(tracked));
    pending.add(tracked);
  }

  const requireAdmin = (req: Request, res: Response, next: NextFunction) => {
    const m = /^Bearer (.+)$/.exec(req.header("authorization") ?? "");
    if (!m || !tokenMatches(m[1], adminToken)) {
      res.status(401).json({ error: "unauthorized" });
      return;
    }
    next();
  };

  const app = express();

  // Raw body is required: the signature covers the exact bytes sent.
  app.post(
    "/webhooks/payments",
    express.raw({ type: () => true, limit: "1mb" }),
    (req: Request, res: Response) => {
      const raw = Buffer.isBuffer(req.body) ? req.body : Buffer.alloc(0);
      if (!verifySignature(raw, req.header("x-signature"), webhookSecret)) {
        res.status(401).json({ error: "invalid signature" });
        return;
      }

      let event: any;
      try {
        event = JSON.parse(raw.toString("utf8"));
      } catch {
        res.status(400).json({ error: "invalid JSON" });
        return;
      }
      if (
        !event ||
        typeof event.id !== "string" ||
        !event.id ||
        typeof event.type !== "string" ||
        typeof event.merchant_id !== "string"
      ) {
        res.status(400).json({ error: "event requires string fields id, type, merchant_id" });
        return;
      }

      const merchant = getMerchant.get(event.merchant_id) as { callback_url: string } | undefined;
      if (!merchant) {
        res.status(404).json({ error: "unknown merchant" });
        return;
      }

      const isSuccess = event.type === SUCCESS_EVENT_TYPE;
      const info = insertEvent.run(
        event.id,
        event.merchant_id,
        event.type,
        raw.toString("utf8"),
        new Date().toISOString(),
        isSuccess ? "pending" : "not_applicable",
      );
      if (info.changes === 0) {
        // Provider retry of an event we already have: acknowledge, don't re-forward.
        res.status(200).json({ status: "duplicate" });
        return;
      }

      // Acknowledge first so provider timeouts don't depend on the merchant's server.
      res.status(202).json({ status: "accepted" });
      if (isSuccess) track(deliver(event.id, merchant.callback_url, raw.toString("utf8")));
    },
  );

  const json = express.json();

  app.put("/merchants/:id", requireAdmin, json, (req: Request, res: Response) => {
    const url = req.body?.callback_url;
    if (!isHttpUrl(url)) {
      res.status(400).json({ error: "callback_url must be an http(s) URL" });
      return;
    }
    upsertMerchant.run(req.params.id, url);
    res.json({ id: req.params.id, callback_url: url });
  });

  app.get("/merchants/:id/events", requireAdmin, (req: Request, res: Response) => {
    const parsed = Number.parseInt(String(req.query.limit ?? "50"), 10);
    const limit = Number.isFinite(parsed) ? Math.min(Math.max(parsed, 1), 200) : 50;
    const rows = listEvents.all(req.params.id, limit) as Array<{
      id: string;
      type: string;
      payload: string;
      received_at: string;
      delivery_status: string;
      delivery_attempts: number;
    }>;
    res.json({
      events: rows.map((r) => ({
        id: r.id,
        type: r.type,
        received_at: r.received_at,
        delivery_status: r.delivery_status,
        delivery_attempts: r.delivery_attempts,
        payload: JSON.parse(r.payload),
      })),
    });
  });

  // Malformed JSON on admin routes etc.
  app.use((err: any, _req: Request, res: Response, _next: NextFunction) => {
    const status = err?.status ?? err?.statusCode ?? 500;
    res.status(status).json({ error: status === 500 ? "internal error" : "bad request" });
  });

  return { app, flush: async () => void (await Promise.all([...pending])) };
}
