// CWE-200: the Express error handler returns the stack and the raw error to the client.
import type { NextFunction, Request, Response } from "express";
import { Pool } from "pg";

const pool = new Pool();

export function errorHandler(err: Error, _req: Request, res: Response, _next: NextFunction): void {
  res.status(500).json({ message: err.message, stack: err.stack }); // expect: arbiter/typescript.error-stack-in-response
}

export async function listOrders(req: Request, res: Response): Promise<void> {
  try {
    const { rows } = await pool.query("SELECT id, total FROM orders WHERE owner = $1", [req.params.owner]);
    res.json(rows);
  } catch (err) {
    res.status(500).json(err); // expect: arbiter/typescript.error-object-in-response
  }
}
