import Database from "better-sqlite3";
import request from "supertest";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, sign } from "./app.js";

const SECRET = "whsec_test";
const TOKEN = "admin-token";
const auth = { Authorization: `Bearer ${TOKEN}` };

function setup(fetchImpl?: typeof fetch) {
  const fetchFn = vi.fn(fetchImpl ?? (async () => new Response("ok", { status: 200 })));
  const { app, flush } = createApp({
    db: new Database(":memory:"),
    webhookSecret: SECRET,
    adminToken: TOKEN,
    fetchFn: fetchFn as unknown as typeof fetch,
    retryDelayMs: 1,
  });
  return { app, flush, fetchFn };
}

function post(app: any, event: unknown, signature?: string) {
  const body = JSON.stringify(event);
  return request(app)
    .post("/webhooks/payments")
    .set("Content-Type", "application/json")
    .set("X-Signature", signature ?? sign(body, SECRET))
    .send(body);
}

const evt = (id: string, type = "payment.succeeded") => ({
  id,
  type,
  merchant_id: "m1",
  data: { amount: 1000, currency: "usd" },
});

describe("webhook receiver", () => {
  let ctx: ReturnType<typeof setup>;

  beforeEach(async () => {
    ctx = setup();
    await request(ctx.app).put("/merchants/m1").set(auth).send({ callback_url: "https://merchant.test/hook" });
  });

  it("rejects missing, malformed and wrong signatures", async () => {
    const body = JSON.stringify(evt("e1"));
    await request(ctx.app).post("/webhooks/payments").send(body).expect(401);
    await post(ctx.app, evt("e1"), "nothex").expect(401);
    await post(ctx.app, evt("e1"), sign(body, "other-secret")).expect(401);
    await post(ctx.app, evt("e1"), sign(JSON.stringify(evt("e2")), SECRET)).expect(401);
    const list = await request(ctx.app).get("/merchants/m1/events").set(auth);
    expect(list.body.events).toHaveLength(0);
  });

  it("accepts a valid signature, including a sha256= prefix", async () => {
    const e = evt("e1", "payment.failed");
    await post(ctx.app, e, `sha256=${sign(JSON.stringify(e), SECRET)}`).expect(202);
  });

  it("returns 400 for signed but invalid payloads", async () => {
    const raw = "not json";
    await request(ctx.app)
      .post("/webhooks/payments")
      .set("X-Signature", sign(raw, SECRET))
      .send(raw)
      .expect(400);
    await post(ctx.app, { type: "payment.succeeded" }).expect(400);
  });

  it("returns 404 for unknown merchants", async () => {
    await post(ctx.app, { ...evt("e1"), merchant_id: "nope" }).expect(404);
  });

  it("forwards successful payments to the merchant callback", async () => {
    await post(ctx.app, evt("e1")).expect(202);
    await ctx.flush();
    expect(ctx.fetchFn).toHaveBeenCalledTimes(1);
    const [url, init] = ctx.fetchFn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://merchant.test/hook");
    expect(JSON.parse(init.body as string).id).toBe("e1");

    const list = await request(ctx.app).get("/merchants/m1/events").set(auth);
    expect(list.body.events[0]).toMatchObject({ id: "e1", delivery_status: "delivered", delivery_attempts: 1 });
  });

  it("stores but does not forward other event types", async () => {
    await post(ctx.app, evt("e1", "payment.failed")).expect(202);
    await ctx.flush();
    expect(ctx.fetchFn).not.toHaveBeenCalled();
    const list = await request(ctx.app).get("/merchants/m1/events").set(auth);
    expect(list.body.events[0]).toMatchObject({ type: "payment.failed", delivery_status: "not_applicable" });
  });

  it("does not store or re-forward duplicate events", async () => {
    await post(ctx.app, evt("e1")).expect(202);
    await post(ctx.app, evt("e1")).expect(200);
    await ctx.flush();
    expect(ctx.fetchFn).toHaveBeenCalledTimes(1);
    const list = await request(ctx.app).get("/merchants/m1/events").set(auth);
    expect(list.body.events).toHaveLength(1);
  });

  it("retries failed callbacks and marks delivery as failed", async () => {
    const failing = setup(async () => new Response("no", { status: 500 }));
    await request(failing.app).put("/merchants/m1").set(auth).send({ callback_url: "https://merchant.test/hook" });
    await post(failing.app, evt("e1")).expect(202);
    await failing.flush();
    expect(failing.fetchFn).toHaveBeenCalledTimes(3);
    const list = await request(failing.app).get("/merchants/m1/events").set(auth);
    expect(list.body.events[0]).toMatchObject({ delivery_status: "failed", delivery_attempts: 3 });
  });

  it("still acknowledges the event when the callback throws", async () => {
    const throwing = setup(async () => {
      throw new Error("ECONNREFUSED");
    });
    await request(throwing.app).put("/merchants/m1").set(auth).send({ callback_url: "https://merchant.test/hook" });
    await post(throwing.app, evt("e1")).expect(202);
    await throwing.flush();
    const list = await request(throwing.app).get("/merchants/m1/events").set(auth);
    expect(list.body.events[0].delivery_status).toBe("failed");
  });

  it("lists recent events newest first, honouring limit", async () => {
    for (const id of ["e1", "e2", "e3"]) await post(ctx.app, evt(id, "payment.failed")).expect(202);
    const res = await request(ctx.app).get("/merchants/m1/events?limit=2").set(auth).expect(200);
    expect(res.body.events.map((e: any) => e.id)).toEqual(["e3", "e2"]);
    expect(res.body.events[0].payload.data.amount).toBe(1000);
  });

  it("requires the admin token for listing and merchant config", async () => {
    await request(ctx.app).get("/merchants/m1/events").expect(401);
    await request(ctx.app).get("/merchants/m1/events").set("Authorization", "Bearer wrong").expect(401);
    await request(ctx.app).put("/merchants/m2").send({ callback_url: "https://x.test" }).expect(401);
  });

  it("validates callback_url", async () => {
    await request(ctx.app).put("/merchants/m2").set(auth).send({ callback_url: "ftp://x.test" }).expect(400);
    await request(ctx.app).put("/merchants/m2").set(auth).send({}).expect(400);
  });
});
