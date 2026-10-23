import crypto from 'crypto';
import Database from 'better-sqlite3';
import express, { NextFunction, Request, Response } from 'express';

export interface AppOptions {
  dbPath?: string;
  rateLimit?: number;
  windowMs?: number;
  tokenTtlMs?: number;
  now?: () => number;
}

interface AuthedRequest extends Request {
  userId?: number;
}

class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const sha256 = (s: string) => crypto.createHash('sha256').update(s).digest('hex');

function hashPassword(password: string, salt = crypto.randomBytes(16)): string {
  const key = crypto.scryptSync(password, salt, 64);
  return `${salt.toString('hex')}:${key.toString('hex')}`;
}

function verifyPassword(password: string, stored: string): boolean {
  const [saltHex, keyHex] = stored.split(':');
  const expected = Buffer.from(keyHex, 'hex');
  const actual = crypto.scryptSync(password, Buffer.from(saltHex, 'hex'), expected.length);
  return crypto.timingSafeEqual(actual, expected);
}

// Used so that logins for unknown usernames cost the same as for known ones.
const DUMMY_HASH = hashPassword('dummy-password');

export function createApp(opts: AppOptions = {}) {
  const {
    dbPath = 'notes.db',
    rateLimit = 60,
    windowMs = 60_000,
    tokenTtlMs = 24 * 60 * 60 * 1000,
    now = Date.now,
  } = opts;

  const db = new Database(dbPath);
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');
  db.exec(`
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT NOT NULL UNIQUE COLLATE NOCASE,
      password_hash TEXT NOT NULL,
      created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS tokens (
      token_hash TEXT PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      expires_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS notes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      title TEXT NOT NULL,
      body TEXT NOT NULL DEFAULT '',
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id, updated_at DESC);
  `);

  const app = express();
  app.disable('x-powered-by');
  app.use(express.json({ limit: '100kb' }));

  // --- Rate limiting: fixed window, keyed by user id (or IP when unauthenticated) ---
  const windows = new Map<string, { start: number; count: number }>();
  function limiter(key: string, res: Response) {
    const t = now();
    if (windows.size > 10_000) {
      for (const [k, w] of windows) if (t - w.start >= windowMs) windows.delete(k);
    }
    let w = windows.get(key);
    if (!w || t - w.start >= windowMs) {
      w = { start: t, count: 0 };
      windows.set(key, w);
    }
    w.count++;
    const resetSec = Math.ceil((w.start + windowMs - t) / 1000);
    res.set({
      'X-RateLimit-Limit': String(rateLimit),
      'X-RateLimit-Remaining': String(Math.max(0, rateLimit - w.count)),
      'X-RateLimit-Reset': String(resetSec),
    });
    if (w.count > rateLimit) {
      res.set('Retry-After', String(resetSec));
      throw new HttpError(429, 'Rate limit exceeded');
    }
  }

  const ipLimit = (req: Request, res: Response, next: NextFunction) => {
    limiter(`ip:${req.ip}`, res);
    next();
  };

  const auth = (req: AuthedRequest, res: Response, next: NextFunction) => {
    const m = /^Bearer (.+)$/.exec(req.get('authorization') ?? '');
    const row = m
      ? (db
          .prepare('SELECT user_id, expires_at FROM tokens WHERE token_hash = ?')
          .get(sha256(m[1])) as { user_id: number; expires_at: number } | undefined)
      : undefined;
    if (!row || row.expires_at <= now()) {
      limiter(`ip:${req.ip}`, res);
      throw new HttpError(401, 'Invalid or missing token');
    }
    req.userId = row.user_id;
    limiter(`user:${row.user_id}`, res);
    next();
  };

  function issueToken(userId: number): string {
    const token = crypto.randomBytes(32).toString('hex');
    db.prepare('INSERT INTO tokens (token_hash, user_id, expires_at) VALUES (?, ?, ?)').run(
      sha256(token),
      userId,
      now() + tokenTtlMs,
    );
    db.prepare('DELETE FROM tokens WHERE expires_at <= ?').run(now());
    return token;
  }

  // --- Validation helpers ---
  const str = (v: unknown, name: string, min: number, max: number): string => {
    if (typeof v !== 'string' || v.length < min || v.length > max) {
      throw new HttpError(400, `${name} must be a string of ${min}-${max} characters`);
    }
    return v;
  };
  const credentials = (b: any) => ({
    username: str(b?.username, 'username', 3, 32),
    password: str(b?.password, 'password', 8, 128),
  });
  const noteId = (req: Request): number => {
    const id = Number(req.params.id);
    if (!Number.isSafeInteger(id) || id < 1) throw new HttpError(404, 'Note not found');
    return id;
  };

  // --- Auth routes ---
  app.post('/signup', ipLimit, (req, res) => {
    const { username, password } = credentials(req.body);
    if (!/^[A-Za-z0-9_.-]+$/.test(username)) {
      throw new HttpError(400, 'username may only contain letters, digits, _ . -');
    }
    try {
      const info = db
        .prepare('INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)')
        .run(username, hashPassword(password), now());
      const id = Number(info.lastInsertRowid);
      res.status(201).json({ id, username, token: issueToken(id) });
    } catch (e: any) {
      if (e?.code === 'SQLITE_CONSTRAINT_UNIQUE') throw new HttpError(409, 'Username already taken');
      throw e;
    }
  });

  app.post('/login', ipLimit, (req, res) => {
    const { username, password } = credentials(req.body);
    const user = db
      .prepare('SELECT id, password_hash FROM users WHERE username = ?')
      .get(username) as { id: number; password_hash: string } | undefined;
    const ok = verifyPassword(password, user?.password_hash ?? DUMMY_HASH);
    if (!user || !ok) throw new HttpError(401, 'Invalid username or password');
    res.json({ token: issueToken(user.id) });
  });

  // --- Notes routes ---
  const notes = express.Router();
  notes.use(auth);

  const COLS = 'id, title, body, created_at AS createdAt, updated_at AS updatedAt';

  notes.post('/', (req: AuthedRequest, res) => {
    const title = str(req.body?.title, 'title', 1, 200);
    const body = req.body?.body === undefined ? '' : str(req.body.body, 'body', 0, 10_000);
    const t = now();
    const info = db
      .prepare('INSERT INTO notes (user_id, title, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?)')
      .run(req.userId, title, body, t, t);
    res.status(201).json(db.prepare(`SELECT ${COLS} FROM notes WHERE id = ?`).get(info.lastInsertRowid));
  });

  notes.get('/', (req: AuthedRequest, res) => {
    const limit = Math.min(Math.max(parseInt(String(req.query.limit ?? '50'), 10) || 50, 1), 100);
    const offset = Math.max(parseInt(String(req.query.offset ?? '0'), 10) || 0, 0);
    const params: unknown[] = [req.userId];
    let where = 'user_id = ?';
    if (typeof req.query.q === 'string' && req.query.q !== '') {
      const like = `%${req.query.q.replace(/[\\%_]/g, '\\$&')}%`;
      where += " AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')";
      params.push(like, like);
    }
    const items = db
      .prepare(`SELECT ${COLS} FROM notes WHERE ${where} ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?`)
      .all(...params, limit, offset);
    res.json({ items, limit, offset });
  });

  notes.get('/:id', (req: AuthedRequest, res) => {
    const note = db
      .prepare(`SELECT ${COLS} FROM notes WHERE id = ? AND user_id = ?`)
      .get(noteId(req), req.userId);
    if (!note) throw new HttpError(404, 'Note not found');
    res.json(note);
  });

  notes.put('/:id', (req: AuthedRequest, res) => {
    const id = noteId(req);
    const title = req.body?.title === undefined ? null : str(req.body.title, 'title', 1, 200);
    const body = req.body?.body === undefined ? null : str(req.body.body, 'body', 0, 10_000);
    if (title === null && body === null) throw new HttpError(400, 'Provide title and/or body');
    const info = db
      .prepare(
        'UPDATE notes SET title = COALESCE(?, title), body = COALESCE(?, body), updated_at = ? WHERE id = ? AND user_id = ?',
      )
      .run(title, body, now(), id, req.userId);
    if (info.changes === 0) throw new HttpError(404, 'Note not found');
    res.json(db.prepare(`SELECT ${COLS} FROM notes WHERE id = ?`).get(id));
  });

  notes.delete('/:id', (req: AuthedRequest, res) => {
    const info = db.prepare('DELETE FROM notes WHERE id = ? AND user_id = ?').run(noteId(req), req.userId);
    if (info.changes === 0) throw new HttpError(404, 'Note not found');
    res.status(204).end();
  });

  app.use('/notes', notes);

  app.use((_req, _res, next) => next(new HttpError(404, 'Not found')));

  app.use((err: any, _req: Request, res: Response, _next: NextFunction) => {
    if (err instanceof HttpError) return res.status(err.status).json({ error: err.message });
    if (err?.type === 'entity.parse.failed') return res.status(400).json({ error: 'Invalid JSON' });
    if (err?.type === 'entity.too.large') return res.status(413).json({ error: 'Payload too large' });
    console.error(err);
    res.status(500).json({ error: 'Internal server error' });
  });

  return { app, db };
}
