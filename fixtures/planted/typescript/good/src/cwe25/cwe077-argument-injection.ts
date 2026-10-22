// CWE-77: clone a user-supplied repository, validated and `--`-separated.
import { execFile } from "node:child_process";
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
