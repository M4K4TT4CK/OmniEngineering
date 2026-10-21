// CWE-798: the token-signing secret and the DB password come from the environment.
import jwt from "jsonwebtoken";
import { Pool } from "pg";

export const pool = new Pool({ host: "db.internal", user: "app", password: process.env.PGPASSWORD });

export function issueToken(userId: string): string {
  return jwt.sign({ sub: userId }, process.env.JWT_SECRET ?? "", { expiresIn: "1h", algorithm: "HS256" });
}
