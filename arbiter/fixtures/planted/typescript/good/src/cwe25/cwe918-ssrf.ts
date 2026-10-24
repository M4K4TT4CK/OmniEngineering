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

export async function userProfile(req: Request, res: Response): Promise<void> {
  // The host is fixed; the request only picks a path on it.
  const r = await fetch(`https://api.example.com/users/${encodeURIComponent(req.params.id)}`, { signal: AbortSignal.timeout(5000) });
  res.json(await r.json());
}

export async function webhookTest(req: Request, res: Response): Promise<void> {
  const { callbackUrl } = req.body;
  if (!isAllowedWebhook(callbackUrl)) {
    res.sendStatus(400);
    return;
  }
  const r = await fetch(callbackUrl, { method: "POST", redirect: "error", signal: AbortSignal.timeout(5000) });
  res.json({ delivered: r.ok });
}

function isAllowedWebhook(raw: string): boolean {
  const u = new URL(raw);
  return u.protocol === "https:" && ALLOWED_HOSTS.has(u.hostname);
}
