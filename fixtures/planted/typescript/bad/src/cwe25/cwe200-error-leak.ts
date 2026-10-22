// CWE-200: the Express error handler returns the stack and the raw error to the client.
import type { NextFunction, Request, Response } from "express";

export function errorHandler(err: Error, _req: Request, res: Response, _next: NextFunction): void {
  res.status(500).json({ message: err.message, stack: err.stack }); // gap: CWE-200 - nothing in this build reports it (docs/cwe-top25.md)
}
