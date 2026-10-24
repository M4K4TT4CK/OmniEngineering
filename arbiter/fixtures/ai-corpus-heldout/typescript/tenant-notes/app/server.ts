import { createServer, IncomingMessage, Server } from 'node:http';
import { HttpError, Store } from './store';

const MAX_BODY_BYTES = 1024 * 1024;

interface Result {
  status: number;
  body?: unknown;
}

export function createApp(store: Store): Server {
  return createServer(async (req, res) => {
    try {
      const result = await handle(store, req);
      if (result.body === undefined) {
        res.writeHead(result.status).end();
        return;
      }
      res.writeHead(result.status, { 'content-type': 'application/json' });
      res.end(JSON.stringify(result.body));
    } catch (err) {
      const status = err instanceof HttpError ? err.status : 500;
      const message = err instanceof HttpError ? err.message : 'Internal server error';
      if (status === 500) {
        console.error(err);
      }
      res.writeHead(status, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ error: message }));
    }
  });
}

async function handle(store: Store, req: IncomingMessage): Promise<Result> {
  const url = new URL(req.url ?? '/', 'http://localhost');
  const parts = url.pathname.split('/').filter(Boolean);
  const method = req.method ?? 'GET';

  // Public routes.
  if (method === 'POST' && url.pathname === '/orgs') {
    const body = await readJson(req);
    const user = store.createOrganisation(body.organisation, body.email, body.password);
    return { status: 201, body: user };
  }
  if (method === 'POST' && url.pathname === '/auth/login') {
    const body = await readJson(req);
    return { status: 200, body: { token: store.login(body.email, body.password) } };
  }

  // Everything else requires a valid bearer token.
  const actor = store.authenticate(bearerToken(req));

  if (parts[0] === 'notes' && parts.length === 1) {
    if (method === 'GET') {
      return { status: 200, body: store.listNotes(actor, url.searchParams.get('q')) };
    }
    if (method === 'POST') {
      return { status: 201, body: store.createNote(actor, await readJson(req)) };
    }
  }

  if (parts[0] === 'notes' && parts.length === 2) {
    const id = parseId(parts[1]);
    if (method === 'GET') {
      return { status: 200, body: store.getNote(actor, id) };
    }
    if (method === 'PATCH') {
      return { status: 200, body: store.updateNote(actor, id, await readJson(req)) };
    }
    if (method === 'DELETE') {
      store.deleteNote(actor, id);
      return { status: 204 };
    }
  }

  if (parts[0] === 'users' && parts.length === 1 && method === 'POST') {
    const body = await readJson(req);
    return { status: 201, body: store.inviteUser(actor, body.email, body.role) };
  }

  if (parts[0] === 'users' && parts.length === 2 && method === 'PATCH') {
    const body = await readJson(req);
    return { status: 200, body: store.changeRole(actor, parseId(parts[1]), body.role) };
  }

  throw new HttpError(404, 'Not found');
}

function bearerToken(req: IncomingMessage): string | undefined {
  return /^Bearer (.+)$/.exec(req.headers.authorization ?? '')?.[1];
}

function parseId(raw: string): number {
  if (!/^\d+$/.test(raw)) {
    throw new HttpError(404, 'Not found');
  }
  return Number(raw);
}

async function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > MAX_BODY_BYTES) {
      throw new HttpError(413, 'Request body too large');
    }
    chunks.push(chunk as Buffer);
  }
  const text = Buffer.concat(chunks).toString('utf8');
  if (text === '') {
    return {};
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new HttpError(400, 'Invalid JSON');
  }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new HttpError(400, 'JSON body must be an object');
  }
  return value as Record<string, unknown>;
}

if (require.main === module) {
  const store = new Store(process.env.DB_PATH ?? 'notes.db');
  const port = Number(process.env.PORT ?? 3000);
  createApp(store).listen(port, () => {
    console.log(`Listening on http://localhost:${port}`);
  });
}
