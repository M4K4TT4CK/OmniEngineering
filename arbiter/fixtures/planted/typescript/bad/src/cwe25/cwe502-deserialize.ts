// CWE-502: restore a user's saved preferences from a cookie with node-serialize.
import serialize from "node-serialize";
import type { Request, Response } from "express";

export function loadPrefs(req: Request, res: Response): void {
  const raw = Buffer.from(req.cookies.prefs ?? "", "base64").toString();
  const prefs = serialize.unserialize(raw); // expect-ext: semgrep/semgrep-rules.javascript.express.security.audit.express-third-party-object-deserialization
  res.json(prefs);
}
