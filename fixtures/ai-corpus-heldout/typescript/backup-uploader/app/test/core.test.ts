import { existsSync } from "node:fs";
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import type { AddressInfo } from "node:net";
import os from "node:os";
import path from "node:path";
import { extract } from "tar";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  BackupRunner,
  backupKey,
  type Config,
  createStatusServer,
  formatLogLine,
  loadConfig,
  type ObjectStore,
  parseBackupTime,
  parseConfig,
  pruneBackups,
  type RunResult,
  runBackup,
} from "../src/core.js";

class MemoryStore implements ObjectStore {
  objects = new Map<string, Buffer>();
  uploadedFrom: string[] = [];
  failUpload = false;
  failDelete = false;

  async upload(key: string, filePath: string): Promise<void> {
    this.uploadedFrom.push(filePath);
    if (this.failUpload) throw new Error("upload refused");
    this.objects.set(key, await readFile(filePath));
  }
  async list(prefix: string): Promise<string[]> {
    return [...this.objects.keys()].filter((k) => k.startsWith(prefix));
  }
  async delete(key: string): Promise<void> {
    if (this.failDelete) throw new Error("delete refused");
    this.objects.delete(key);
  }
}

const DAY = 24 * 60 * 60 * 1000;
const NOW = new Date("2026-10-22T03:00:00.000Z");

function baseConfig(overrides: Partial<Config> = {}): Config {
  return parseConfig({
    schedule: "0 3 * * *",
    directories: ["/tmp"],
    retentionDays: 7,
    s3: { bucket: "b", prefix: "host/" },
    ...overrides,
  });
}

let tmp: string;
beforeEach(async () => {
  tmp = await realpath(await mkdtemp(path.join(os.tmpdir(), "backup-test-")));
});
afterEach(async () => {
  await rm(tmp, { recursive: true, force: true });
});

describe("config", () => {
  it("applies defaults", () => {
    const cfg = baseConfig();
    expect(cfg.s3).toMatchObject({ prefix: "host/", region: "us-east-1", forcePathStyle: false });
    expect(cfg.status).toEqual({ host: "127.0.0.1", port: 8080 });
  });

  it("reports every problem at once", () => {
    let message = "";
    try {
      parseConfig({ schedule: "not a cron", directories: ["relative/dir"], retentionDays: 0, s3: {} });
    } catch (err) {
      message = (err as Error).message;
    }
    expect(message).toMatch(/schedule/);
    expect(message).toMatch(/absolute path/);
    expect(message).toMatch(/retentionDays/);
    expect(message).toMatch(/s3.bucket/);
  });

  it("rejects an unknown timezone", () => {
    expect(() => baseConfig({ timezone: "Mars/Olympus" })).toThrow(/timezone/);
  });

  it("loads from a file and reports JSON errors", async () => {
    const good = path.join(tmp, "good.json");
    await writeFile(good, JSON.stringify({ schedule: "*/5 * * * *", directories: ["/etc"], retentionDays: 1, s3: { bucket: "x" } }));
    expect((await loadConfig(good)).s3.bucket).toBe("x");

    const bad = path.join(tmp, "bad.json");
    await writeFile(bad, "{ nope");
    await expect(loadConfig(bad)).rejects.toThrow(/cannot parse/);
  });
});

describe("backup keys", () => {
  it("round-trips the timestamp", () => {
    const key = backupKey("host/", NOW);
    expect(key).toBe("host/backup-2026-10-22T03-00-00.000Z.tar.gz");
    expect(parseBackupTime("host/", key)?.toISOString()).toBe(NOW.toISOString());
  });

  it("ignores keys it did not create", () => {
    expect(parseBackupTime("host/", "host/notes.txt")).toBeNull();
    expect(parseBackupTime("host/", "host/nested/backup-2026-10-22T03-00-00.000Z.tar.gz")).toBeNull();
    expect(parseBackupTime("host/", "other/backup-2026-10-22T03-00-00.000Z.tar.gz")).toBeNull();
  });
});

describe("pruneBackups", () => {
  it("deletes only expired backups under the prefix", async () => {
    const store = new MemoryStore();
    const old = backupKey("host/", new Date(NOW.getTime() - 8 * DAY));
    const recent = backupKey("host/", new Date(NOW.getTime() - 6 * DAY));
    const otherHost = backupKey("other/", new Date(NOW.getTime() - 30 * DAY));
    const unrelated = "host/README.txt";
    for (const k of [old, recent, otherHost, unrelated]) store.objects.set(k, Buffer.from("x"));

    const deleted = await pruneBackups(store, "host/", 7, NOW);

    expect(deleted).toEqual([old]);
    expect([...store.objects.keys()].sort()).toEqual([otherHost, recent, unrelated].sort());
  });
});

