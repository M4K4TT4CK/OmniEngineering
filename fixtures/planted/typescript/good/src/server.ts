import express, { type Request, type Response } from "express";
import { Pool } from "pg";
import { execFile, spawn } from "node:child_process";
import escapeHtml from "escape-html";
import dayjs from "dayjs";

const app = express();
const pool = new Pool();
const settings = new Map<string, unknown>();
const ALLOWED_SETTINGS = new Set(["theme", "locale"]);
const FORMULAS: Record<string, (x: number) => number> = { double: (x) => x * 2, square: (x) => x * x };

app.get("/users/:id", async (req: Request, res: Response) => {
  const { rows } = await pool.query("SELECT * FROM users WHERE id = $1", [req.params.id]);
  res.json(rows[0]);
});

app.get("/search", async (req: Request, res: Response) => {
  const name = String(req.query.name ?? "");
  const rows = await pool.query("SELECT id FROM users WHERE name LIKE $1", [`%${name}%`]);
  res.json(rows);
});

app.get("/report", async (_req: Request, res: Response) => {
  // Two literals joined: nothing from outside reaches the statement.
  const rows = await pool.query("SELECT id, name FROM users " + "ORDER BY name");
  res.json(rows);
});

app.get("/hello", (req: Request, res: Response) => {
  res.send(`<h1>Hello ${escapeHtml(String(req.query.name ?? ""))}</h1>`);
});

app.get("/goodbye", (_req: Request, res: Response) => {
  res.write("<p>Bye</p>");
  res.end();
});

app.get("/disk/:path", (req: Request, res: Response) => {
  execFile("du", ["-sh", req.params.path], (err, out) => res.send(err ? err.message : out));
});

app.get("/uptime", (_req: Request, res: Response) => {
  execFile("uptime", [], (err, out) => res.send(err ? err.message : out));
});

app.post("/convert", (req: Request, res: Response) => {
  const child = spawn("convert", [String(req.body.file), "out.png"]);
  child.on("close", (code) => res.json({ code }));
});

app.post("/settings", (req: Request, res: Response) => {
  const key = String(req.body.key);
  if (ALLOWED_SETTINGS.has(key)) settings.set(key, req.body.value);
  res.json(Object.fromEntries(settings));
});

app.post("/formula", (req: Request, res: Response) => {
  const compute = FORMULAS[String(req.body.formula)];
  res.json({ result: compute ? compute(1) : null });
});

export function parseFilter(body: string): unknown {
  return JSON.parse(body);
}

export function today(): string {
  return dayjs().format("YYYY-MM-DD");
}

export default app;
