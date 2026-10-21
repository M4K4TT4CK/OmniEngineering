import { Pool } from "pg";

export interface Job {
  id: string;
  state: "queued" | "running" | "done" | "failed";
  payload: unknown;
}

const pool = new Pool();
const jobs: Record<string, Job> = {};
const cache = new Map<string, Promise<Job>>();
const log = (msg: string, err?: unknown) => console.error(msg, err);
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

async function loadJob(id: string): Promise<Job> {
  const { rows } = await pool.query("SELECT * FROM jobs WHERE id = $1", [id]);
  return rows[0] as Job;
}

export async function getJob(id: string): Promise<Job> {
  // The pending promise is stored before anything awaits, so two concurrent
  // callers share one load instead of both missing the cache.
  if (!cache.has(id)) {
    cache.set(id, loadJob(id));
  }
  return cache.get(id)!;
}

export async function claim(id: string): Promise<boolean> {
  const job = jobs[id];
  // Check and change with no await between them: nothing can interleave.
  if (job.state === "queued") {
    job.state = "running";
    await pool.query("UPDATE jobs SET state = 'running' WHERE id = $1", [id]);
    return true;
  }
  return false;
}

async function handle(job: Job): Promise<void> {
  await pool.query("UPDATE jobs SET state = 'done' WHERE id = $1", [job.id]);
}

export async function fetchNext(attempts = 5): Promise<Job | undefined> {
  for (let attempt = 1; ; attempt++) {
    try {
      const { rows } = await pool.query("SELECT * FROM jobs WHERE state = 'queued' LIMIT 1");
      return rows[0] as Job | undefined;
    } catch (err) {
      if (attempt >= attempts) throw err;
      log(`fetchNext attempt ${attempt} failed`, err);
      await sleep(Math.min(30_000, 200 * 2 ** attempt) + Math.random() * 100);
    }
  }
}

export async function connectUntilReady(signal: AbortSignal): Promise<Pool> {
  for (;;) {
    try {
      await pool.query("SELECT 1");
      return pool;
    } catch (err) {
      if (signal.aborted) throw err;
      log("database not ready yet", err);
      await sleep(1_000);
    }
  }
}

export async function runWorker(signal: AbortSignal): Promise<void> {
  while (!signal.aborted) {
    const job = await fetchNext();
    if (job) await handle(job);
  }
}

export async function pollUntilStopped(isRunning: () => boolean): Promise<void> {
  for (;;) {
    if (!isRunning()) break;
    try {
      const job = await fetchNext();
      if (job) await handle(job);
    } catch (err) {
      log("poll iteration failed", err);
    }
  }
}

export async function drain(batch: Job[]): Promise<number> {
  let failed = 0;
  for (const job of batch) {
    try {
      await handle(job);
    } catch (err) {
      failed++;
      log(`job ${job.id} failed`, err);
    }
  }
  return failed;
}

export async function closeQuietly(): Promise<void> {
  try {
    await pool.end();
  } catch (err) {
    log("pool.end failed during shutdown", err);
  }
}

export function fireAndForget(job: Job): void {
  handle(job).catch((err) => log(`background job ${job.id} failed`, err));
}
