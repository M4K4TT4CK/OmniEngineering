// CWE-22: serve an uploaded attachment by name.
import path from "node:path";
import type { Request, Response } from "express";

const UPLOADS = "/srv/app/uploads";

export function download(req: Request, res: Response): void {
  res.sendFile(path.join(UPLOADS, req.params.name)); // expect-ext: semgrep/semgrep-rules.javascript.express.security.audit.express-res-sendfile
}
