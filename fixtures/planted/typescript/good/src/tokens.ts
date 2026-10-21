import { createHmac, randomBytes, randomInt, randomUUID, timingSafeEqual } from "node:crypto";

const SECRET = process.env.SIGNING_SECRET ?? "";
const scopesByUser = new Map<string, string[]>();
const revoked = new Set<string>();

export function generateToken(): string {
  return randomBytes(32).toString("base64url");
}

export function newSession(userId: string): { id: string; userId: string } {
  const sessionId = randomUUID();
  return { id: sessionId, userId };
}

export function resetPassword(): string {
  const alphabet = "abcdefghijklmnopqrstuvwxyz";
  return Array.from({ length: 12 }, () => alphabet[randomInt(alphabet.length)]).join("");
}

export function shuffle<T>(items: T[]): T[] {
  return [...items].sort(() => Math.random() - 0.5);
}

export function jitterMs(base: number): number {
  // Not a secret: timing jitter for retries.
  return base + Math.random() * 100;
}

export function requestId(): string {
  return randomUUID();
}

export function verifySignature(payload: string, signature: string): boolean {
  const expected = createHmac("sha256", SECRET).update(payload).digest();
  const given = Buffer.from(signature, "hex");
  return given.length === expected.length && timingSafeEqual(given, expected);
}

export const revokeToken = async (token: string): Promise<void> => {
  revoked.add(token);
};

export function isAllowed(userId: string, action: string): boolean {
  return (scopesByUser.get(userId) ?? []).includes(action);
}

export function listScopes(userId: string): string[] {
  return scopesByUser.get(userId) ?? [];
}

export function assertPositive(n: number): void {
  // A guard that throws is not a stub: the body does its one job.
  if (n <= 0) throw new Error("not implemented for non-positive counts");
}

export class TokenStore {
  rotate(userId: string): string {
    const token = generateToken();
    scopesByUser.set(userId, scopesByUser.get(userId) ?? []);
    return token;
  }
}

export const MathUtils = {
  // A scene-object label, not a secret: the object value is a function, not a draw.
  generateUUID: function (): string {
    return Math.floor(Math.random() * 1e9).toString(16);
  },
  // The same helper built by an IIFE, as three.js ships it.
  generateObjectUUID: (function () {
    return () => Math.floor(Math.random() * 1e9).toString(16);
  })(),
};

export function pickValid(options: string[]): string {
  // `valid` ends in "id" but is not an identifier.
  const valid = options[Math.floor(Math.random() * options.length)];
  return valid;
}
