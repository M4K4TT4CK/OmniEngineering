// One-hop data flow (Milestone 3): the defect is written one step away from
// the sink, through a local, a destructured field or a same-file helper.
import { execFile, exec } from "node:child_process";
import axios from "axios";
import type { Request, Response } from "express";
import { Pool } from "pg";

const pool = new Pool();
const GIT = "git";

function greeting(name: string): string {
  return "<p>Hello " + name + "</p>";
}

function byOwner(owner: string): string {
  return `SELECT * FROM notes WHERE owner = '${owner}'`;
}

export function hello(req: Request, res: Response): void {
  const { name } = req.query;
  const page = `<h1>Hi ${name}</h1>`;
  res.send(page); // expect: arbiter/typescript.html-from-request
}

export function card(req: Request, res: Response): void {
  res.send(greeting(String(req.params.name))); // expect: arbiter/typescript.html-from-request
}

export async function notes(req: Request, res: Response): Promise<void> {
  const rows = await pool.query(byOwner(req.params.owner)); // expect: arbiter/typescript.sql-string-built
  res.json(rows.rows);
}

export async function preview(req: Request, res: Response): Promise<void> {
  const body = req.body;
  const target = body.url;
  const upstream = await axios.get(target, { timeout: 5000 }); // expect: arbiter/typescript.ssrf-request-url
  res.json({ status: upstream.status });
}

export function mirror(req: Request, res: Response): void {
  const args = ["clone", req.body.repo, "/srv/mirror"];
  execFile(GIT, args, { timeout: 60_000 }, (err) => { // expect: arbiter/typescript.argument-injection
    res.sendStatus(err ? 500 : 201);
  });
}

export function usage(req: Request, res: Response): void {
  const dir = req.params.dir;
  const cmd = "du -sh " + dir;
  exec(cmd, (err, out) => res.json({ out })); // expect: arbiter/typescript.shell-command-built
}
