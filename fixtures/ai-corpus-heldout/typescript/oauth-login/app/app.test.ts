import { after, afterEach, before, beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import { once } from "node:events";
import http from "node:http";
import type { AddressInfo } from "node:net";
import { createApp } from "./app";
import type { Config } from "./app";
import { openDatabase } from "./db";
import type { Db } from "./db";

const CLIENT_SECRET = "test-secret";
const PUBLIC_URL = "http://localhost:3000";
const defaultUser = {
  id: 1001,
  login: "octocat",
  name: "The Octocat",
  email: "octo@example.com",
  avatar_url: "https://avatars.example/octo.png",
};

// Fake GitHub: serves the token and user endpoints the app calls, so no network is needed.
let githubUser = { ...defaultUser };
let fakeGithub: http.Server;
let config: Config;

let db: Db;
let server: http.Server;
let client: Client;

before(async () => {
  fakeGithub = http.createServer((req, res) => {
    if (req.method === "POST" && req.url === "/login/oauth/access_token") {
      let raw = "";
      req.on("data", (chunk) => (raw += chunk));
      req.on("end", () => {
        const body = JSON.parse(raw);
        const ok = body.code === "good-code" && body.client_secret === CLIENT_SECRET;
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(ok ? { access_token: "gho_test" } : { error: "bad_verification_code" }));
      });
      return;
    }
    if (req.method === "GET" && req.url === "/user" && req.headers.authorization === "Bearer gho_test") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(githubUser));
      return;
    }
    res.writeHead(404).end();
  });
  fakeGithub.listen(0, "127.0.0.1");
  await once(fakeGithub, "listening");
  const githubBase = `http://127.0.0.1:${(fakeGithub.address() as AddressInfo).port}`;
  config = {
    publicUrl: PUBLIC_URL,
    githubClientId: "client-id",
    githubClientSecret: CLIENT_SECRET,
    githubWebUrl: githubBase,
    githubApiUrl: githubBase,
    secureCookies: false,
  };
});

after(() => {
  fakeGithub.close();
});

beforeEach(async () => {
  githubUser = { ...defaultUser };
  db = openDatabase(":memory:");
  server = createApp(db, config).listen(0, "127.0.0.1");
  await once(server, "listening");
  client = new Client(`http://127.0.0.1:${(server.address() as AddressInfo).port}`);
});

afterEach(() => {
  server.close();
  db.close();
});

// Minimal HTTP client that keeps cookies between requests and does not follow redirects.
class Client {
  readonly cookies = new Map<string, string>();

  constructor(private readonly base: string) {}

  async request(path: string, init: RequestInit = {}): Promise<Response> {
    const headers = new Headers(init.headers);
    if (this.cookies.size > 0) {
      headers.set("cookie", [...this.cookies].map(([k, v]) => `${k}=${v}`).join("; "));
    }
    const res = await fetch(this.base + path, { ...init, headers, redirect: "manual" });
    for (const line of res.headers.getSetCookie()) {
      const [pair] = line.split(";");
      const index = pair.indexOf("=");
      const name = pair.slice(0, index);
      const value = pair.slice(index + 1);
      const expired = /Expires=Thu, 01 Jan 1970/i.test(line);
      if (expired || value === "") this.cookies.delete(name);
      else this.cookies.set(name, value);
    }
    return res;
  }
}

// Runs the whole login flow through the app's own routes and returns the callback response.
async function loginAs(c: Client, next = "/profile"): Promise<Response> {
  const start = await c.request(`/login?next=${encodeURIComponent(next)}`);
  const state = new URL(start.headers.get("location")!).searchParams.get("state")!;
  return c.request(`/callback?code=good-code&state=${state}`);
}

function count(table: string): number {
  return (db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get() as { n: number }).n;
}

test("login redirects to GitHub with a state value stored in a cookie", async () => {
  const res = await client.request("/login?next=/profile");

  assert.equal(res.status, 302);
  const location = new URL(res.headers.get("location")!);
  assert.equal(location.origin + location.pathname, `${config.githubWebUrl}/login/oauth/authorize`);
  assert.equal(location.searchParams.get("client_id"), "client-id");
  assert.equal(location.searchParams.get("redirect_uri"), `${PUBLIC_URL}/callback`);
  assert.equal(location.searchParams.get("scope"), "read:user");
  assert.equal(location.searchParams.get("state"), client.cookies.get("oauth_state"));
});

