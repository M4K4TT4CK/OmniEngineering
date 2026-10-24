import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, afterEach, beforeEach, describe, test } from "node:test";
import { createApp, deriveStatus, openStore, sign, SIGNATURE_HEADER, type PaymentEvent } from "../src/app.js";

const SECRET = "test-secret";
const tmpRoot = mkdtempSync(path.join(tmpdir(), "payment-webhook-"));
after(() => rmSync(tmpRoot, { recursive: true, force: true }));

let counter = 0;
function freshDbPath(): string {
  return path.join(tmpRoot, `db-${++counter}.sqlite`);
}

interface Running {
  baseUrl: string;
  dbPath: string;
  stop: () => Promise<void>;
}

async function start(dbPath = freshDbPath()): Promise<Running> {
  const { server } = createApp({ dbPath, webhookSecret: SECRET, maxBodyBytes: 1024 });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  return {
    baseUrl: `http://127.0.0.1:${port}`,
    dbPath,
    stop: () =>
      new Promise<void>((resolve, reject) => {
        server.closeAllConnections();
        server.close((err) => (err ? reject(err) : resolve()));
      }),
  };
}

function deliver(app: Running, body: string, signature: string | null = sign(body, SECRET)) {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (signature !== null) headers[SIGNATURE_HEADER] = signature;
  return fetch(`${app.baseUrl}/webhooks/payments`, { method: "POST", headers, body });
}

function send(app: Running, event: PaymentEvent) {
  return deliver(app, JSON.stringify(event));
}

async function getOrder(app: Running, orderId: string) {
  const res = await fetch(`${app.baseUrl}/orders/${encodeURIComponent(orderId)}`);
  return { status: res.status, body: await res.json() };
}

