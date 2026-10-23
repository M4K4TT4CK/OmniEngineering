import request from "supertest";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, openDb } from "./app";

let getTitle: ReturnType<typeof vi.fn>;
let app: ReturnType<typeof createApp>;

beforeEach(() => {
  getTitle = vi.fn(async () => "Example Title");
  app = createApp(openDb(":memory:"), getTitle);
});

describe("POST /api/links", () => {
  it("creates a link with a generated code and fetched title", async () => {
    const res = await request(app).post("/api/links").send({ url: "https://example.com/page" });
    expect(res.status).toBe(201);
    expect(res.body.code).toMatch(/^[A-Za-z0-9]{7}$/);
    expect(res.body.title).toBe("Example Title");
    expect(res.body.shortUrl).toMatch(new RegExp(`/${res.body.code}$`));
    expect(getTitle).toHaveBeenCalledWith("https://example.com/page");
  });

  it("supports a custom alias", async () => {
    const res = await request(app).post("/api/links").send({ url: "https://example.com", alias: "my-link" });
    expect(res.status).toBe(201);
    expect(res.body.code).toBe("my-link");
  });

  it("rejects a duplicate alias with 409", async () => {
    await request(app).post("/api/links").send({ url: "https://example.com", alias: "dup" });
    const res = await request(app).post("/api/links").send({ url: "https://other.com", alias: "dup" });
    expect(res.status).toBe(409);
  });

  it.each(["ab", "has space", "bad/slash", "api", "x".repeat(33)])("rejects invalid alias %j", async (alias) => {
    const res = await request(app).post("/api/links").send({ url: "https://example.com", alias });
    expect(res.status).toBe(400);
  });

  it.each([undefined, "", "not a url", "ftp://example.com", "javascript:alert(1)", 42])(
    "rejects invalid url %j",
    async (url) => {
      const res = await request(app).post("/api/links").send({ url });
      expect(res.status).toBe(400);
    },
  );

  it("rejects malformed JSON with 400", async () => {
    const res = await request(app).post("/api/links").set("content-type", "application/json").send("{oops");
    expect(res.status).toBe(400);
  });

  it("still creates the link when the title cannot be fetched", async () => {
    getTitle.mockResolvedValueOnce(null);
    const res = await request(app).post("/api/links").send({ url: "https://example.com" });
    expect(res.status).toBe(201);
    expect(res.body.title).toBeNull();
  });
});

describe("GET /:code", () => {
  it("redirects to the original URL", async () => {
    await request(app).post("/api/links").send({ url: "https://example.com/a?b=1", alias: "go" });
    const res = await request(app).get("/go");
    expect(res.status).toBe(302);
    expect(res.headers.location).toBe("https://example.com/a?b=1");
  });

  it("returns 404 for an unknown code", async () => {
    const res = await request(app).get("/nope123");
    expect(res.status).toBe(404);
  });
});

describe("GET /api/links/:code/stats", () => {
  it("returns clicks, creation time and title", async () => {
    await request(app).post("/api/links").send({ url: "https://example.com", alias: "stat" });
    await request(app).get("/stat");
    await request(app).get("/stat");

    const res = await request(app).get("/api/links/stat/stats");
    expect(res.status).toBe(200);
    expect(res.body).toMatchObject({
      code: "stat",
      url: "https://example.com/",
      title: "Example Title",
      clicks: 2,
    });
    expect(new Date(res.body.createdAt).toString()).not.toBe("Invalid Date");
  });

  it("does not count stats lookups as clicks", async () => {
    await request(app).post("/api/links").send({ url: "https://example.com", alias: "quiet" });
    await request(app).get("/api/links/quiet/stats");
    const res = await request(app).get("/api/links/quiet/stats");
    expect(res.body.clicks).toBe(0);
  });

  it("returns 404 for an unknown code", async () => {
    const res = await request(app).get("/api/links/missing/stats");
    expect(res.status).toBe(404);
  });
});
