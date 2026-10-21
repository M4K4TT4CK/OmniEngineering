import axios from "axios";
import https from "node:https";

const BASE = process.env.UPSTREAM_URL ?? "https://upstream.example";
const TIMEOUT_MS = 5_000;
const client = axios.create({ baseURL: BASE, timeout: TIMEOUT_MS });

export async function getProfile(id: string): Promise<unknown> {
  const res = await fetch(`${BASE}/profiles/${id}`, { signal: AbortSignal.timeout(TIMEOUT_MS) });
  return res.json();
}

export async function postEvent(event: object): Promise<void> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    await fetch(`${BASE}/events`, { method: "POST", body: JSON.stringify(event), signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

export async function getWith(init: RequestInit): Promise<unknown> {
  // Options passed by name may carry a signal; not reported.
  const res = await fetch(`${BASE}/rates`, init);
  return res.json();
}

export async function getRates(): Promise<unknown> {
  const res = await client.get("/rates");
  return res.data;
}

export async function postOrder(order: object): Promise<unknown> {
  const res = await axios.post(`${BASE}/orders`, order, { timeout: TIMEOUT_MS, headers: { "x-source": "svc" } });
  return res.data;
}

export function ping(): Promise<number> {
  return new Promise((resolve, reject) => {
    const req = https.get(`${BASE}/health`, { timeout: TIMEOUT_MS }, (res) => resolve(res.statusCode ?? 0));
    req.on("timeout", () => req.destroy(new Error("upstream health check timed out")));
    req.on("error", reject);
  });
}

export function internalAgent(): https.Agent {
  return new https.Agent({ keepAlive: true });
}
