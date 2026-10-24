// CWE-287: auth middleware that verifies the bearer token's signature and algorithm.
import jwt from "jsonwebtoken";
import { decodeJwt, jwtVerify, createRemoteJWKSet } from "jose";
import type { NextFunction, Request, Response } from "express";

const jwks = createRemoteJWKSet(new URL("https://issuer.example/.well-known/jwks.json"));

export function requireUser(req: Request, res: Response, next: NextFunction): void {
  const token = (req.headers.authorization ?? "").replace(/^Bearer /, "");
  try {
    const claims = jwt.verify(token, process.env.JWT_SECRET ?? "", { algorithms: ["HS256"], ignoreExpiration: false }) as { sub: string };
    res.locals.userId = claims.sub;
    next();
  } catch {
    res.sendStatus(401);
  }
}

// The key id is read from the unverified header only to pick the key; the
// signature is checked in the same function before any claim is trusted.
export function verifyWithKid(token: string, keys: Record<string, string>): jwt.JwtPayload | string {
  const kid = jwt.decode(token, { complete: true })?.header.kid ?? "";
  return jwt.verify(token, keys[kid] ?? "", { algorithms: ["RS256"] });
}

// Verified first; decodeJwt only re-reads the payload of a token jose already checked.
export async function tenantOf(token: string): Promise<string | undefined> {
  await jwtVerify(token, jwks, { algorithms: ["RS256"] });
  return decodeJwt(token).tenant as string | undefined;
}
