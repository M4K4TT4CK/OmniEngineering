// One-hop data flow (Milestone 3): the same shapes, done safely. Each value
// is provably constant or sanitised by the time it reaches the sink.
import { execFile, exec } from "node:child_process";
import axios from "axios";
import escapeHtml from "escape-html";
import type { Request, Response } from "express";
import { Pool } from "pg";

const pool = new Pool();
const GIT = "git";

function greeting(name: string): string {
  return "<p>Hello " + escapeHtml(name) + "</p>";
}

export function hello(req: Request, res: Response): void {
  let { name } = req.query;
  name = "guest";
  const page = `<h1>Hi ${name}</h1>`;
  res.send(page);
}

export function card(req: Request, res: Response): void {
  res.send(greeting(String(req.params.name)));
}

export async function notes(req: Request, res: Response): Promise<void> {
  const rows = await pool.query("SELECT * FROM notes WHERE owner = $1", [req.params.owner]);
  res.json(rows.rows);
}

export async function preview(req: Request, res: Response): Promise<void> {
  const target = `https://previews.example.com/render?u=${encodeURIComponent(req.body.url)}`;
  const upstream = await axios.get(target, { timeout: 5000 });
  res.json({ status: upstream.status });
}

export function mirror(req: Request, res: Response): void {
  const args = ["clone", "--", req.body.repo, "/srv/mirror"];
  execFile(GIT, args, { timeout: 60_000 }, (err) => {
    res.sendStatus(err ? 500 : 201);
  });
}

export function usage(req: Request, res: Response): void {
  let dir = req.params.dir;
  if (false) {
    dir = "/";
  }
  dir = "/var/data";
  const cmd = "du -sh " + (1 + 1 > 3 ? req.params.dir : dir);
  exec(cmd, (err, out) => res.json({ out }));
}