describe("runBackup", () => {
  async function makeSource(): Promise<string> {
    const src = path.join(tmp, "src");
    await mkdir(path.join(src, "sub"), { recursive: true });
    await writeFile(path.join(src, "a.txt"), "hello");
    await writeFile(path.join(src, "sub", "b.txt"), "world");
    return src;
  }

  it("archives, uploads and prunes", async () => {
    const src = await makeSource();
    const store = new MemoryStore();
    const expired = backupKey("host/", new Date(NOW.getTime() - 10 * DAY));
    store.objects.set(expired, Buffer.from("old"));

    const result = await runBackup(baseConfig({ directories: [src] }), store, () => NOW);

    expect(result.ok).toBe(true);
    expect(result.error).toBeUndefined();
    expect(result.key).toBe(backupKey("host/", NOW));
    expect(result.deleted).toEqual([expired]);
    expect(store.objects.has(expired)).toBe(false);

    // The uploaded archive contains the files, stored under their absolute path.
    const archive = path.join(tmp, "out.tar.gz");
    const out = path.join(tmp, "out");
    await writeFile(archive, store.objects.get(result.key!)!);
    await mkdir(out);
    await extract({ file: archive, cwd: out });
    const restored = path.join(out, path.relative("/", src));
    expect(await readFile(path.join(restored, "a.txt"), "utf8")).toBe("hello");
    expect(await readFile(path.join(restored, "sub", "b.txt"), "utf8")).toBe("world");

    // The temporary archive is cleaned up.
    expect(existsSync(store.uploadedFrom[0])).toBe(false);
  });

  it("does not prune when the upload fails", async () => {
    const src = await makeSource();
    const store = new MemoryStore();
    store.failUpload = true;
    const expired = backupKey("host/", new Date(NOW.getTime() - 10 * DAY));
    store.objects.set(expired, Buffer.from("old"));

    const result = await runBackup(baseConfig({ directories: [src] }), store, () => NOW);

    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/upload refused/);
    expect(store.objects.has(expired)).toBe(true);
    expect(existsSync(store.uploadedFrom[0])).toBe(false);
  });

  it("reports a retention failure after a successful upload", async () => {
    const src = await makeSource();
    const store = new MemoryStore();
    store.failDelete = true;
    store.objects.set(backupKey("host/", new Date(NOW.getTime() - 10 * DAY)), Buffer.from("old"));

    const result = await runBackup(baseConfig({ directories: [src] }), store, () => NOW);

    expect(result.ok).toBe(false);
    expect(result.key).toBe(backupKey("host/", NOW));
    expect(result.error).toMatch(/uploaded .* but retention cleanup failed: delete refused/);
  });

  it("fails without uploading when a directory is missing", async () => {
    const store = new MemoryStore();
    const result = await runBackup(baseConfig({ directories: [path.join(tmp, "missing")] }), store, () => NOW);

    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/not a readable directory/);
    expect(store.objects.size).toBe(0);
  });
});

describe("BackupRunner", () => {
  const sample: RunResult = {
    startedAt: NOW.toISOString(),
    finishedAt: NOW.toISOString(),
    durationMs: 0,
    ok: true,
    key: "host/k",
  };

  it("skips a run while one is in progress and records the last result", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const logged: RunResult[] = [];
    const runner = new BackupRunner(async () => {
      await gate;
      return sample;
    }, (r) => logged.push(r));

    const first = runner.run();
    expect(runner.running).toBe(true);
    expect(await runner.run()).toBeNull();
    release();
    expect(await first).toBe(sample);
    expect(runner.running).toBe(false);
    expect(runner.lastRun).toBe(sample);
    expect(logged).toEqual([sample]);
  });

  it("formats one JSON log line per run", () => {
    const line = formatLogLine({ ...sample, deleted: ["a", "b"], warnings: [] });
    expect(line).not.toContain("\n");
    expect(JSON.parse(line)).toMatchObject({ event: "backup", ok: true, key: "host/k", deleted: 2, warnings: 0 });
  });
});

describe("status server", () => {
  it("serves the last run and next run time", async () => {
    const runner = new BackupRunner(async () => ({
      startedAt: NOW.toISOString(),
      finishedAt: NOW.toISOString(),
      durationMs: 5,
      ok: false,
      error: "boom",
    }), () => {});
    const next = new Date("2026-10-23T03:00:00.000Z");
    const server = createStatusServer(runner, () => next);
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
    const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    try {
      let body = await (await fetch(`${base}/status`)).json();
      expect(body).toEqual({ running: false, nextRun: next.toISOString(), lastRun: null });

      await runner.run();
      body = await (await fetch(`${base}/status`)).json();
      expect(body.lastRun).toMatchObject({ ok: false, error: "boom", finishedAt: NOW.toISOString() });

      expect((await fetch(`${base}/nope`)).status).toBe(404);
      expect((await fetch(`${base}/status`, { method: "POST" })).status).toBe(405);
    } finally {
      await new Promise((r) => server.close(r));
    }
  });
});
