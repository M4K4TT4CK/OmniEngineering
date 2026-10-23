import { createReadStream } from "node:fs";
import { mkdtemp, readFile, realpath, rm, stat } from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { DeleteObjectCommand, ListObjectsV2Command, S3Client } from "@aws-sdk/client-s3";
import { Upload } from "@aws-sdk/lib-storage";
import { Cron } from "croner";
import { create as createTar } from "tar";

const DAY_MS = 24 * 60 * 60 * 1000;
const MAX_REPORTED_WARNINGS = 50;

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

export interface S3Config {
  bucket: string;
  /** Literal key prefix, e.g. "hosts/web-1/". Only keys directly under it are managed. */
  prefix: string;
  region: string;
  endpoint?: string;
  forcePathStyle: boolean;
}

export interface Config {
  schedule: string;
  timezone?: string;
  directories: string[];
  retentionDays: number;
  s3: S3Config;
  status: { host: string; port: number };
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Validates a parsed config object, collecting every problem rather than stopping at the first. */
export function parseConfig(raw: unknown): Config {
  const errors: string[] = [];
  if (!isObject(raw)) throw new Error("config must be a JSON object");

  const schedule = raw.schedule;
  const timezone = raw.timezone;
  if (timezone !== undefined && typeof timezone !== "string") errors.push("timezone must be a string");
  if (typeof schedule !== "string" || schedule.trim() === "") {
    errors.push("schedule must be a non-empty cron expression");
  } else {
    try {
      new Cron(schedule, { paused: true, timezone: typeof timezone === "string" ? timezone : undefined }).stop();
    } catch (err) {
      errors.push(`schedule/timezone is invalid: ${errorMessage(err)}`);
    }
  }

  const directories = raw.directories;
  if (!Array.isArray(directories) || directories.length === 0) {
    errors.push("directories must be a non-empty array");
  } else {
    for (const d of directories) {
      if (typeof d !== "string" || !path.isAbsolute(d)) errors.push(`directory must be an absolute path: ${JSON.stringify(d)}`);
    }
  }

  const retentionDays = raw.retentionDays;
  if (typeof retentionDays !== "number" || !Number.isFinite(retentionDays) || retentionDays <= 0) {
    errors.push("retentionDays must be a positive number");
  }

  const s3 = isObject(raw.s3) ? raw.s3 : {};
  if (!isObject(raw.s3)) errors.push("s3 section is required");
  if (typeof s3.bucket !== "string" || s3.bucket === "") errors.push("s3.bucket is required");
  if (s3.prefix !== undefined && typeof s3.prefix !== "string") errors.push("s3.prefix must be a string");
  if (s3.region !== undefined && typeof s3.region !== "string") errors.push("s3.region must be a string");
  if (s3.endpoint !== undefined && typeof s3.endpoint !== "string") errors.push("s3.endpoint must be a string");
  if (s3.forcePathStyle !== undefined && typeof s3.forcePathStyle !== "boolean") errors.push("s3.forcePathStyle must be a boolean");

  const status = isObject(raw.status) ? raw.status : {};
  if (raw.status !== undefined && !isObject(raw.status)) errors.push("status must be an object");
  if (status.host !== undefined && typeof status.host !== "string") errors.push("status.host must be a string");
  const port = status.port ?? 8080;
  if (typeof port !== "number" || !Number.isInteger(port) || port < 0 || port > 65535) {
    errors.push("status.port must be an integer between 0 and 65535");
  }

  if (errors.length > 0) throw new Error(`invalid config:\n  - ${errors.join("\n  - ")}`);

  return {
    schedule: schedule as string,
    timezone: timezone as string | undefined,
    directories: (directories as string[]).map((d) => path.resolve(d)),
    retentionDays: retentionDays as number,
    s3: {
      bucket: s3.bucket as string,
      prefix: (s3.prefix as string | undefined) ?? "",
      region: (s3.region as string | undefined) ?? "us-east-1",
      endpoint: s3.endpoint as string | undefined,
      forcePathStyle: (s3.forcePathStyle as boolean | undefined) ?? false,
    },
    status: { host: (status.host as string | undefined) ?? "127.0.0.1", port: port as number },
  };
}

export async function loadConfig(file: string): Promise<Config> {
  const text = await readFile(file, "utf8");
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (err) {
    throw new Error(`cannot parse ${file}: ${errorMessage(err)}`);
  }
  return parseConfig(raw);
}

// ---------------------------------------------------------------------------
// Object store
// ---------------------------------------------------------------------------

export interface ObjectStore {
  upload(key: string, filePath: string): Promise<void>;
  list(prefix: string): Promise<string[]>;
  delete(key: string): Promise<void>;
}

export class S3Store implements ObjectStore {
  constructor(
    private readonly client: S3Client,
    private readonly bucket: string,
  ) {}

