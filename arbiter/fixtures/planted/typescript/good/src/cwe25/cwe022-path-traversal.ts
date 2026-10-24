// CWE-22: serve an uploaded attachment by name, confined to the uploads root.
import path from "node:path";
import type { Request, Response } from "express";

const UPLOADS = "/srv/app/uploads";

export function download(req: Request, res: Response): void {
  const name = path.basename(req.params.name);
  res.sendFile(name, { root: UPLOADS, dotfiles: "deny" });
}
