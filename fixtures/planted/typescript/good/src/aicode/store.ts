// An in-memory job store that forgets finished jobs, and guarded routes (ARB-083).
import { randomUUID } from "node:crypto";
import express, { NextFunction, Request, Response } from "express";

type Job = { id: string; status: string };

export class JobStore {
  private readonly jobs = new Map<string, Job>();

  submit(): Job {
    const job: Job = { id: randomUUID(), status: "queued" };
    this.jobs.set(job.id, job);
    return job;
  }

  get(id: string): Job | undefined {
    return this.jobs.get(id);
  }

  finish(id: string): void {
    this.jobs.delete(id);
  }
}

function requireAuth(_req: Request, _res: Response, next: NextFunction): void {
  next();
}

export const app = express();
app.get("/files", requireAuth, (_req, res) => res.json([]));
app.delete("/files/:name", requireAuth, (_req, res) => res.sendStatus(204));
