import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { Worker } from 'node:worker_threads';
import type { Job } from './jobs';

export interface QueueOptions {
  /** Number of worker threads. */
  concurrency?: number;
  /** Retries after the first failed attempt (so total attempts = maxRetries + 1). */
  maxRetries?: number;
  /** Delay before retry n is retryDelayMs * n. */
  retryDelayMs?: number;
  /** Script run in each worker thread; must follow the protocol in worker.ts. */
  workerFile?: string;
}

export class JobQueue {
  private readonly jobs = new Map<string, Job>();
  private readonly pending: string[] = [];
  private readonly idle: Worker[] = [];
  private readonly workers = new Set<Worker>();
  private readonly running = new Map<Worker, string>();
  private readonly timers = new Set<NodeJS.Timeout>();
  private closed = false;

  private readonly concurrency: number;
  private readonly maxRetries: number;
  private readonly retryDelayMs: number;
  private readonly workerFile: string;

  constructor(opts: QueueOptions = {}) {
    this.concurrency = opts.concurrency ?? 4;
    this.maxRetries = opts.maxRetries ?? 3;
    this.retryDelayMs = opts.retryDelayMs ?? 1000;
    this.workerFile = opts.workerFile ?? path.join(__dirname, 'worker.js');
    for (let i = 0; i < this.concurrency; i++) this.spawn();
  }

  submit(type: string, payload: unknown): Job {
    if (this.closed) throw new Error('queue is closed');
    const now = new Date().toISOString();
    const job: Job = {
      id: randomUUID(),
      type,
      payload,
      status: 'queued',
      attempts: 0,
      createdAt: now,
      updatedAt: now,
    };
    this.jobs.set(job.id, job);
    this.pending.push(job.id);
    this.dispatch();
    return job;
  }

  get(id: string): Job | undefined {
    return this.jobs.get(id);
  }

  async close(): Promise<void> {
    this.closed = true;
    for (const t of this.timers) clearTimeout(t);
    this.timers.clear();
    const all = [...this.workers];
    this.workers.clear();
    this.idle.length = 0;
    this.running.clear();
    await Promise.all(all.map((w) => w.terminate()));
  }

  private spawn(): void {
    const worker = new Worker(this.workerFile);
    this.workers.add(worker);
    this.idle.push(worker);
    worker.on('message', (msg: { id: string; ok: boolean; result?: unknown; error?: string }) => {
      if (this.running.get(worker) !== msg.id) return;
      this.running.delete(worker);
      this.idle.push(worker);
      const job = this.jobs.get(msg.id)!;
      if (msg.ok) {
        this.update(job, { status: 'completed', result: msg.result, error: undefined });
      } else {
        this.attemptFailed(job, msg.error ?? 'unknown error');
      }
      this.dispatch();
    });
    // 'error' (uncaught exception) is followed by 'exit'; handle once.
    worker.on('error', (err) => this.workerDied(worker, err.message));
    worker.on('exit', (code) => this.workerDied(worker, `worker exited with code ${code}`));
  }

  private workerDied(worker: Worker, reason: string): void {
    if (!this.workers.delete(worker)) return; // already handled, or closing
    const idleAt = this.idle.indexOf(worker);
    if (idleAt >= 0) this.idle.splice(idleAt, 1);
    const jobId = this.running.get(worker);
    this.running.delete(worker);
    void worker.terminate();
    if (jobId) this.attemptFailed(this.jobs.get(jobId)!, reason);
    if (!this.closed) {
      this.spawn();
      this.dispatch();
    }
  }

  private dispatch(): void {
    while (!this.closed && this.idle.length > 0 && this.pending.length > 0) {
      const worker = this.idle.shift()!;
      const job = this.jobs.get(this.pending.shift()!)!;
      this.update(job, { status: 'running', attempts: job.attempts + 1 });
      this.running.set(worker, job.id);
      worker.postMessage({ id: job.id, type: job.type, payload: job.payload, attempt: job.attempts });
    }
  }

  private attemptFailed(job: Job, error: string): void {
    if (job.attempts > this.maxRetries) {
      this.update(job, { status: 'failed', error });
      return;
    }
    this.update(job, { status: 'queued', error });
    const timer = setTimeout(() => {
      this.timers.delete(timer);
      this.pending.push(job.id);
      this.dispatch();
    }, this.retryDelayMs * job.attempts);
    this.timers.add(timer);
  }

  private update(job: Job, patch: Partial<Job>): void {
    Object.assign(job, patch, { updatedAt: new Date().toISOString() });
  }
}
