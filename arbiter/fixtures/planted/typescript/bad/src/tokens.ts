import { randomUUID } from "node:crypto";

export function generateToken(): string {
  return Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2); // expect: arbiter/typescript.insecure-random-secret
}

export function newSession(userId: string): { id: string; userId: string } {
  const sessionId = Math.random().toString(16).slice(2); // expect: arbiter/typescript.insecure-random-secret
  return { id: sessionId, userId };
}

export function resetPassword(): string {
  const tempPassword = Array.from({ length: 12 }, () => String.fromCharCode(97 + Math.floor(Math.random() * 26))).join(""); // expect: arbiter/typescript.insecure-random-secret
  return tempPassword;
}

export function shuffle<T>(items: T[]): T[] {
  // Not a secret: ordering a list is a fine use of Math.random.
  return [...items].sort(() => Math.random() - 0.5);
}

export function requestId(): string {
  return randomUUID();
}

export function verifySignature(payload: string, signature: string): boolean {
  throw new Error("not implemented"); // expect: arbiter/typescript.stub-throws-not-implemented
}

export const revokeToken = async (token: string): Promise<void> => {
  throw new Error("TODO: revoke in the store"); // expect: arbiter/typescript.stub-throws-not-implemented
};

export function isAllowed(userId: string, action: string): boolean {
  return true; // TODO: real permission check // expect: arbiter/typescript.stub-returns-constant
}

export function listScopes(userId: string): string[] {
  // TODO: load from the database
  return []; // expect: arbiter/typescript.stub-returns-constant
}

export class TokenStore {
  rotate(userId: string): string {
    throw new Error("unimplemented"); // expect: arbiter/typescript.stub-throws-not-implemented
  }
}
