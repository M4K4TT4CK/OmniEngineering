// CWE-918: link-preview endpoint fetches whatever URL the client sends.
import type { Request, Response } from "express";

export async function preview(req: Request, res: Response): Promise<void> {
  const target = String(req.query.url);
  const upstream = await fetch(target, { signal: AbortSignal.timeout(5000) }); // gap: CWE-918 - nothing in this build reports it (docs/cwe-top25.md)
  res.json({ status: upstream.status, body: (await upstream.text()).slice(0, 200) });
}
