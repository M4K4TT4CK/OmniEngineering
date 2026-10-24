// Webhook forwarding as model-written receivers ship it (ARB-083).
type Merchant = { callback_url: string };

export async function deliver(merchant: Merchant, body: string): Promise<boolean> {
  const res = await fetch(merchant.callback_url, { // expect: arbiter/typescript.ssrf-stored-callback
    method: "POST",
    headers: { "content-type": "application/json" },
    body,
    signal: AbortSignal.timeout(5000),
  });
  return res.ok;
}
