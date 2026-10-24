import crypto from 'node:crypto';
import dns from 'node:dns/promises';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import express from 'express';
import multer from 'multer';
import sharp from 'sharp';

const DEFAULTS = {
  maxSourceBytes: 20 * 1024 * 1024,
  maxWidths: 10,
  maxWidth: 4096,
  fetchTimeoutMs: 10_000,
  // Off by default so URL sources cannot reach internal hosts (SSRF).
  // Tests turn it on so they can serve images from 127.0.0.1.
  allowPrivateAddresses: false,
};

export type ServiceOptions = typeof DEFAULTS & { cacheDir: string };

class HttpError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

const ID_PATTERN = /^[0-9a-f]{64}$/;
const WIDTH_PATTERN = /^[1-9]\d*$/;

// Cache layout: <cacheDir>/<id>/original and <cacheDir>/<id>/<width>.webp.
// id is the sha256 of the uploaded bytes, or of the URL for URL sources.
export function createApp(input: Partial<ServiceOptions> & Pick<ServiceOptions, 'cacheDir'>): express.Express {
  const o = { ...DEFAULTS, ...input };
  const cacheDir = path.resolve(o.cacheDir);
  const inFlight = new Map<string, Promise<void>>();
  const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: o.maxSourceBytes } }).single('image');

  const app = express();
  app.use(express.json({ limit: '64kb' }));

  app.post('/thumbnails', upload, async (req, res, next) => {
    try {
      const body = (req.body ?? {}) as Record<string, unknown>;
      const widths = parseWidths(body.widths, o.maxWidth, o.maxWidths);
      const url = typeof body.url === 'string' ? body.url : undefined;
      if ((req.file === undefined) === (url === undefined)) {
        throw new HttpError(400, 'send exactly one of an "image" file or a "url"');
      }

      // Check the URL before consulting the cache so a cached copy cannot bypass the address check.
      const target = url === undefined ? undefined : await checkUrl(url, o);
      const id = req.file ? sha256(req.file.buffer) : sha256(`url:${url}`);
      const dir = path.join(cacheDir, id);
      const originalPath = path.join(dir, 'original');

      const cached = await exists(originalPath);
      const source = req.file?.buffer ?? (cached ? await fs.readFile(originalPath) : await fetchSource(target!, o));
      if (!cached) {
        await validateImage(source);
        await writeAtomic(originalPath, source);
      }

      await Promise.all(widths.map(width => ensureThumbnail(path.join(dir, `${width}.webp`), width, source)));

      const base = `${req.protocol}://${req.get('host')}`;
      res.status(201).json({
        id,
        thumbnails: widths.map(width => ({ width, url: `${base}/thumbnails/${id}/${width}.webp` })),
      });
    } catch (err) {
      next(err);
    }
  });

  app.get('/thumbnails/:id/:name', async (req, res, next) => {
    const match = /^([1-9]\d*)\.webp$/.exec(req.params.name);
    if (!ID_PATTERN.test(req.params.id) || !match) {
      res.sendStatus(404);
      return;
    }
    const file = path.join(cacheDir, req.params.id, `${match[1]}.webp`);
    if (!(await exists(file))) {
      res.sendStatus(404);
      return;
    }
    res.sendFile(file, { headers: { 'Cache-Control': 'public, max-age=31536000, immutable' } }, err => {
      if (err) next(err);
    });
  });

  app.use((err: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    const status = statusOf(err);
    if (status === 500) console.error(err);
    res.status(status).json({ error: status === 500 ? 'internal error' : (err as Error).message });
  });

  // Generates one thumbnail, sharing the work between concurrent requests for the same file.
  function ensureThumbnail(file: string, width: number, source: Buffer): Promise<void> {
    const pending = inFlight.get(file);
    if (pending) return pending;
    const job = (async () => {
      if (await exists(file)) return;
      const thumb = await sharp(source)
        .rotate()
        .resize({ width, withoutEnlargement: true })
        .webp({ quality: 80 })
        .toBuffer();
      await writeAtomic(file, thumb);
    })().finally(() => inFlight.delete(file));
    inFlight.set(file, job);
    return job;
  }

  return app;
}

