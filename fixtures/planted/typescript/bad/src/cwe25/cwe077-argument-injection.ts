// CWE-77: clone a user-supplied repository (argument injection, CWE-88).
import { execFile, spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import type { Request, Response } from "express";

export function importRepo(req: Request, res: Response): void {
  const dir = mkdtempSync("/tmp/import-");
  execFile("git", ["clone", req.body.repoUrl, dir], { timeout: 120_000 }, (err) => { // expect: arbiter/typescript.argument-injection
    res.status(err ? 500 : 201).json({ dir });
  });
}

export function ping(req: Request, res: Response): void {
  const host = String(req.query.host);
  const child = spawn("ssh", [host, "uptime"], { timeout: 10_000 }); // expect: arbiter/typescript.argument-injection
  child.on("close", (code) => res.json({ code }));
}
