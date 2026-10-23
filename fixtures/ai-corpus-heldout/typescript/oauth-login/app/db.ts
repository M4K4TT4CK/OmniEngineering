import crypto from "node:crypto";
import Database from "better-sqlite3";

export type Db = Database.Database;

export const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;

export interface User {
  id: number;
  github_id: number;
  login: string;
  name: string | null;
  email: string | null;
  avatar_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface GitHubProfile {
  id: number;
  login: string;
  name: string | null;
  email: string | null;
  avatar_url: string | null;
}

export function openDatabase(file: string): Db {
  const db = new Database(file);
  db.pragma("foreign_keys = ON");
  db.exec(`
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      github_id INTEGER NOT NULL UNIQUE,
      login TEXT NOT NULL,
      name TEXT,
      email TEXT,
      avatar_url TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS sessions (
      id TEXT PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      expires_at INTEGER NOT NULL
    );
  `);
  return db;
}

// Creates the user on first login and refreshes their GitHub details on later ones.
export function upsertUser(db: Db, profile: GitHubProfile): User {
  return db
    .prepare(
      `INSERT INTO users (github_id, login, name, email, avatar_url)
       VALUES (@github_id, @login, @name, @email, @avatar_url)
       ON CONFLICT(github_id) DO UPDATE SET
         login = excluded.login,
         name = excluded.name,
         email = excluded.email,
         avatar_url = excluded.avatar_url,
         updated_at = datetime('now')
       RETURNING *`,
    )
    .get({
      github_id: profile.id,
      login: profile.login,
      name: profile.name,
      email: profile.email,
      avatar_url: profile.avatar_url,
    }) as User;
}

export function createSession(db: Db, userId: number): string {
  db.prepare("DELETE FROM sessions WHERE expires_at <= ?").run(Date.now());
  const id = crypto.randomBytes(32).toString("base64url");
  db.prepare("INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)").run(
    id,
    userId,
    Date.now() + SESSION_TTL_MS,
  );
  return id;
}

export function findSessionUser(db: Db, sessionId: string): User | undefined {
  return db
    .prepare(
      `SELECT users.* FROM sessions
       JOIN users ON users.id = sessions.user_id
       WHERE sessions.id = ? AND sessions.expires_at > ?`,
    )
    .get(sessionId, Date.now()) as User | undefined;
}

export function deleteSession(db: Db, sessionId: string): void {
  db.prepare("DELETE FROM sessions WHERE id = ?").run(sessionId);
}
