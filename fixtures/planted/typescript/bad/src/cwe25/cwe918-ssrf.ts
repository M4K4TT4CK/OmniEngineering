// CWE-918: link-preview endpoint fetches whatever URL the client sends.
import axios from "axios";
import type { Request, Response } from "express";

export async function preview(req: Request, res: Response): Promise<void> {
  const target = String(req.query.url);
  const upstream = await fetch(target, { signal: AbortSignal.timeout(5000) }); // expect: arbiter/typescript.ssrf-request-url
  res.json({ status: upstream.status, body: (await upstream.text()).slice(0, 200) });
}

export async function proxyImage(req: Request, res: Response): Promise<void> {
  const upstream = await axios.get(req.query.src as string, { responseType: "arraybuffer", timeout: 5000 }); // expect: arbiter/typescript.ssrf-request-url
  res.type("png").send(upstream.data);
}

export async function webhookTest(req: Request, res: Response): Promise<void> {
  const { callbackUrl } = req.body;
  try {
    const r = await fetch(callbackUrl, { method: "POST", signal: AbortSignal.timeout(5000) }); // expect: arbiter/typescript.ssrf-request-url
    res.json({ delivered: r.ok });
  } catch {
    res.sendStatus(502);
  }
}