test("callback exchanges the code, creates the user and starts a session", async () => {
  const res = await loginAs(client, "/profile");

  assert.equal(res.status, 302);
  assert.equal(res.headers.get("location"), "/profile");
  assert.ok(client.cookies.has("sid"));
  assert.equal(count("users"), 1);
  assert.equal(count("sessions"), 1);

  const profile = await client.request("/profile");
  assert.equal(profile.status, 200);
  assert.match(await profile.text(), /octocat/);
});

test("a later login updates the existing user instead of creating a duplicate", async () => {
  await loginAs(client);
  githubUser = { ...defaultUser, login: "octocat-renamed", name: "Renamed" };

  await loginAs(client);

  assert.equal(count("users"), 1);
  const row = db.prepare("SELECT login, name FROM users").get() as { login: string; name: string };
  assert.equal(row.login, "octocat-renamed");
  assert.equal(row.name, "Renamed");
});

test("callback rejects a state value that does not match the cookie", async () => {
  await client.request("/login");

  const res = await client.request("/callback?code=good-code&state=forged");

  assert.equal(res.status, 400);
  assert.equal(count("users"), 0);
  assert.equal(count("sessions"), 0);
});

test("callback rejects a request with no login cookie", async () => {
  const res = await client.request("/callback?code=good-code&state=anything");

  assert.equal(res.status, 400);
  assert.equal(count("users"), 0);
});

test("callback returns 502 when GitHub rejects the code", async () => {
  const start = await client.request("/login");
  const state = new URL(start.headers.get("location")!).searchParams.get("state");

  const res = await client.request(`/callback?code=bad-code&state=${state}`);

  assert.equal(res.status, 502);
  assert.equal(count("users"), 0);
  assert.equal(count("sessions"), 0);
});

test("profile sends anonymous visitors to log in", async () => {
  const res = await client.request("/profile");

  assert.equal(res.status, 302);
  assert.equal(res.headers.get("location"), "/login?next=%2Fprofile");
});

test("the user is sent back to the page they came from", async () => {
  const res = await loginAs(client, "/notes?id=3");

  assert.equal(res.headers.get("location"), "/notes?id=3");
});

test("external and protocol-relative return URLs fall back to the profile page", async () => {
  for (const next of ["https://evil.example/", "//evil.example/", "/\\evil.example"]) {
    const freshDb = openDatabase(":memory:");
    const freshServer = createApp(freshDb, config).listen(0, "127.0.0.1");
    await once(freshServer, "listening");
    const freshClient = new Client(`http://127.0.0.1:${(freshServer.address() as AddressInfo).port}`);

    const res = await loginAs(freshClient, next);

    assert.equal(res.headers.get("location"), "/profile", `next=${next}`);
    freshServer.close();
    freshDb.close();
  }
});

test("logout ends the session", async () => {
  await loginAs(client);

  const logout = await client.request("/logout", { method: "POST" });
  assert.equal(logout.status, 302);
  assert.equal(logout.headers.get("location"), "/");
  assert.equal(count("sessions"), 0);

  const profile = await client.request("/profile");
  assert.equal(profile.status, 302);
  assert.match(profile.headers.get("location")!, /^\/login/);
});

test("expired sessions are rejected", async () => {
  await loginAs(client);
  db.prepare("UPDATE sessions SET expires_at = 0").run();

  const res = await client.request("/profile");

  assert.equal(res.status, 302);
  assert.match(res.headers.get("location")!, /^\/login/);
});

test("profile fields from GitHub are HTML-escaped", async () => {
  githubUser = { ...defaultUser, name: "<script>alert(1)</script>" };
  await loginAs(client);

  const body = await (await client.request("/profile")).text();

  assert.ok(!body.includes("<script>alert(1)</script>"));
  assert.ok(body.includes("&lt;script&gt;alert(1)&lt;/script&gt;"));
});
