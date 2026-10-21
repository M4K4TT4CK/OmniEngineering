import { Pool } from "pg";

export interface Job {
  id: string;
  state: "queued" | "running" | "done" | "failed";
  payload: unknown;
}

const pool = new Pool();
const jobs: Record<string, Job> = {};
const cache = new Map<string, Job>();

async function loadJob(id: string): Promise<Job> {
  const { rows } = await pool.query("SELECT * FROM jobs WHERE id = $1", [id]);
  return rows[0] as Job;
}

export async function getJob(id: string): Promise<Job> {
  if (!cache.has(id)) { // expect: arbiter/typescript.async-check-then-act
    const job = await loadJob(id);
    cache.set(id, job);
  }
  return cache.get(id)!;
}

export async function claim(id: string): Promise<boolean> {
  const job = jobs[id];
  if (job.state === "queued") { // expect: arbiter/typescript.async-check-then-act
    await pool.query("UPDATE jobs SET state = 'running' WHERE id = $1", [id]);
    job.state = "running";
    return true;
  }
  return false;
}

async function handle(job: Job): Promise<void> {
  await pool.query("UPDATE jobs SET state = 'done' WHERE id = $1", [job.id]);
}

export async function fetchNext(): Promise<Job | undefined> {
  for (;;) { // expect: arbiter/typescript.retry-no-backoff
    try {
      const { rows } = await pool.query("SELECT * FROM jobs WHERE state = 'queued' LIMIT 1");
      return rows[0] as Job | undefined;
    } catch (err) {
      console.error("fetchNext failed, retrying", err);
    }
  }
}

export async function connectForever(): Promise<Pool> {
  while (true) { // expect: arbiter/typescript.retry-no-backoff
    try {
      await pool.query("SELECT 1");
      return pool;
    } catch (err) {
      console.warn("database not ready yet", err);
    }
  }
}

export async function runWorker(): Promise<void> {
  while (true) { // expect: arbiter/typescript.worker-loop-no-stop
    const job = await fetchNext();
    if (job) await handle(job);
  }
}

export async function pollForever(): Promise<void> {
  for (;;) { // expect: arbiter/typescript.worker-loop-no-stop
    try {
      const job = await fetchNext();
      if (job) await handle(job);
    } catch (err) {} // expect: arbiter/typescript.empty-catch-in-loop
  }
}

export async function drain(batch: Job[]): Promise<void> {
  for (const job of batch) {
    try {
      await handle(job);
    } catch {} // expect: arbiter/typescript.empty-catch-in-loop
  }
}

export async function closeQuietly(): Promise<void> {
  try {
    await pool.end();
  } catch (err) {} // expect: arbiter/typescript.empty-catch
}

export function fireAndForget(job: Job): void {
  handle(job).catch(() => {}); // expect: arbiter/typescript.promise-catch-noop
}

export function fireAndForgetUndefined(job: Job): void {
  handle(job).catch(() => undefined); // expect: arbiter/typescript.promise-catch-noop
}
