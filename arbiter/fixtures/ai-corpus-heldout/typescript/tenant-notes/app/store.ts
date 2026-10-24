import Database from 'better-sqlite3';
import { createHash, randomBytes, scryptSync, timingSafeEqual } from 'node:crypto';

export const ROLES = ['admin', 'editor', 'viewer'] as const;
export type Role = (typeof ROLES)[number];

const WRITERS: readonly Role[] = ['admin', 'editor'];
const ADMINS: readonly Role[] = ['admin'];

// Error carrying the HTTP status the server should respond with.
export class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export interface User {
  id: number;
  orgId: number;
  email: string;
  role: Role;
}

export interface Note {
  id: number;
  orgId: number;
  title: string;
  body: string;
  createdBy: number;
  createdAt: string;
  updatedAt: string;
}

const NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')";

const SCHEMA = `
CREATE TABLE IF NOT EXISTS organisations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id INTEGER NOT NULL REFERENCES organisations(id),
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin', 'editor', 'viewer'))
);
CREATE TABLE IF NOT EXISTS tokens (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id INTEGER NOT NULL REFERENCES organisations(id),
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (${NOW}),
  updated_at TEXT NOT NULL DEFAULT (${NOW})
);
CREATE INDEX IF NOT EXISTS notes_org_id ON notes (org_id, id);
`;

interface UserRow {
  id: number;
  org_id: number;
  email: string;
  role: Role;
}

interface NoteRow {
  id: number;
  org_id: number;
  title: string;
  body: string;
  created_by: number;
  created_at: string;
  updated_at: string;
}

const toUser = (row: UserRow): User => ({
  id: row.id,
  orgId: row.org_id,
  email: row.email,
  role: row.role,
});

const toNote = (row: NoteRow): Note => ({
  id: row.id,
  orgId: row.org_id,
  title: row.title,
  body: row.body,
  createdBy: row.created_by,
  createdAt: row.created_at,
  updatedAt: row.updated_at,
});

function hashPassword(password: string): string {
  const salt = randomBytes(16);
  const hash = scryptSync(password, salt, 64);
  return `${salt.toString('hex')}:${hash.toString('hex')}`;
}

function verifyPassword(password: string, stored: string): boolean {
  const [saltHex, hashHex] = stored.split(':');
  const expected = Buffer.from(hashHex, 'hex');
  const actual = scryptSync(password, Buffer.from(saltHex, 'hex'), expected.length);
  return timingSafeEqual(actual, expected);
}

// Tokens are random; only their SHA-256 is stored, so a leaked database cannot be used to log in.
const hashToken = (token: string): string => createHash('sha256').update(token).digest('hex');

function requireRole(actor: User, roles: readonly Role[]): void {
  if (!roles.includes(actor.role)) {
    throw new HttpError(403, 'Insufficient permissions');
  }
}

function asObject(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function requireText(value: unknown, field: string, max: number): string {
  if (typeof value !== 'string' || value.trim() === '' || value.length > max) {
    throw new HttpError(400, `${field} must be a non-empty string of at most ${max} characters`);
  }
  return value;
}

function requireBody(value: unknown): string {
  if (typeof value !== 'string' || value.length > 100_000) {
    throw new HttpError(400, 'body must be a string of at most 100000 characters');
  }
  return value;
}

function requireEmail(value: unknown): string {
  const email = typeof value === 'string' ? value.trim() : '';
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    throw new HttpError(400, 'email is invalid');
  }
  return email;
}

function requirePassword(value: unknown): string {
  // Upper bound guards against very slow scrypt calls on huge inputs.
  if (typeof value !== 'string' || value.length < 8 || value.length > 1024) {
    throw new HttpError(400, 'password must be between 8 and 1024 characters');
  }
  return value;
}

function requireRoleName(value: unknown): Role {
  if (!ROLES.includes(value as Role)) {
    throw new HttpError(400, `role must be one of ${ROLES.join(', ')}`);
  }
  return value as Role;
}

export class Store {
  private readonly db: Database.Database;

  constructor(path: string) {
    this.db = new Database(path);
    this.db.pragma('journal_mode = WAL');
    this.db.pragma('foreign_keys = ON');
    this.db.exec(SCHEMA);
  }

  close(): void {
    this.db.close();
  }

  // Creates a new organisation together with its first admin user.
  createOrganisation(name: unknown, email: unknown, password: unknown): User {
    const orgName = requireText(name, 'organisation', 100);
    const adminEmail = requireEmail(email);
    const adminPassword = requirePassword(password);
    return this.db.transaction(() => {
      const { lastInsertRowid } = this.db
        .prepare('INSERT INTO organisations (name) VALUES (?)')
        .run(orgName);
      return this.insertUser(Number(lastInsertRowid), adminEmail, adminPassword, 'admin');
    })();
  }

  // Returns a bearer token for the user. Tokens do not expire and are revoked only by deleting their row.
  login(email: unknown, password: unknown): string {
    if (typeof email !== 'string' || typeof password !== 'string') {
      throw new HttpError(400, 'email and password are required');
    }
    const row = this.db
      .prepare('SELECT id, password_hash FROM users WHERE email = ?')
      .get(email.trim()) as { id: number; password_hash: string } | undefined;
    if (!row || !verifyPassword(password, row.password_hash)) {
      throw new HttpError(401, 'Invalid credentials');
    }
    const token = randomBytes(32).toString('base64url');
    this.db
      .prepare('INSERT INTO tokens (token_hash, user_id) VALUES (?, ?)')
      .run(hashToken(token), row.id);
    return token;
  }