  async upload(key: string, filePath: string): Promise<void> {
    // lib-storage switches to multipart automatically for large archives.
    const upload = new Upload({
      client: this.client,
      params: { Bucket: this.bucket, Key: key, Body: createReadStream(filePath), ContentType: "application/gzip" },
    });
    await upload.done();
  }

  async list(prefix: string): Promise<string[]> {
    const keys: string[] = [];
    let token: string | undefined;
    do {
      const res = await this.client.send(
        new ListObjectsV2Command({ Bucket: this.bucket, Prefix: prefix, ContinuationToken: token }),
      );
      for (const obj of res.Contents ?? []) if (obj.Key) keys.push(obj.Key);
      token = res.IsTruncated ? res.NextContinuationToken : undefined;
    } while (token);
    return keys;
  }

  async delete(key: string): Promise<void> {
    // Single-object deletes: DeleteObjects is not supported by every S3-compatible store.
    await this.client.send(new DeleteObjectCommand({ Bucket: this.bucket, Key: key }));
  }

  close(): void {
    this.client.destroy();
  }
}

/** Credentials come from the standard AWS provider chain (env vars, shared config, instance role). */
export function createS3Store(cfg: S3Config): S3Store {
  const client = new S3Client({
    region: cfg.region,
    endpoint: cfg.endpoint,
    forcePathStyle: cfg.forcePathStyle,
    // Newer SDKs add CRC checksums by default, which several S3-compatible stores reject.
    requestChecksumCalculation: "WHEN_REQUIRED",
    responseChecksumValidation: "WHEN_REQUIRED",
  });
  return new S3Store(client, cfg.bucket);
}

// ---------------------------------------------------------------------------
// Backup naming, archiving and retention
// ---------------------------------------------------------------------------

const KEY_PATTERN = /^backup-(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})\.(\d{3})Z\.tar\.gz$/;

export function backupKey(prefix: string, date: Date): string {
  return `${prefix}backup-${date.toISOString().replace(/:/g, "-")}.tar.gz`;
}

