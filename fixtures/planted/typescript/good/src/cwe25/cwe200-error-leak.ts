// CWE-200: the Express error handler logs the detail and returns an opaque incident id.
import { randomUUID } from "node:crypto";
import type { NextFunction, Request, Response } from "express";
import { Pool } from "pg";

const pool = new Pool();

export function errorHandler(err: Error, _req: Request, res: Response, _next: NextFunction): void {
  const incident = randomUUID();
  console.error(incident, err);
  res.status(500).json({ message: "internal error", incident });
}

// The stack goes to the client only on a developer's machine.
export function devErrorHandler(err: Error, _req: Request, res: Response, _next: NextFunction): void {
  if (process.env.NODE_ENV === "production") {
    res.status(500).json({ message: "internal error" });
    return;
  }
  res.status(500).json({ message: err.message, stack: err.stack });
}

export async function listOrders(req: Request, res: Response): Promise<void> {
  try {
    const { rows } = await pool.query("SELECT id, total FROM orders WHERE owner = $1", [req.params.owner]);
    res.json(rows);
  } catch (err) {
    console.error("list orders", err);
    res.status(500).json({ error: "internal error" });
  }
}
