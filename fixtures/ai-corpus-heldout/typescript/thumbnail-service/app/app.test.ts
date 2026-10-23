import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import type { AddressInfo } from 'node:net';
import { after, before, test } from 'node:test';
import sharp from 'sharp';
import { createApp } from './app';

interface Thumbnails {
  id: string;
  thumbnails: { width: number; url: string }[];
}

interface Running {
  server: http.Server;
  base: string;
}

let cacheDir: string;
let png: Buffer;
let service: Running;
let images: Running;
let imageHits = 0;

function listen(listener: http.RequestListener): Promise<Running> {
  return new Promise(resolve => {
    const server = http.createServer(listener).listen(0, '127.0.0.1', () => {
      const { port } = server.address() as AddressInfo;
      resolve({ server, base: `http://127.0.0.1:${port}` });
    });
  });
}

function postFile(bytes: Buffer, widths: string): Promise<Response> {
  const form = new FormData();
  form.append('image', new Blob([bytes], { type: 'image/png' }), 'cat.png');
  form.append('widths', widths);
  return fetch(`${service.base}/thumbnails`, { method: 'POST', body: form });
}

function postJson(body: unknown, base = service.base): Promise<Response> {
  return fetch(`${base}/thumbnails`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

before(async () => {
  cacheDir = await fs.mkdtemp(path.join(os.tmpdir(), 'thumbnail-test-'));
  png = await sharp({ create: { width: 800, height: 600, channels: 3, background: '#c33' } }).png().toBuffer();
  images = await listen((req, res) => {
    imageHits += 1;
    if (req.url !== '/cat.png') {
      res.statusCode = 404;
      res.end();
      return;
    }
    res.setHeader('Content-Type', 'image/png');
    res.end(png);
  });
  service = await listen(createApp({ cacheDir, allowPrivateAddresses: true }));
});

after(async () => {
  service.server.close();
  images.server.close();
  await fs.rm(cacheDir, { recursive: true, force: true });
});

test('uploaded file yields thumbnails at each requested width', async () => {
  const res = await postFile(png, '100,320');
  assert.equal(res.status, 201);
  const { thumbnails } = (await res.json()) as Thumbnails;
  assert.deepEqual(thumbnails.map(t => t.width), [100, 320]);

  for (const { width, url } of thumbnails) {
    const img = await fetch(url);
    assert.equal(img.status, 200);
    assert.equal(img.headers.get('content-type'), 'image/webp');
    const meta = await sharp(Buffer.from(await img.arrayBuffer())).metadata();
    assert.equal(meta.width, width);
  }
});

test('thumbnails and original are cached on disk and not regenerated', async () => {
  const first = (await (await postFile(png, '64')).json()) as Thumbnails;
  const thumbPath = path.join(cacheDir, first.id, '64.webp');
  await fs.access(path.join(cacheDir, first.id, 'original'));
  const firstStat = await fs.stat(thumbPath);

  const second = (await (await postFile(png, '64')).json()) as Thumbnails;
  assert.equal(second.id, first.id);
  const secondStat = await fs.stat(thumbPath);
  assert.equal(secondStat.ino, firstStat.ino);
});

test('URL source is fetched once and later requests use the cached original', async () => {
  const hitsBefore = imageHits;
  const first = await postJson({ url: `${images.base}/cat.png`, widths: [200] });
  assert.equal(first.status, 201);
  await first.json();

  const second = await postJson({ url: `${images.base}/cat.png`, widths: [200, 50] });
  assert.equal(second.status, 201);
  assert.equal(imageHits - hitsBefore, 1);
  const body = (await second.json()) as Thumbnails;
  assert.deepEqual(body.thumbnails.map(t => t.width), [200, 50]);
});

test('refuses URLs that resolve to private addresses by default', async () => {
  // Shares the cache with the app above, so a cached copy must not bypass the address check.
  const strict = await listen(createApp({ cacheDir }));
  try {
    const res = await postJson({ url: `${images.base}/cat.png`, widths: [100] }, strict.base);
    assert.equal(res.status, 400);
    await res.json();
  } finally {
    strict.server.close();
  }
});

test('rejects invalid widths', async () => {
  for (const widths of ['', '0', '-5', 'abc', '1.5', '5000', '1,2,3,4,5,6,7,8,9,10,11']) {
    const res = await postFile(png, widths);
    assert.equal(res.status, 400, `widths=${widths}`);
    await res.json();
  }
});

test('rejects input that is not an image', async () => {
  const res = await postFile(Buffer.from('definitely not a png'), '100');
  assert.equal(res.status, 400);
  await res.json();
});

test('requires exactly one of an image file or a url', async () => {
  const none = await postJson({ widths: [100] });
  assert.equal(none.status, 400);
  await none.json();

  const form = new FormData();
  form.append('image', new Blob([png], { type: 'image/png' }), 'cat.png');
  form.append('url', `${images.base}/cat.png`);
  form.append('widths', '100');
  const both = await fetch(`${service.base}/thumbnails`, { method: 'POST', body: form });
  assert.equal(both.status, 400);
  await both.json();
});

test('unknown, malformed, or non-thumbnail paths return 404', async () => {
  const { id } = (await (await postFile(png, '32')).json()) as Thumbnails;
  const paths = [`${'a'.repeat(64)}/32.webp`, `%2e%2e%2fsecret/32.webp`, `${id}/original`, `${id}/33.webp`];
  for (const p of paths) {
    const res = await fetch(`${service.base}/thumbnails/${p}`);
    assert.equal(res.status, 404, p);
    await res.arrayBuffer();
  }
});
