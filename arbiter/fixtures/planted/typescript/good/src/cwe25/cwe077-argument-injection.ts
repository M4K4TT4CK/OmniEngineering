// CWE-77: clone a user-supplied repository, validated and `--`-separated.
import { execFile, spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import type { Request, Response } from "express";

export function importRepo(req: Request, res: Response): void {
  const repoUrl = new URL(String(req.body.repoUrl));
  if (repoUrl.protocol !== "https:") {
    res.sendStatus(400);
    return;
  }
  const dir = mkdtempSync("/tmp/import-");
  execFile("git", ["clone", "--", repoUrl.href, dir], { timeout: 120_000 }, (err) => {
    res.status(err ? 500 : 201).json({ dir });
  });
}

export function ping(req: Request, res: Response): void {
  const host = String(req.query.host);
  if (host.startsWith("-") || !/^[a-z0-9.-]+$/i.test(host)) {
    res.sendStatus(400);
    return;
  }
  const child = spawn("ssh", ["--", host, "uptime"], { timeout: 10_000 });
  child.on("close", (code) => res.json({ code }));
}

export function checkout(req: Request, res: Response): void {
  // The value is bound to its option, so it cannot be read as another one.
  execFile("git", ["fetch", "origin", `--depth=${Number(req.query.depth) || 1}`], { timeout: 60_000 }, (err) => {
    res.sendStatus(err ? 500 : 204);
  });
}
