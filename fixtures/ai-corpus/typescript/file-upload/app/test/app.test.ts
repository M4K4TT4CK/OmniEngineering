import { afterEach, beforeEach, describe, expect, it } from "vitest";
import request from "supertest";
import sharp from "sharp";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createApp } from "../src/app";

let dir: string;
let app: ReturnType<typeof createApp>;

beforeEach(async () => {
  dir = await fs.mkdtemp(path.join(os.tmpdir(), "upload-test-"));
  app = createApp({ dir, maxFileSize: 1024 * 1024, thumbnailSize: 50 });
});

afterEach(async () => {
  await fs.rm(dir, { recursive: true, force: true });
});

const png = () =>
  sharp({ create: { width: 400, height: 200, channels: 3, background: "#f00" } })
    .png()
    .toBuffer();

describe("upload", () => {
  it("stores a file on disk", async () => {
    const res = await request(app).post("/files").attach("file", Buffer.from("hello"), "a.txt");
    expect(res.status).toBe(201);
    expect(res.body).toEqual({ name: "a.txt", size: 5 });
    expect(await fs.readFile(path.join(dir, "a.txt"), "utf8")).toBe("hello");
  });

  it("rejects a missing file field", async () => {
    const res = await request(app).post("/files").field("x", "y");
    expect(res.status).toBe(400);
  });

  it("rejects duplicates with 409", async () => {
    await request(app).post("/files").attach("file", Buffer.from("1"), "a.txt");
    const res = await request(app).post("/files").attach("file", Buffer.from("2"), "a.txt");
    expect(res.status).toBe(409);
    expect(await fs.readFile(path.join(dir, "a.txt"), "utf8")).toBe("1");
  });

  it("rejects unsafe names", async () => {
    for (const name of ["../evil.txt", ".hidden", "sub/dir.txt"]) {
      const res = await request(app).post("/files").attach("file", Buffer.from("x"), name);
      expect(res.status).toBe(400);
    }
  });

  it("rejects files over the size limit with 413", async () => {
    const res = await request(app)
      .post("/files")
      .attach("file", Buffer.alloc(1024 * 1024 + 1), "big.bin");
    expect(res.status).toBe(413);
  });
});

describe("list", () => {
  it("returns names, sizes and upload times", async () => {
    await request(app).post("/files").attach("file", Buffer.from("abc"), "b.txt");
    await request(app).post("/files").attach("file", Buffer.from("de"), "a.txt");
    const res = await request(app).get("/files");
    expect(res.status).toBe(200);
    expect(res.body.map((f: any) => [f.name, f.size])).toEqual([
      ["a.txt", 2],
      ["b.txt", 3],
    ]);
    expect(Number.isNaN(Date.parse(res.body[0].uploadedAt))).toBe(false);
  });

  it("does not list the thumbnail directory", async () => {
    const res = await request(app).get("/files");
    expect(res.body).toEqual([]);
  });
});

describe("download", () => {
  it("returns file contents", async () => {
    await request(app).post("/files").attach("file", Buffer.from("hello"), "a.txt");
    const res = await request(app).get("/files/a.txt").buffer().parse((r, cb) => {
      const chunks: Buffer[] = [];
      r.on("data", (c) => chunks.push(c));
      r.on("end", () => cb(null, Buffer.concat(chunks)));
    });
    expect(res.status).toBe(200);
    expect(res.headers["content-disposition"]).toContain("a.txt");
    expect((res.body as Buffer).toString()).toBe("hello");
  });

  it("404s for unknown files", async () => {
    expect((await request(app).get("/files/nope.txt")).status).toBe(404);
  });

  it("does not serve hidden entries", async () => {
    expect((await request(app).get("/files/.thumbnails")).status).toBe(400);
  });
});

describe("delete", () => {
  it("removes the file and its thumbnail", async () => {
    await request(app).post("/files").attach("file", await png(), "p.png");
    await request(app).post("/files/p.png/thumbnail");
    const res = await request(app).delete("/files/p.png");
    expect(res.status).toBe(204);
    expect((await request(app).get("/files/p.png")).status).toBe(404);
    expect((await request(app).get("/files/p.png/thumbnail")).status).toBe(404);
  });

  it("404s for unknown files", async () => {
    expect((await request(app).delete("/files/nope.txt")).status).toBe(404);
  });
});

describe("thumbnails", () => {
  it("generates a resized PNG for an image", async () => {
    await request(app).post("/files").attach("file", await png(), "p.png");
    const created = await request(app).post("/files/p.png/thumbnail");
    expect(created.status).toBe(201);

    const res = await request(app).get("/files/p.png/thumbnail").buffer().parse((r, cb) => {
      const chunks: Buffer[] = [];
      r.on("data", (c) => chunks.push(c));
      r.on("end", () => cb(null, Buffer.concat(chunks)));
    });
    expect(res.status).toBe(200);
    expect(res.headers["content-type"]).toContain("image/png");
    const meta = await sharp(res.body as Buffer).metadata();
    expect(meta.width).toBe(50);
    expect(meta.height).toBe(25);
  });

  it("returns 415 for non-images", async () => {
    await request(app).post("/files").attach("file", Buffer.from("text"), "a.txt");
    expect((await request(app).post("/files/a.txt/thumbnail")).status).toBe(415);
  });

  it("returns 404 for unknown files and ungenerated thumbnails", async () => {
    expect((await request(app).post("/files/nope.png/thumbnail")).status).toBe(404);
    await request(app).post("/files").attach("file", await png(), "p.png");
    expect((await request(app).get("/files/p.png/thumbnail")).status).toBe(404);
  });
});
