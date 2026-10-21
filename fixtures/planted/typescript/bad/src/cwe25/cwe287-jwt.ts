// CWE-287: auth middleware that decodes the bearer token without verifying it.
import jwt from "jsonwebtoken";
import type { NextFunction, Request, Response } from "express";

export function requireUser(req: Request, res: Response, next: NextFunction): void {
  const token = (req.headers.authorization ?? "").replace(/^Bearer /, "");
  const claims = jwt.decode(token) as { sub?: string } | null; // gap: CWE-287 - nothing in this build reports it (docs/cwe-top25.md)
  if (!claims?.sub) {
    res.sendStatus(401);
    return;
  }
  res.locals.userId = claims.sub;
  next();
}