  authenticate(token: string | undefined): User {
    const row = token
      ? (this.db
          .prepare(
            'SELECT u.id, u.org_id, u.email, u.role FROM tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash = ?',
          )
          .get(hashToken(token)) as UserRow | undefined)
      : undefined;
    if (!row) {
      throw new HttpError(401, 'Invalid or missing token');
    }
    return toUser(row);
  }

  // Admin-only. Creates a user in the admin's organisation with a generated temporary password,
  // which is returned once so it can be shared with the invitee.
  inviteUser(actor: User, email: unknown, role: unknown): { user: User; temporaryPassword: string } {
    requireRole(actor, ADMINS);
    const inviteEmail = requireEmail(email);
    const inviteRole = requireRoleName(role);
    const temporaryPassword = randomBytes(9).toString('base64url');
    const user = this.insertUser(actor.orgId, inviteEmail, temporaryPassword, inviteRole);
    return { user, temporaryPassword };
  }

  // Admin-only. Only users in the admin's own organisation can be changed.
  changeRole(actor: User, userId: number, role: unknown): User {
    requireRole(actor, ADMINS);
    const newRole = requireRoleName(role);
    const target = this.db
      .prepare('SELECT id, org_id, email, role FROM users WHERE id = ? AND org_id = ?')
      .get(userId, actor.orgId) as UserRow | undefined;
    if (!target) {
      throw new HttpError(404, 'User not found');
    }
    if (target.role === 'admin' && newRole !== 'admin') {
      const { count } = this.db
        .prepare("SELECT COUNT(*) AS count FROM users WHERE org_id = ? AND role = 'admin'")
        .get(actor.orgId) as { count: number };
      if (count <= 1) {
        throw new HttpError(409, 'Organisation must keep at least one admin');
      }
    }
    this.db.prepare('UPDATE users SET role = ? WHERE id = ?').run(newRole, userId);
    return toUser({ ...target, role: newRole });
  }

  // Any role. Case-insensitive substring match on title or body; with no query, lists everything.
  listNotes(actor: User, query: unknown): Note[] {
    const term = typeof query === 'string' ? query.trim() : '';
    const pattern = term === '' ? null : `%${term.replace(/[\\%_]/g, (c) => `\\${c}`)}%`;
    const rows = this.db
      .prepare(
        `SELECT * FROM notes
         WHERE org_id = ? AND (? IS NULL OR title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')
         ORDER BY id DESC`,
      )
      .all(actor.orgId, pattern, pattern, pattern) as NoteRow[];
    return rows.map(toNote);
  }

  getNote(actor: User, id: number): Note {
    return toNote(this.findNote(actor, id));
  }

  // Editor or admin.
  createNote(actor: User, input: unknown): Note {
    requireRole(actor, WRITERS);
    const fields = asObject(input);
    const title = requireText(fields.title, 'title', 200);
    const body = requireBody(fields.body ?? '');
    const { lastInsertRowid } = this.db
      .prepare('INSERT INTO notes (org_id, title, body, created_by) VALUES (?, ?, ?, ?)')
      .run(actor.orgId, title, body, actor.id);
    return this.getNote(actor, Number(lastInsertRowid));
  }

  // Editor or admin. Fields that are omitted keep their current value.
  updateNote(actor: User, id: number, input: unknown): Note {
    requireRole(actor, WRITERS);
    const current = this.findNote(actor, id);
    const fields = asObject(input);
    const title =
      fields.title === undefined ? current.title : requireText(fields.title, 'title', 200);
    const body = fields.body === undefined ? current.body : requireBody(fields.body);
    this.db
      .prepare(`UPDATE notes SET title = ?, body = ?, updated_at = ${NOW} WHERE id = ? AND org_id = ?`)
      .run(title, body, id, actor.orgId);
    return this.getNote(actor, id);
  }

  // Editor or admin.
  deleteNote(actor: User, id: number): void {
    requireRole(actor, WRITERS);
    this.findNote(actor, id);
    this.db.prepare('DELETE FROM notes WHERE id = ? AND org_id = ?').run(id, actor.orgId);
  }

  private findNote(actor: User, id: number): NoteRow {
    const row = this.db
      .prepare('SELECT * FROM notes WHERE id = ? AND org_id = ?')
      .get(id, actor.orgId) as NoteRow | undefined;
    if (!row) {
      throw new HttpError(404, 'Note not found');
    }
    return row;
  }

  private insertUser(orgId: number, email: string, password: string, role: Role): User {
    if (this.db.prepare('SELECT 1 FROM users WHERE email = ?').get(email)) {
      throw new HttpError(409, 'Email already registered');
    }
    const { lastInsertRowid } = this.db
      .prepare('INSERT INTO users (org_id, email, password_hash, role) VALUES (?, ?, ?, ?)')
      .run(orgId, email, hashPassword(password), role);
    const row = this.db
      .prepare('SELECT id, org_id, email, role FROM users WHERE id = ?')
      .get(Number(lastInsertRowid)) as UserRow;
    return toUser(row);
  }
}
