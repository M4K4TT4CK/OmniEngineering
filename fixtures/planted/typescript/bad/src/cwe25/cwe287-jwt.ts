// CWE-287: auth middleware that decodes the bearer token without verifying it,
// and verifiers whose options switch the signature or the expiry check off.
import jwt from "jsonwebtoken";
import { decodeJwt } from "jose";
import type { NextFunction, Request, Response } from "express";

export function requireUser(req: Request, res: Response, next: NextFunction): void {
  const token = (req.headers.authorization ?? "").replace(/^Bearer /, "");
  const claims = jwt.decode(token) as { sub?: string } | null; // expect: arbiter/typescript.jwt-decode-unverified
  if (!claims?.sub) {
    res.sendStatus(401);
    return;
  }
  res.locals.userId = claims.sub;
  next();
}

export function tenantOf(token: string): string | undefined {
  return decodeJwt(token).tenant as string | undefined; // expect: arbiter/typescript.jwt-decode-unverified
}

export function verifyAnyAlg(token: string, key: string): jwt.JwtPayload | string {
  return jwt.verify(token, key, { algorithms: ["HS256", "none"] }); // expect: arbiter/typescript.jwt-verify-insecure-options
}

export function verifyStale(token: string): jwt.JwtPayload | string {
  return jwt.verify(token, process.env.JWT_SECRET ?? "", {
    algorithms: ["HS256"],
    ignoreExpiration: true, // expect: arbiter/typescript.jwt-verify-insecure-options
  });
}
