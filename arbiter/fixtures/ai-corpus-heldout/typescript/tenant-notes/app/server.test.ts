import { afterEach, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { createApp } from './server';
import { Store } from './store';

const PASSWORD = 'correct horse battery';

interface Ctx {
  store: Store;
  server: Server;
  base: string;
}

let ctx: Ctx;

beforeEach(async () => {
  const store = new Store(':memory:');
  const server = createApp(store);
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  ctx = { store, server, base: `http://127.0.0.1:${port}` };
});

afterEach(async () => {
  await new Promise<void>((resolve) => ctx.server.close(() => resolve()));
  ctx.store.close();
});

async function call(
  method: string,
  path: string,
  options: { token?: string; body?: unknown } = {},
): Promise<{ status: number; body: any }> {
  const headers: Record<string, string> = {};
  if (options.token) {
    headers.authorization = `Bearer ${options.token}`;
  }
  if (options.body !== undefined) {
    headers['content-type'] = 'application/json';
  }
  const res = await fetch(ctx.base + path, {
    method,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const text = await res.text();
  return { status: res.status, body: text ? JSON.parse(text) : undefined };
}

async function login(email: string, password = PASSWORD): Promise<string> {
  const res = await call('POST', '/auth/login', { body: { email, password } });
  assert.equal(res.status, 200);
  return res.body.token;
}

// Creates an organisation with an admin and returns the admin's token.
async function setupOrg(name = 'Acme', adminEmail = `admin@${name.toLowerCase()}.test`): Promise<string> {
  const res = await call('POST', '/orgs', {
    body: { organisation: name, email: adminEmail, password: PASSWORD },
  });
  assert.equal(res.status, 201);
  return login(adminEmail);
}

// Invites a user as the given admin and logs them in with the temporary password.
async function inviteAndLogin(adminToken: string, email: string, role: string): Promise<string> {
  const res = await call('POST', '/users', { token: adminToken, body: { email, role } });
  assert.equal(res.status, 201);
  return login(email, res.body.temporaryPassword);
}

async function createNote(token: string, title: string, body = ''): Promise<any> {
  const res = await call('POST', '/notes', { token, body: { title, body } });
  assert.equal(res.status, 201);
  return res.body;
}

describe('authentication', () => {
  it('logs in with valid credentials and rejects invalid ones', async () => {
    await setupOrg();
    assert.equal(typeof (await login('admin@acme.test')), 'string');

    const res = await call('POST', '/auth/login', {
      body: { email: 'admin@acme.test', password: 'wrong password' },
    });
    assert.equal(res.status, 401);
  });

  it('rejects requests without a valid token', async () => {
    await setupOrg();
    assert.equal((await call('GET', '/notes')).status, 401);
    assert.equal((await call('GET', '/notes', { token: 'bogus' })).status, 401);
  });
});

describe('roles', () => {
  it('viewers can read notes but not write them', async () => {
    const admin = await setupOrg();
    const viewer = await inviteAndLogin(admin, 'viewer@acme.test', 'viewer');
    const note = await createNote(admin, 'Plan');

    const list = await call('GET', '/notes', { token: viewer });
    assert.equal(list.status, 200);
    assert.equal(list.body.length, 1);

    assert.equal((await call('GET', `/notes/${note.id}`, { token: viewer })).status, 200);
    assert.equal(
      (await call('POST', '/notes', { token: viewer, body: { title: 'x' } })).status,
      403,
    );
    assert.equal(
      (await call('PATCH', `/notes/${note.id}`, { token: viewer, body: { title: 'x' } })).status,
      403,
    );
    assert.equal((await call('DELETE', `/notes/${note.id}`, { token: viewer })).status, 403);
  });

  it('editors can create, update and delete notes', async () => {
    const admin = await setupOrg();
    const editor = await inviteAndLogin(admin, 'editor@acme.test', 'editor');

    const note = await createNote(editor, 'Draft', 'first');
    const updated = await call('PATCH', `/notes/${note.id}`, {
      token: editor,
      body: { title: 'Final' },
    });
    assert.equal(updated.status, 200);
    assert.equal(updated.body.title, 'Final');
    assert.equal(updated.body.body, 'first');

    assert.equal((await call('DELETE', `/notes/${note.id}`, { token: editor })).status, 204);
    assert.equal((await call('GET', `/notes/${note.id}`, { token: editor })).status, 404);
  });

  it('only admins can invite users or change roles', async () => {
    const admin = await setupOrg();
    const editor = await inviteAndLogin(admin, 'editor@acme.test', 'editor');
    const viewerRes = await call('POST', '/users', {
      token: admin,
      body: { email: 'viewer@acme.test', role: 'viewer' },
    });

    const invite = await call('POST', '/users', {
      token: editor,
      body: { email: 'new@acme.test', role: 'viewer' },
    });
    assert.equal(invite.status, 403);

    const change = await call('PATCH', `/users/${viewerRes.body.user.id}`, {
      token: editor,
      body: { role: 'admin' },
    });
    assert.equal(change.status, 403);
  });

  it('admins can change a user role, which takes effect immediately', async () => {
    const admin = await setupOrg();
    const res = await call('POST', '/users', {
      token: admin,
      body: { email: 'bob@acme.test', role: 'viewer' },
    });
    const bob = await login('bob@acme.test', res.body.temporaryPassword);
    assert.equal((await call('POST', '/notes', { token: bob, body: { title: 'x' } })).status, 403);

    const changed = await call('PATCH', `/users/${res.body.user.id}`, {
      token: admin,
      body: { role: 'editor' },
    });
    assert.equal(changed.status, 200);
    assert.equal(changed.body.role, 'editor');

    assert.equal((await call('POST', '/notes', { token: bob, body: { title: 'x' } })).status, 201);
  });

  it('rejects inviting an email that is already registered', async () => {
    const admin = await setupOrg();
    await inviteAndLogin(admin, 'dup@acme.test', 'viewer');
    const res = await call('POST', '/users', {
      token: admin,
      body: { email: 'dup@acme.test', role: 'viewer' },
    });
    assert.equal(res.status, 409);
  });

  it('refuses to demote the last admin', async () => {
    const admin = await setupOrg();
    const me = ctx.store.authenticate(admin);
    const res = await call('PATCH', `/users/${me.id}`, {
      token: admin,
      body: { role: 'viewer' },
    });
    assert.equal(res.status, 409);
  });
});

describe('tenant isolation', () => {
  it('other organisations cannot see or modify notes', async () => {
    const adminA = await setupOrg('Acme');
    const adminB = await setupOrg('Globex');
    const note = await createNote(adminA, 'Secret');

    const list = await call('GET', '/notes', { token: adminB });
    assert.deepEqual(list.body, []);

    assert.equal((await call('GET', `/notes/${note.id}`, { token: adminB })).status, 404);
    assert.equal(
      (await call('PATCH', `/notes/${note.id}`, { token: adminB, body: { title: 'x' } })).status,
      404,
    );
    assert.equal((await call('DELETE', `/notes/${note.id}`, { token: adminB })).status, 404);

    const stillThere = await call('GET', `/notes/${note.id}`, { token: adminA });
    assert.equal(stillThere.body.title, 'Secret');
  });

  it('admins cannot change users in another organisation', async () => {
    const adminA = await setupOrg('Acme');
    const adminB = await setupOrg('Globex');
    const me = ctx.store.authenticate(adminA);
    const res = await call('PATCH', `/users/${me.id}`, { token: adminB, body: { role: 'viewer' } });
    assert.equal(res.status, 404);
  });
});

describe('search', () => {
  it('lists newest first and searches title and body case-insensitively', async () => {
    const admin = await setupOrg();
    await createNote(admin, 'Recipe for soup', 'carrots');
    await createNote(admin, 'Meeting', 'Discuss the RECIPE book');
    await createNote(admin, 'Shopping', 'milk');

    const all = await call('GET', '/notes', { token: admin });
    assert.deepEqual(
      all.body.map((n: { title: string }) => n.title),
      ['Shopping', 'Meeting', 'Recipe for soup'],
    );

    const found = await call('GET', '/notes?q=recipe', { token: admin });
    assert.deepEqual(
      found.body.map((n: { title: string }) => n.title),
      ['Meeting', 'Recipe for soup'],
    );
  });

  it('treats % and _ in queries literally', async () => {
    const admin = await setupOrg();
    await createNote(admin, '100% done');
    await createNote(admin, 'other');

    const percent = await call('GET', `/notes?q=${encodeURIComponent('%')}`, { token: admin });
    assert.equal(percent.body.length, 1);
    assert.equal(percent.body[0].title, '100% done');

    const underscore = await call('GET', `/notes?q=${encodeURIComponent('_')}`, { token: admin });
    assert.equal(underscore.body.length, 0);
  });
});

describe('validation', () => {
  it('rejects bad input with 400', async () => {
    const admin = await setupOrg();

    assert.equal((await call('POST', '/notes', { token: admin, body: { title: '  ' } })).status, 400);
    assert.equal(
      (await call('POST', '/orgs', { body: { organisation: 'X', email: 'not-an-email', password: PASSWORD } })).status,
      400,
    );
    assert.equal(
      (await call('POST', '/orgs', { body: { organisation: 'X', email: 'x@x.test', password: 'short' } })).status,
      400,
    );
    assert.equal(
      (await call('POST', '/users', { token: admin, body: { email: 'y@y.test', role: 'owner' } })).status,
      400,
    );
  });

  it('rejects malformed JSON and returns 404 for unknown routes', async () => {
    const admin = await setupOrg();
    const res = await fetch(`${ctx.base}/notes`, {
      method: 'POST',
      headers: { authorization: `Bearer ${admin}`, 'content-type': 'application/json' },
      body: '{not json',
    });
    assert.equal(res.status, 400);
    assert.equal((await call('GET', '/nothing-here', { token: admin })).status, 404);
  });
});

describe('persistence', () => {
  it('keeps organisations, users and tokens across store restarts', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'tenant-notes-'));
    const file = join(dir, 'notes.db');
    try {
      const first = new Store(file);
      first.createOrganisation('Acme', 'admin@acme.test', PASSWORD);
      const token = first.login('admin@acme.test', PASSWORD);
      first.close();

      const second = new Store(file);
      assert.equal(second.authenticate(token).email, 'admin@acme.test');
      second.close();
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
