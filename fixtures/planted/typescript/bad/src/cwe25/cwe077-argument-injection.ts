// CWE-77: clone a user-supplied repository (argument injection, CWE-88).
import { execFile } from "node:child_process";
import { mkdtempSync } from "node:fs";
import type { Request, Response } from "express";

export function importRepo(req: Request, res: Response): void {
  const dir = mkdtempSync("/tmp/import-");
  execFile("git", ["clone", req.body.repoUrl, dir], { timeout: 120_000 }, (err) => { // gap: CWE-77 - nothing in this build reports it (docs/cwe-top25.md)
    res.status(err ? 500 : 201).json({ dir });
  });
}