function parseWidths(raw: unknown, maxWidth: number, maxWidths: number): number[] {
  const parts: unknown[] = Array.isArray(raw) ? raw : typeof raw === 'string' ? raw.split(',') : [];
  const widths = new Set<number>();
  for (const part of parts) {
    const text = String(part).trim();
    if (!WIDTH_PATTERN.test(text) || Number(text) > maxWidth) {
      throw new HttpError(400, `invalid width "${text}"; widths must be integers from 1 to ${maxWidth}`);
    }
    widths.add(Number(text));
  }
  if (widths.size === 0 || widths.size > maxWidths) {
    throw new HttpError(400, `send between 1 and ${maxWidths} distinct widths`);
  }
  return [...widths];
}

async function checkUrl(raw: string, o: typeof DEFAULTS): Promise<URL> {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new HttpError(400, 'url is not valid');
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new HttpError(400, 'url must use http or https');
  }
  if (!o.allowPrivateAddresses) await assertPublicHost(url.hostname);
  return url;
}

async function assertPublicHost(hostname: string): Promise<void> {
  const host = hostname.replace(/^\[|\]$/g, '');
  let addresses: string[];
  try {
    addresses = net.isIP(host) ? [host] : (await dns.lookup(host, { all: true })).map(a => a.address);
  } catch {
    throw new HttpError(400, 'url host could not be resolved');
  }
  if (addresses.some(isPrivateAddress)) throw new HttpError(400, 'url must point at a public address');
}

function isPrivateAddress(ip: string): boolean {
  if (net.isIPv4(ip)) {
    const [a, b] = ip.split('.').map(Number);
    return (
      a === 0 ||
      a === 10 ||
      a === 127 ||
      a >= 224 ||
      (a === 100 && b >= 64 && b <= 127) ||
      (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 168)
    );
  }
  const v6 = ip.toLowerCase();
  // IPv4-mapped addresses are treated as private wholesale, which rejects some public hosts.
  return v6 === '::' || v6 === '::1' || v6.startsWith('::ffff:') || /^f[cd]/.test(v6) || /^fe[89ab]/.test(v6);
}

async function fetchSource(url: URL, o: typeof DEFAULTS): Promise<Buffer> {
  let res: Response;
  try {
    // Redirects are refused because a redirect could point at a host that was never checked.
    res = await fetch(url, { redirect: 'error', signal: AbortSignal.timeout(o.fetchTimeoutMs) });
  } catch (err) {
    throw new HttpError(502, `could not fetch url: ${(err as Error).message}`);
  }
  if (!res.ok) throw new HttpError(502, `url responded with status ${res.status}`);
  if (!res.body) throw new HttpError(502, 'url returned an empty body');
  if (Number(res.headers.get('content-length') ?? 0) > o.maxSourceBytes) {
    throw new HttpError(413, 'image is too large');
  }

  const reader = res.body.getReader();
  const chunks: Buffer[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > o.maxSourceBytes) {
        await reader.cancel();
        throw new HttpError(413, 'image is too large');
      }
      chunks.push(Buffer.from(value));
    }
  } catch (err) {
    if (err instanceof HttpError) throw err;
    throw new HttpError(502, 'could not read url body');
  }
  return Buffer.concat(chunks);
}

async function validateImage(source: Buffer): Promise<void> {
  try {
    await sharp(source).metadata();
  } catch {
    throw new HttpError(400, 'input is not a supported image');
  }
}

function statusOf(err: unknown): number {
  if (err instanceof HttpError) return err.status;
  if (err instanceof multer.MulterError) return err.code === 'LIMIT_FILE_SIZE' ? 413 : 400;
  return (err as { status?: number } | null)?.status ?? 500;
}

function sha256(data: Buffer | string): string {
  return crypto.createHash('sha256').update(data).digest('hex');
}

async function exists(file: string): Promise<boolean> {
  try {
    await fs.access(file);
    return true;
  } catch {
    return false;
  }
}

// Write to a temp name and rename, so readers never see a partial file.
async function writeAtomic(file: string, data: Buffer): Promise<void> {
  await fs.mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.${crypto.randomUUID()}.tmp`;
  await fs.writeFile(tmp, data);
  await fs.rename(tmp, file);
}

if (require.main === module) {
  const port = Number(process.env.PORT ?? 3000);
  const cacheDir = process.env.CACHE_DIR ?? 'cache';
  createApp({ cacheDir }).listen(port, () => {
    console.log(`thumbnail service listening on :${port}, cache in ${path.resolve(cacheDir)}`);
  });
}
