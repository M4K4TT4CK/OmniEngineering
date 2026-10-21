import axios from "axios";
import https from "node:https";

const BASE = process.env.UPSTREAM_URL ?? "https://upstream.example";

export async function getProfile(id: string): Promise<unknown> {
  const res = await fetch(`${BASE}/profiles/${id}`); // expect: arbiter/typescript.fetch-no-timeout
  return res.json();
}

export async function postEvent(event: object): Promise<void> {
  await fetch(`${BASE}/events`, { method: "POST", body: JSON.stringify(event) }); // expect: arbiter/typescript.fetch-no-timeout
}

export async function getRates(): Promise<unknown> {
  const res = await axios.get(`${BASE}/rates`); // expect: arbiter/typescript.axios-no-timeout
  return res.data;
}

export async function postOrder(order: object): Promise<unknown> {
  const res = await axios.post(`${BASE}/orders`, order, { headers: { "x-source": "svc" } }); // expect: arbiter/typescript.axios-no-timeout
  return res.data;
}

export function ping(): Promise<number> {
  return new Promise((resolve, reject) => {
    const req = https.get(`${BASE}/health`, (res) => resolve(res.statusCode ?? 0)); // expect: arbiter/typescript.http-request-no-timeout
    req.on("error", reject);
  });
}

export function internalAgent(): https.Agent {
  return new https.Agent({ rejectUnauthorized: false }); // expect: arbiter/authored.security-check-disabled
}
