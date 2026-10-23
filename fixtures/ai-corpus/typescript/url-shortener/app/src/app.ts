import { randomBytes } from "node:crypto";
import dns from "node:dns/promises";
import net from "node:net";
import express from "express";
import Database from "better-sqlite3";

export type TitleFetcher = (url: string) => Promise<string | null>;

const ALIAS_RE = /^[A-Za-z0-9_-]{3,32}$/;
const RESERVED = new Set(["api", "health"]);
const BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
const MAX_BODY_BYTES = 128 * 1024;
const MAX_REDIRECTS = 3;
const FETCH_TIMEOUT_MS = 5000;

export function openDb(file: string): Database.Database {
  const db = new Database(file);
  db.pragma("journal_mode = WAL");
  db.exec(`CREATE TABLE IF NOT EXISTS links (
    code TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT,
    clicks INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
  )`);
  return db;
}

function generateCode(length = 7): string {
  const bytes = randomBytes(length);
  let out = "";
  for (const b of bytes) out += BASE62[b % 62];
  return out;
}

function parseHttpUrl(input: unknown): URL | null {
  if (typeof input !== "string" || input.length > 2048) return null;
  try {
    const u = new URL(input);
    return u.protocol === "http:" || u.protocol === "https:" ? u : null;
  } catch {
    return null;
  }
}

function isPrivateIp(ip: string): boolean {
  if (net.isIPv4(ip)) {
    const [a, b] = ip.split(".").map(Number);
    return (
      a === 0 || a === 10 || a === 127 ||
      (a === 100 && b >= 64 && b <= 127) ||
      (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 168) ||
      a >= 224
    );
  }
  const v6 = ip.toLowerCase();
  if (v6.startsWith("::ffff:")) return isPrivateIp(v6.slice(7));
  return v6 === "::" || v6 === "::1" || v6.startsWith("fc") || v6.startsWith("fd") || v6.startsWith("fe80");
}

async function assertPublicHost(url: URL): Promise<void> {
  const host = url.hostname.replace(/^\[|\]$/g, "");
  const addrs = net.isIP(host) ? [{ address: host }] : await dns.lookup(host, { all: true });
  if (addrs.length === 0 || addrs.some((a) => isPrivateIp(a.address))) {
    throw new Error("target resolves to a non-public address");
  }
}

async function readLimited(res: Response): Promise<string> {
  if (!res.body) return "";
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (total < MAX_BODY_BYTES) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    total += value.length;
  }
  await reader.cancel().catch(() => {});
  return Buffer.concat(chunks).toString("utf8");
}

function decodeEntities(s: string): string {
  return s
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'");
}

/**
 * Fetches the page and extracts its <title>. Best effort: returns null on any
 * failure. Only public http(s) hosts are contacted, including across redirects.
 * (Resolution is checked before fetching, so DNS rebinding is not fully prevented.)
 */
export const fetchPageTitle: TitleFetcher = async (rawUrl) => {
  try {
    let url = new URL(rawUrl);
    for (let hop = 0; hop <= MAX_REDIRECTS; hop++) {
      if (url.protocol !== "http:" && url.protocol !== "https:") return null;
      await assertPublicHost(url);
      const res = await fetch(url, {
        redirect: "manual",
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
        headers: { "user-agent": "url-shortener/1.0", accept: "text/html" },
      });
      const location = res.headers.get("location");
      if (res.status >= 300 && res.status < 400 && location) {
        url = new URL(location, url);
        continue;
      }
      if (!res.ok) return null;
      const html = await readLimited(res);
      const m = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(html);
      const title = m ? decodeEntities(m[1]).replace(/\s+/g, " ").trim() : "";
      return title ? title.slice(0, 500) : null;
    }
    return null;
  } catch {
    return null;
  }
};

interface LinkRow {
  code: string;
  url: string;
  title: string | null;
  clicks: number;
  created_at: string;
}

export function createApp(db: Database.Database, getTitle: TitleFetcher = fetchPageTitle) {
  const app = express();
  app.use(express.json({ limit: "10kb" }));

  const insert = db.prepare("INSERT INTO links (code, url, title, created_at) VALUES (?, ?, ?, ?)");
  const byCode = db.prepare("SELECT * FROM links WHERE code = ?");
  const exists = db.prepare("SELECT 1 FROM links WHERE code = ?");
  const click = db.prepare("UPDATE links SET clicks = clicks + 1 WHERE code = ?");

  const baseUrl = (req: express.Request) =>
    process.env.BASE_URL?.replace(/\/$/, "") ?? `${req.protocol}://${req.get("host")}`;

  app.post("/api/links", async (req, res) => {
    const { url, alias } = (req.body ?? {}) as { url?: unknown; alias?: unknown };
    const target = parseHttpUrl(url);
    if (!target) return res.status(400).json({ error: "url must be a valid http(s) URL" });

    if (alias !== undefined && alias !== null) {
      if (typeof alias !== "string" || !ALIAS_RE.test(alias) || RESERVED.has(alias.toLowerCase())) {
        return res.status(400).json({
          error: "alias must be 3-32 characters of letters, digits, '-' or '_' and not reserved",
        });
      }
      if (exists.get(alias)) return res.status(409).json({ error: "alias already in use" });
    }

    const title = await getTitle(target.href);
    const createdAt = new Date().toISOString();

    // Insert directly and rely on the PRIMARY KEY to handle races/collisions.
    for (let attempt = 0; attempt < 5; attempt++) {
      const code = typeof alias === "string" ? alias : generateCode();
      try {
        insert.run(code, target.href, title, createdAt);
        return res.status(201).json({
          code,
          shortUrl: `${baseUrl(req)}/${code}`,
          url: target.href,
          title,
          createdAt,
        });
      } catch (err) {
        const sqliteCode = (err as { code?: string }).code ?? "";
        if (!sqliteCode.startsWith("SQLITE_CONSTRAINT")) throw err;
        if (typeof alias === "string") return res.status(409).json({ error: "alias already in use" });
      }
    }
    return res.status(500).json({ error: "could not allocate a short code" });
  });

  app.get("/api/links/:code/stats", (req, res) => {
    const row = byCode.get(req.params.code) as LinkRow | undefined;
    if (!row) return res.status(404).json({ error: "not found" });
    res.json({
      code: row.code,
      url: row.url,
      title: row.title,
      clicks: row.clicks,
      createdAt: row.created_at,
    });
  });

  app.get("/health", (_req, res) => res.json({ status: "ok" }));

  app.get("/:code", (req, res) => {
    const row = byCode.get(req.params.code) as LinkRow | undefined;
    if (!row) return res.status(404).json({ error: "not found" });
    click.run(row.code);
    res.redirect(302, row.url);
  });

  // JSON error handler (e.g. malformed JSON bodies)
  app.use((err: any, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    const status = err?.status && err.status < 500 ? err.status : 500;
    res.status(status).json({ error: status < 500 ? "invalid request" : "internal error" });
  });

  return app;
}
