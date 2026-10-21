// CWE-918: link-preview endpoint fetches only https URLs on allowlisted hosts, no redirects.
import type { Request, Response } from "express";

const ALLOWED_HOSTS = new Set(["github.com", "gitlab.com", "developer.mozilla.org"]);

export async function preview(req: Request, res: Response): Promise<void> {
  const target = new URL(String(req.query.url));
  if (target.protocol !== "https:" || !ALLOWED_HOSTS.has(target.hostname)) {
    res.sendStatus(400);
    return;
  }
  const upstream = await fetch(target, { redirect: "error", signal: AbortSignal.timeout(5000) });
  res.json({ status: upstream.status, body: (await upstream.text()).slice(0, 200) });
}
