import request from 'supertest';
import { beforeEach, describe, expect, it } from 'vitest';
import { createApp } from '../src/app';

let clock: number;
let app: ReturnType<typeof createApp>['app'];

beforeEach(() => {
  clock = 1_000_000;
  app = createApp({ dbPath: ':memory:', now: () => clock }).app;
});

async function signup(username: string, password = 'password123'): Promise<string> {
  const res = await request(app).post('/signup').send({ username, password });
  expect(res.status).toBe(201);
  return res.body.token;
}
const bearer = (t: string) => ({ Authorization: `Bearer ${t}` });

describe('accounts', () => {
  it('signs up, rejects duplicates (case-insensitive) and bad input', async () => {
    await signup('alice');
    const dup = await request(app).post('/signup').send({ username: 'ALICE', password: 'password123' });
    expect(dup.status).toBe(409);
    expect((await request(app).post('/signup').send({ username: 'bob', password: 'short' })).status).toBe(400);
    expect((await request(app).post('/signup').send({ username: 'b b', password: 'password123' })).status).toBe(400);
  });

  it('logs in with correct password only', async () => {
    await signup('alice');
    const ok = await request(app).post('/login').send({ username: 'alice', password: 'password123' });
    expect(ok.status).toBe(200);
    expect(ok.body.token).toMatch(/^[0-9a-f]{64}$/);
    expect((await request(app).post('/login').send({ username: 'alice', password: 'wrongpass1' })).status).toBe(401);
    expect((await request(app).post('/login').send({ username: 'nobody', password: 'password123' })).status).toBe(401);
  });

  it('requires a valid, unexpired token', async () => {
    expect((await request(app).get('/notes')).status).toBe(401);
    expect((await request(app).get('/notes').set(bearer('bogus'))).status).toBe(401);
    const t = await signup('alice');
    expect((await request(app).get('/notes').set(bearer(t))).status).toBe(200);
    clock += 25 * 60 * 60 * 1000;
    expect((await request(app).get('/notes').set(bearer(t))).status).toBe(401);
  });

  it('returns 400 for malformed JSON', async () => {
    const res = await request(app).post('/login').set('Content-Type', 'application/json').send('{bad');
    expect(res.status).toBe(400);
  });
});

describe('notes', () => {
  it('supports create, get, update, delete', async () => {
    const t = await signup('alice');
    const created = await request(app).post('/notes').set(bearer(t)).send({ title: 'Groceries', body: 'milk' });
    expect(created.status).toBe(201);
    const id = created.body.id;

    clock += 1000;
    const updated = await request(app).put(`/notes/${id}`).set(bearer(t)).send({ body: 'milk, eggs' });
    expect(updated.status).toBe(200);
    expect(updated.body).toMatchObject({ title: 'Groceries', body: 'milk, eggs' });
    expect(updated.body.updatedAt).toBeGreaterThan(updated.body.createdAt);

    expect((await request(app).get(`/notes/${id}`).set(bearer(t))).body.body).toBe('milk, eggs');
    expect((await request(app).delete(`/notes/${id}`).set(bearer(t))).status).toBe(204);
    expect((await request(app).get(`/notes/${id}`).set(bearer(t))).status).toBe(404);
  });

  it('validates input', async () => {
    const t = await signup('alice');
    expect((await request(app).post('/notes').set(bearer(t)).send({ body: 'x' })).status).toBe(400);
    expect((await request(app).post('/notes').set(bearer(t)).send({ title: 5 })).status).toBe(400);
    const { body } = await request(app).post('/notes').set(bearer(t)).send({ title: 'a' });
    expect((await request(app).put(`/notes/${body.id}`).set(bearer(t)).send({})).status).toBe(400);
  });

  it('lists and searches only own notes, treating wildcards literally', async () => {
    const a = await signup('alice');
    const b = await signup('bob');
    await request(app).post('/notes').set(bearer(a)).send({ title: 'Meeting', body: 'discuss roadmap' });
    await request(app).post('/notes').set(bearer(a)).send({ title: 'Discount', body: '50% off' });
    await request(app).post('/notes').set(bearer(b)).send({ title: 'Roadmap secret' });

    const all = await request(app).get('/notes').set(bearer(a));
    expect(all.body.items).toHaveLength(2);

    const q = await request(app).get('/notes').query({ q: 'roadmap' }).set(bearer(a));
    expect(q.body.items.map((n: any) => n.title)).toEqual(['Meeting']);

    const pct = await request(app).get('/notes').query({ q: '%' }).set(bearer(a));
    expect(pct.body.items.map((n: any) => n.title)).toEqual(['Discount']);

    const page = await request(app).get('/notes').query({ limit: 1, offset: 1 }).set(bearer(a));
    expect(page.body.items).toHaveLength(1);
  });

  it("cannot read, modify or delete another user's note", async () => {
    const a = await signup('alice');
    const b = await signup('bob');
    const { body } = await request(app).post('/notes').set(bearer(a)).send({ title: 'private' });
    expect((await request(app).get(`/notes/${body.id}`).set(bearer(b))).status).toBe(404);
    expect((await request(app).put(`/notes/${body.id}`).set(bearer(b)).send({ title: 'x' })).status).toBe(404);
    expect((await request(app).delete(`/notes/${body.id}`).set(bearer(b))).status).toBe(404);
    expect((await request(app).get(`/notes/${body.id}`).set(bearer(a))).body.title).toBe('private');
  });
});

describe('rate limiting', () => {
  it('allows 60 requests per minute per user, then 429, then resets', async () => {
    const a = await signup('alice');
    const b = await signup('bob');
    for (let i = 0; i < 60; i++) {
      expect((await request(app).get('/notes').set(bearer(a))).status).toBe(200);
    }
    const blocked = await request(app).get('/notes').set(bearer(a));
    expect(blocked.status).toBe(429);
    expect(blocked.headers['retry-after']).toBeDefined();

    // Another user is unaffected.
    expect((await request(app).get('/notes').set(bearer(b))).status).toBe(200);

    clock += 60_000;
    expect((await request(app).get('/notes').set(bearer(a))).status).toBe(200);
  });
});
