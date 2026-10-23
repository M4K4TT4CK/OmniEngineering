import { after, before, describe, it } from "node:test";
import assert from "node:assert/strict";
import type { AddressInfo } from "node:net";
import { WebSocket } from "ws";
import { createRelayServer, HISTORY_LIMIT, type RelayServer } from "../src/server";

type Msg = Record<string, any>;

// Wraps a WebSocket so tests can await incoming messages in order.
class TestClient {
  private inbox: Msg[] = [];
  private waiting: ((msg: Msg) => void)[] = [];

  private constructor(readonly socket: WebSocket) {
    socket.on("message", (data) => {
      const msg = JSON.parse(data.toString());
      const resolve = this.waiting.shift();
      if (resolve) resolve(msg);
      else this.inbox.push(msg);
    });
  }

  static connect(port: number): Promise<TestClient> {
    const socket = new WebSocket(`ws://127.0.0.1:${port}`);
    const client = new TestClient(socket);
    return new Promise((resolve, reject) => {
      socket.once("open", () => resolve(client));
      socket.once("error", reject);
    });
  }

  send(payload: Msg) {
    this.socket.send(JSON.stringify(payload));
  }

  next(timeoutMs = 2000): Promise<Msg> {
    const msg = this.inbox.shift();
    if (msg) return Promise.resolve(msg);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("timed out waiting for message")), timeoutMs);
      this.waiting.push((m) => {
        clearTimeout(timer);
        resolve(m);
      });
    });
  }

  close() {
    this.socket.close();
  }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe("chat relay", () => {
  let relay: RelayServer;
  let port: number;
  const clients: TestClient[] = [];

  before(async () => {
    relay = createRelayServer();
    await new Promise<void>((resolve) => relay.httpServer.listen(0, "127.0.0.1", resolve));
    port = (relay.httpServer.address() as AddressInfo).port;
  });

  after(async () => {
    for (const client of clients) client.close();
    await relay.close();
  });

  async function connectAs(name: string): Promise<TestClient> {
    const client = await TestClient.connect(port);
    clients.push(client);
    client.send({ type: "hello", name });
    assert.deepEqual(await client.next(), { type: "welcome", name });
    return client;
  }

  async function join(client: TestClient, room: string): Promise<Msg> {
    client.send({ type: "join", room });
    return client.next();
  }

  async function roomCounts(): Promise<Record<string, number>> {
    const res = await fetch(`http://127.0.0.1:${port}/rooms`);
    const list: { name: string; members: number }[] = await res.json();
    return Object.fromEntries(list.map((room) => [room.name, room.members]));
  }

  it("relays a message to everyone else in the room, not back to the sender", async () => {
    const alice = await connectAs("alice");
    const bob = await connectAs("bob");
    const room = "relay";

    const joined = await join(alice, room);
    assert.equal(joined.type, "joined");
    await join(bob, room);

    alice.send({ type: "message", room, text: "hi" });
    const received = await bob.next();
    assert.equal(received.type, "message");
    assert.equal(received.room, room);
    assert.equal(received.from, "alice");
    assert.equal(received.text, "hi");
    assert.equal(typeof received.ts, "number");

    // The next thing alice receives must be bob's reply, so she was not sent her own "hi".
    bob.send({ type: "message", room, text: "pong" });
    const reply = await alice.next();
    assert.equal(reply.text, "pong");
    assert.equal(reply.from, "bob");
  });

  it("does not relay messages to clients in other rooms", async () => {
    const alice = await connectAs("alice-iso");
    const carol = await connectAs("carol-iso");
    const bob = await connectAs("bob-iso");

    await join(alice, "iso-a");
    await join(carol, "iso-a");
    await join(bob, "iso-b");

    alice.send({ type: "message", room: "iso-a", text: "private" });
    assert.equal((await carol.next()).text, "private");

    // bob is only in iso-b, so the first thing he hears must be this error, not alice's message.
    bob.send({ type: "message", room: "iso-a", text: "sneaky" });
    const response = await bob.next();
    assert.equal(response.type, "error");
  });

  it("sends the last 50 messages of a room when a client joins", async () => {
    const alice = await connectAs("alice-hist");
    const room = "history";
    await join(alice, room);

    const total = HISTORY_LIMIT + 5;
    for (let i = 0; i < total; i++) {
      alice.send({ type: "message", room, text: `m${i}` });
    }
    // The joined reply comes after everything alice sent before it, so the history is up to date.
    await join(alice, "history-sync");

    const bob = await connectAs("bob-hist");
    const joined = await join(bob, room);
    assert.equal(joined.type, "joined");
    assert.equal(joined.history.length, HISTORY_LIMIT);
    assert.equal(joined.history[0].text, "m5");
    assert.equal(joined.history[HISTORY_LIMIT - 1].text, `m${total - 1}`);
    assert.equal(joined.history[0].from, "alice-hist");
  });

  it("reports the members of a room when a client joins", async () => {
    const alice = await connectAs("alice-members");
    const bob = await connectAs("bob-members");
    await join(alice, "members");
    const joined = await join(bob, "members");
    assert.deepEqual(joined.members.sort(), ["alice-members", "bob-members"]);
  });

  it("refuses to send to a room the client has not joined", async () => {
    const alice = await connectAs("alice-nojoin");
    alice.send({ type: "message", room: "never-joined", text: "hello?" });
    const response = await alice.next();
    assert.equal(response.type, "error");
  });

  it("requires hello before joining a room", async () => {
    const client = await TestClient.connect(port);
    clients.push(client);
    client.send({ type: "join", room: "no-hello" });
    const response = await client.next();
    assert.equal(response.type, "error");
  });

  it("rejects invalid JSON and unknown message types", async () => {
    const client = await connectAs("badinput");

    client.socket.send("not json");
    assert.equal((await client.next()).type, "error");

    client.send({ type: "dance" });
    assert.equal((await client.next()).type, "error");
  });

  it("lists rooms and their member counts over HTTP", async () => {
    const alice = await connectAs("alice-http");
    const bob = await connectAs("bob-http");
    const carol = await connectAs("carol-http");
    await join(alice, "http-1");
    await join(bob, "http-1");
    await join(carol, "http-2");

    const counts = await roomCounts();
    assert.equal(counts["http-1"], 2);
    assert.equal(counts["http-2"], 1);

    // Disconnecting removes the client from its rooms. Wait for the server to notice.
    alice.close();
    for (let i = 0; i < 50 && (await roomCounts())["http-1"] !== 1; i++) {
      await sleep(20);
    }
    assert.equal((await roomCounts())["http-1"], 1);
  });

  it("returns 404 for unknown HTTP paths", async () => {
    const res = await fetch(`http://127.0.0.1:${port}/nope`);
    assert.equal(res.status, 404);
  });
});
