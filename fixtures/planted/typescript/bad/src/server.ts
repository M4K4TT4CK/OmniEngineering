import express, { type Request, type Response } from "express";
import { Pool } from "pg";
import { exec, spawn } from "node:child_process";
import vm from "node:vm";
import dayjs from "dayjs"; // expect: arbiter/authored.undeclared-import

const app = express();
const pool = new Pool();
const settings: Record<string, unknown> = {};

app.get("/users/:id", async (req: Request, res: Response) => {
  const { rows } = await pool.query(`SELECT * FROM users WHERE id = ${req.params.id}`); // expect: arbiter/typescript.sql-string-built
  res.json(rows[0]);
});

app.get("/search", async (req: Request, res: Response) => {
  const name = String(req.query.name ?? "");
  const rows = await pool.query("SELECT id FROM users WHERE name LIKE '%" + name + "%'"); // expect: arbiter/typescript.sql-string-built
  res.json(rows);
});

app.get("/hello", (req: Request, res: Response) => {
  res.send(`<h1>Hello ${req.query.name}</h1>`); // expect: arbiter/typescript.html-from-request
});

app.get("/goodbye", (req: Request, res: Response) => {
  res.write("<p>Bye " + req.params.name + "</p>"); // expect: arbiter/typescript.html-from-request
  res.end();
});

app.get("/disk/:path", (req: Request, res: Response) => {
  exec(`du -sh ${req.params.path}`, (err, out) => res.send(err ? err.message : out)); // expect: arbiter/typescript.shell-command-built
});

app.post("/convert", (req: Request, res: Response) => {
  const child = spawn(`convert ${req.body.file} out.png`, [], { shell: true }); // expect: arbiter/typescript.shell-spawn-built
  child.on("close", (code) => res.json({ code }));
});

app.post("/settings", (req: Request, res: Response) => {
  settings[req.body.key] = req.body.value; // expect: arbiter/typescript.request-keyed-assignment
  res.json(settings);
});

app.post("/formula", (req: Request, res: Response) => {
  const compute = new Function("x", req.body.formula); // expect: arbiter/typescript.new-function-code
  res.json({ result: compute(1) });
});

app.post("/script", (req: Request, res: Response) => {
  const result = vm.runInNewContext(req.body.script, { input: 1 }); // expect: arbiter/typescript.vm-run-code
  res.json({ result });
});

export function parseFilter(body: string): unknown {
  return eval("(" + body + ")"); // expect: arbiter/typescript.eval-call
}

export function today(): string {
  return dayjs().format("YYYY-MM-DD");
}

export default app;
