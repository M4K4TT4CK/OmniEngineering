// CWE-798: the token-signing secret and the DB password are literals in the source.
import jwt from "jsonwebtoken";
import { Pool } from "pg";

export const pool = new Pool({ host: "db.internal", user: "app", password: "Tr1cky-Pr0d-Pw" }); // expect: arbiter/secrets.assigned-credential expect-ext: semgrep/semgrep-rules.generic.secrets.gitleaks.generic-api-key

export function issueToken(userId: string): string {
  return jwt.sign({ sub: userId }, "s3cr3t-signing-key-do-not-share", { expiresIn: "1h" }); // expect: arbiter/typescript.jwt-hardcoded-secret expect-ext: semgrep/semgrep-rules.javascript.jsonwebtoken.security.hardcoded-jwt-secret
}
