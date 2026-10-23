import path from 'node:path';
import type { AddressInfo } from 'node:net';
import { afterEach, describe, expect, it } from 'vitest';
import { validateJob } from '../src/jobs';
import { JobQueue } from '../src/queue';
import { createServer } from '../src/server';

const workerFile = path.join(__dirname, 'fake-worker.js');

let queue: JobQueue | undefined;
afterEach(async () => {
  await queue?.close();
  queue = undefined;
});

function makeQueue(opts: { concurrency?: number } = {}) {
  queue = new JobQueue({ workerFile, retryDelayMs: 10, ...opts });
  return queue;
}

async function waitFor(q: JobQueue, id: string, statuses = ['completed', 'failed']) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const job = q.get(id)!;
    if (statuses.includes(job.status)) return job;
    await new Promise((r) => setTimeout(r, 5));
  }
  throw new Error(`job ${id} did not finish: ${JSON.stringify(q.get(id))}`);
}

describe('JobQueue', () => {
  it('runs a job and stores its result', async () => {
    const q = makeQueue();
    const { id } = q.submit('echo', { a: 1 });
    const job = await waitFor(q, id);
    expect(job.status).toBe('completed');
    expect(job.attempts).toBe(1);
    expect(job.result).toMatchObject({ payload: { a: 1 } });
  });

  it('retries a flaky job until it succeeds', async () => {
    const q = makeQueue();
    const { id } = q.submit('flaky', { succeedOn: 3 });
    const job = await waitFor(q, id);
    expect(job.status).toBe('completed');
    expect(job.attempts).toBe(3);
  });

  it('fails after the first attempt plus three retries', async () => {
    const q = makeQueue();
    const { id } = q.submit('fail', {});
    const job = await waitFor(q, id);
    expect(job.status).toBe('failed');
    expect(job.attempts).toBe(4);
    expect(job.error).toBe('always fails');
  });

  it('retries when a worker thread dies and replaces the worker', async () => {
    const q = makeQueue({ concurrency: 1 });
    const crashed = q.submit('crash', {});
    expect((await waitFor(q, crashed.id)).status).toBe('failed');
    expect(q.get(crashed.id)!.attempts).toBe(4);
    // The single worker was replaced, so later jobs still run.
    const { id } = q.submit('echo', {});
    expect((await waitFor(q, id)).status).toBe('completed');
  });

  it('processes jobs concurrently up to the pool size', async () => {
    const q = makeQueue({ concurrency: 3 });
    const ids = [1, 2, 3].map(() => q.submit('slow', { ms: 150 }).id);
    const jobs = await Promise.all(ids.map((id) => waitFor(q, id)));
    const starts = jobs.map((j) => (j.result as any).start);
    const ends = jobs.map((j) => (j.result as any).end);
    // All three overlap: the last one starts before the first one ends.
    expect(Math.max(...starts)).toBeLessThan(Math.min(...ends));
  });

  it('queues work beyond the pool size', async () => {
    const q = makeQueue({ concurrency: 1 });
    const a = q.submit('slow', { ms: 50 });
    const b = q.submit('slow', { ms: 50 });
    const [ja, jb] = await Promise.all([waitFor(q, a.id), waitFor(q, b.id)]);
    expect((jb.result as any).start).toBeGreaterThanOrEqual((ja.result as any).end - 1);
  });
});

describe('validateJob', () => {
  it('accepts valid payloads', () => {
    expect(validateJob('resize-image', { path: '/tmp/a.png', width: 100 })).toBeNull();
    expect(validateJob('send-email', { to: 'a@b.co', subject: 's', body: 'b' })).toBeNull();
  });

  it('rejects invalid payloads', () => {
    expect(validateJob('nope', {})).toMatch(/type/);
    expect(validateJob('resize-image', { path: '' })).toMatch(/path/);
    expect(validateJob('resize-image', { path: 'a.png', width: 0 })).toMatch(/width/);
    expect(validateJob('send-email', { to: 'bad', subject: 's', body: 'b' })).toMatch(/to/);
    expect(validateJob('send-email', { to: 'a@b.co', subject: 's' })).toMatch(/body/);
    expect(validateJob('send-email', [])).toMatch(/object/);
  });
});

describe('HTTP API', () => {
  async function start() {
    const q = makeQueue();
    const server = createServer(q);
    await new Promise<void>((resolve) => server.listen(0, resolve));
    const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    return { q, base, stop: () => new Promise((r) => server.close(r)) };
  }

  const post = (base: string, body: string) =>
    fetch(`${base}/jobs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });

  it('accepts a job and reports its status and result', async () => {
    const { q, base, stop } = await start();
    try {
      const res = await post(base, JSON.stringify({
        type: 'send-email',
        payload: { to: 'a@b.co', subject: 'hi', body: 'there' },
      }));
      expect(res.status).toBe(202);
      const { id } = await res.json();
      await waitFor(q, id);

      const status = await fetch(`${base}/jobs/${id}`);
      expect(status.status).toBe(200);
      expect(await status.json()).toMatchObject({ id, type: 'send-email', status: 'completed', attempts: 1 });
    } finally {
      await stop();
    }
  });

  it('rejects bad requests and unknown ids', async () => {
    const { base, stop } = await start();
    try {
      expect((await post(base, 'not json')).status).toBe(400);
      expect((await post(base, JSON.stringify({ type: 'bogus', payload: {} }))).status).toBe(400);
      expect((await post(base, JSON.stringify({ type: 'resize-image', payload: {} }))).status).toBe(400);
      expect((await fetch(`${base}/jobs/missing`)).status).toBe(404);
    } finally {
      await stop();
    }
  });
});
