// CWE-200: the Express error handler logs the detail and returns an opaque incident id.
import { randomUUID } from "node:crypto";
import type { NextFunction, Request, Response } from "express";

export function errorHandler(err: Error, _req: Request, res: Response, _next: NextFunction): void {
  const incident = randomUUID();
  console.error(incident, err);
  res.status(500).json({ message: "internal error", incident });
}