/** Returns the timestamp encoded in a key this tool created, or null for any other key. */
export function parseBackupTime(prefix: string, key: string): Date | null {
  if (!key.startsWith(prefix)) return null;
  const m = KEY_PATTERN.exec(key.slice(prefix.length));
  if (!m) return null;
  const date = new Date(`${m[1]}T${m[2]}:${m[3]}:${m[4]}.${m[5]}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * Writes a gzipped tarball of the given directories to outFile. Entries are stored
 * with their absolute path (minus the leading "/") so same-named directories don't collide.
 */
export async function createArchive(
  directories: string[],
  outFile: string,
): Promise<{ bytes: number; warnings: string[] }> {
  const resolved: string[] = [];
  for (const dir of directories) {
    const real = await realpath(dir).catch(() => null);
    const info = real ? await stat(real) : null;
    if (!real || !info?.isDirectory()) throw new Error(`not a readable directory: ${dir}`);
    resolved.push(real);
  }

  // Never archive our own work directory (e.g. when backing up /tmp).
  const workDir = path.dirname(path.resolve(outFile));
  const warnings: string[] = [];
  await createTar(
    {
      file: outFile,
      gzip: true,
      cwd: "/",
      portable: true,
      onwarn: (code: string, message: string) => warnings.push(`${code}: ${message}`),
      filter: (p: string) => {
        const abs = path.resolve("/", p);
        return abs !== workDir && !abs.startsWith(workDir + path.sep);
      },
    },
    resolved.map((d) => path.relative("/", d) || "."),
  );
  return { bytes: (await stat(outFile)).size, warnings };
}

/** Deletes backups under prefix older than the retention period. Unrecognised keys are never touched. */
export async function pruneBackups(
  store: ObjectStore,
  prefix: string,
  retentionDays: number,
  now: Date,
  keep?: string,
): Promise<string[]> {
  const cutoff = now.getTime() - retentionDays * DAY_MS;
  const expired = (await store.list(prefix)).filter((key) => {
    if (key === keep) return false;
    const time = parseBackupTime(prefix, key);
    return time !== null && time.getTime() < cutoff;
  });
  for (const key of expired) await store.delete(key);
  return expired;
}

// ---------------------------------------------------------------------------
// Running a backup
// ---------------------------------------------------------------------------

export interface RunResult {
  startedAt: string;
  finishedAt: string;
  durationMs: number;
  ok: boolean;
  key?: string;
  bytes?: number;
  deleted?: string[];
  warnings?: string[];
  error?: string;
}

export async function runBackup(
  config: Config,
  store: ObjectStore,
  clock: () => Date = () => new Date(),
): Promise<RunResult> {
  const started = clock();
  const result: Partial<RunResult> = { ok: false };
  let workDir: string | undefined;
  try {
    workDir = await mkdtemp(path.join(os.tmpdir(), "backup-uploader-"));
    const archive = path.join(workDir, "backup.tar.gz");
    const key = backupKey(config.s3.prefix, started);

    const { bytes, warnings } = await createArchive(config.directories, archive);
    result.warnings = warnings.slice(0, MAX_REPORTED_WARNINGS);
    await store.upload(key, archive);
    result.key = key;
    result.bytes = bytes;

    // Only prune after a successful upload, so a failing run never shrinks the backup set.
    try {
      result.deleted = await pruneBackups(store, config.s3.prefix, config.retentionDays, clock(), key);
    } catch (err) {
      throw new Error(`uploaded ${key} but retention cleanup failed: ${errorMessage(err)}`);
    }
    result.ok = true;
  } catch (err) {
    result.error = errorMessage(err);
  } finally {
    if (workDir) await rm(workDir, { recursive: true, force: true }).catch(() => {});
  }
  const finished = clock();
  return {
    ...result,
    ok: result.ok ?? false,
    startedAt: started.toISOString(),
    finishedAt: finished.toISOString(),
    durationMs: finished.getTime() - started.getTime(),
  };
}

export function formatLogLine(r: RunResult): string {
  return JSON.stringify({
    time: r.finishedAt,
    event: "backup",
    ok: r.ok,
    key: r.key,
    bytes: r.bytes,
    durationMs: r.durationMs,
    deleted: r.deleted?.length,
    warnings: r.warnings?.length,
    error: r.error,
  });
}

function defaultLog(r: RunResult): void {
  (r.ok ? console.log : console.error)(formatLogLine(r));
}

/** Serialises runs (a scheduled tick during a slow run is skipped) and remembers the last result. */
export class BackupRunner {
  lastRun: RunResult | null = null;
  private current: Promise<RunResult> | null = null;

  constructor(
    private readonly execute: () => Promise<RunResult>,
    private readonly log: (r: RunResult) => void = defaultLog,
  ) {}

  get running(): boolean {
    return this.current !== null;
  }

  /** Returns the run's result, or null if a run was already in progress. */
  async run(): Promise<RunResult | null> {
    if (this.current) {
      console.error(JSON.stringify({ time: new Date().toISOString(), event: "backup-skipped", reason: "previous run still in progress" }));
      return null;
    }
    this.current = this.execute();
    try {
      const result = await this.current;
      this.lastRun = result;
      this.log(result);
      return result;
    } finally {
      this.current = null;
    }
  }

  /** Resolves once any in-progress run has finished. */
  async idle(): Promise<void> {
    await this.current?.catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// Status endpoint
// ---------------------------------------------------------------------------

export function createStatusServer(runner: BackupRunner, nextRun: () => Date | null): http.Server {
  return http.createServer((req, res) => {
    const pathname = (req.url ?? "/").split("?")[0];
    if (pathname !== "/" && pathname !== "/status") {
      res.writeHead(404, { "Content-Type": "application/json" }).end(JSON.stringify({ error: "not found" }));
      return;
    }
    if (req.method !== "GET" && req.method !== "HEAD") {
      res.writeHead(405, { Allow: "GET, HEAD", "Content-Type": "application/json" }).end(JSON.stringify({ error: "method not allowed" }));
      return;
    }
    const body = JSON.stringify({
      running: runner.running,
      nextRun: nextRun()?.toISOString() ?? null,
      lastRun: runner.lastRun,
    });
    res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    res.end(req.method === "HEAD" ? undefined : body);
  });
}

export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
