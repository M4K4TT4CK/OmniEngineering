// CWE-287: auth middleware that verifies the bearer token's signature and algorithm.
import jwt from "jsonwebtoken";
import type { NextFunction, Request, Response } from "express";

export function requireUser(req: Request, res: Response, next: NextFunction): void {
  const token = (req.headers.authorization ?? "").replace(/^Bearer /, "");
  try {
    const claims = jwt.verify(token, process.env.JWT_SECRET ?? "", { algorithms: ["HS256"] }) as { sub: string };
    res.locals.userId = claims.sub;
    next();
  } catch {
    res.sendStatus(401);
  }
}