describe("webhook endpoint", () => {
  let app: Running;
  beforeEach(async () => {
    app = await start();
  });
  afterEach(async () => {
    await app.stop();
  });

  test("applies a payment and exposes the order status", async () => {
    const res = await send(app, { id: "evt_1", type: "payment.succeeded", order_id: "ord_1", amount: 1500 });
    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), { event_id: "evt_1", result: "applied" });

    const order = await getOrder(app, "ord_1");
    assert.equal(order.status, 200);
    assert.equal(order.body.status, "paid");
    assert.equal(order.body.amount_paid, 1500);
    assert.equal(order.body.amount_refunded, 0);
  });

  test("a retried delivery is acknowledged but applied only once", async () => {
    const event: PaymentEvent = { id: "evt_dup", type: "payment.succeeded", order_id: "ord_dup", amount: 1000 };
    assert.equal((await (await send(app, event)).json()).result, "applied");
    const retry = await send(app, event);
    assert.equal(retry.status, 200);
    assert.equal((await retry.json()).result, "duplicate");

    assert.equal((await getOrder(app, "ord_dup")).body.amount_paid, 1000);
  });

  test("a retry with re-serialized JSON (different key order/whitespace) is still a duplicate", async () => {
    await send(app, { id: "evt_fmt", type: "refund.created", order_id: "ord_fmt", amount: 200 });
    const res = await deliver(app, '{ "amount": 200, "order_id": "ord_fmt", "type": "refund.created", "id": "evt_fmt" }');
    assert.equal((await res.json()).result, "duplicate");
    assert.equal((await getOrder(app, "ord_fmt")).body.amount_refunded, 200);
  });

  test("concurrent duplicate deliveries are applied exactly once", async () => {
    const event: PaymentEvent = { id: "evt_race", type: "payment.succeeded", order_id: "ord_race", amount: 700 };
    const responses = await Promise.all(Array.from({ length: 20 }, () => send(app, event)));
    const results = await Promise.all(responses.map(async (r) => (await r.json()).result));
    assert.equal(results.filter((r) => r === "applied").length, 1);
    assert.equal(results.filter((r) => r === "duplicate").length, 19);
    assert.equal((await getOrder(app, "ord_race")).body.amount_paid, 700);
  });

  test("reusing an event id with a different payload is rejected and not applied", async () => {
    await send(app, { id: "evt_x", type: "payment.succeeded", order_id: "ord_x", amount: 100 });
    const res = await send(app, { id: "evt_x", type: "payment.succeeded", order_id: "ord_x", amount: 999 });
    assert.equal(res.status, 409);
    assert.equal((await getOrder(app, "ord_x")).body.amount_paid, 100);
  });

  test("rejects missing, malformed and wrong signatures without applying anything", async () => {
    const body = JSON.stringify({ id: "evt_sig", type: "payment.succeeded", order_id: "ord_sig", amount: 100 });
    assert.equal((await deliver(app, body, null)).status, 401);
    assert.equal((await deliver(app, body, "not-hex")).status, 401);
    assert.equal((await deliver(app, body, sign(body, "wrong-secret"))).status, 401);
    // Signature computed for a different body (tampered amount).
    const tampered = body.replace("100", "100000");
    assert.equal((await deliver(app, tampered, sign(body, SECRET))).status, 401);

    assert.equal((await getOrder(app, "ord_sig")).status, 404);
  });

  test("accepts a sha256= prefixed signature", async () => {
    const body = JSON.stringify({ id: "evt_pre", type: "payment.succeeded", order_id: "ord_pre", amount: 1 });
    assert.equal((await deliver(app, body, `sha256=${sign(body, SECRET)}`)).status, 200);
  });

  test("rejects signed but invalid payloads", async () => {
    const bad = [
      "not json",
      "[]",
      JSON.stringify({ type: "payment.succeeded", order_id: "o", amount: 1 }),
      JSON.stringify({ id: "e", type: "payment.unknown", order_id: "o", amount: 1 }),
      JSON.stringify({ id: "e", type: "payment.succeeded", amount: 1 }),
      JSON.stringify({ id: "e", type: "payment.succeeded", order_id: "o", amount: 0 }),
      JSON.stringify({ id: "e", type: "payment.succeeded", order_id: "o", amount: 10.5 }),
      JSON.stringify({ id: "e", type: "payment.succeeded", order_id: "o", amount: "10" }),
    ];
    for (const body of bad) {
      assert.equal((await deliver(app, body)).status, 400, body);
    }
  });

  test("rejects oversized bodies", async () => {
    const body = JSON.stringify({ id: "e", type: "payment.succeeded", order_id: "o", amount: 1, pad: "x".repeat(2000) });
    assert.equal((await deliver(app, body)).status, 413);
  });

  test("tracks partial and full refunds", async () => {
    await send(app, { id: "p1", type: "payment.succeeded", order_id: "ord_r", amount: 1000 });
    await send(app, { id: "r1", type: "refund.created", order_id: "ord_r", amount: 400 });
    assert.equal((await getOrder(app, "ord_r")).body.status, "partially_refunded");
    await send(app, { id: "r2", type: "refund.created", order_id: "ord_r", amount: 600 });
    const order = await getOrder(app, "ord_r");
    assert.equal(order.body.status, "refunded");
    assert.equal(order.body.amount_refunded, 1000);
  });

  test("final status does not depend on delivery order", async () => {
    // A late payment.failed must not downgrade a paid order.
    await send(app, { id: "a1", type: "payment.succeeded", order_id: "ord_a", amount: 500 });
    await send(app, { id: "a2", type: "payment.failed", order_id: "ord_a", amount: 500 });
    assert.equal((await getOrder(app, "ord_a")).body.status, "paid");

    // A refund arriving before its payment still ends up refunded.
    await send(app, { id: "b1", type: "refund.created", order_id: "ord_b", amount: 500 });
    await send(app, { id: "b2", type: "payment.succeeded", order_id: "ord_b", amount: 500 });
    assert.equal((await getOrder(app, "ord_b")).body.status, "refunded");
  });

  test("a failed payment followed by a successful retry is paid", async () => {
    await send(app, { id: "f1", type: "payment.failed", order_id: "ord_f", amount: 300 });
    assert.equal((await getOrder(app, "ord_f")).body.status, "failed");
    await send(app, { id: "f2", type: "payment.succeeded", order_id: "ord_f", amount: 300 });
    const order = await getOrder(app, "ord_f");
    assert.equal(order.body.status, "paid");
    assert.equal(order.body.failed_attempts, 1);
  });

  test("unknown routes, methods and orders", async () => {
    assert.equal((await getOrder(app, "missing")).status, 404);
    assert.equal((await fetch(`${app.baseUrl}/webhooks/payments`)).status, 405);
    assert.equal((await fetch(`${app.baseUrl}/nope`)).status, 404);
  });
});

describe("persistence", () => {
  test("deduplication survives a restart", async () => {
    const event: PaymentEvent = { id: "evt_persist", type: "payment.succeeded", order_id: "ord_p", amount: 250 };
    const first = await start();
    await send(first, event);
    await first.stop();

    const second = await start(first.dbPath);
    try {
      assert.equal((await (await send(second, event)).json()).result, "duplicate");
      assert.equal((await getOrder(second, "ord_p")).body.amount_paid, 250);
    } finally {
      await second.stop();
    }
  });

  test("two connections to the same database apply an event once", () => {
    const dbPath = freshDbPath();
    const a = openStore(dbPath);
    const b = openStore(dbPath);
    try {
      const event: PaymentEvent = { id: "evt_shared", type: "payment.succeeded", order_id: "ord_s", amount: 10 };
      assert.equal(a.applyEvent(event), "applied");
      assert.equal(b.applyEvent(event), "duplicate");
      assert.equal(b.getOrder("ord_s")?.amount_paid, 10);
    } finally {
      a.close();
      b.close();
    }
  });
});

describe("deriveStatus", () => {
  test("covers every status", () => {
    assert.equal(deriveStatus(0, 0, 0), "pending");
    assert.equal(deriveStatus(0, 0, 2), "failed");
    assert.equal(deriveStatus(0, 100, 0), "pending");
    assert.equal(deriveStatus(100, 0, 1), "paid");
    assert.equal(deriveStatus(100, 30, 0), "partially_refunded");
    assert.equal(deriveStatus(100, 100, 0), "refunded");
  });
});
