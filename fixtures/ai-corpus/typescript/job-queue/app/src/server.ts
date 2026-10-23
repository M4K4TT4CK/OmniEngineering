import http from 'node:http';
import { validateJob } from './jobs';
import { JobQueue } from './queue';

const MAX_BODY_BYTES = 1024 * 1024;

function send(res: http.ServerResponse, status: number, body: unknown): void {
  const data = JSON.stringify(body);
  res.writeHead(status, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) });
  res.end(data);
}

function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    req.on('data', (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(Object.assign(new Error('request body too large'), { status: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

export function createServer(queue: JobQueue): http.Server {
  return http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url ?? '/', 'http://localhost');

      if (req.method === 'POST' && url.pathname === '/jobs') {
        let body: any;
        try {
          body = JSON.parse(await readBody(req));
        } catch (err: any) {
          if (err.status) return send(res, err.status, { error: err.message });
          return send(res, 400, { error: 'body must be valid JSON' });
        }
        const problem = validateJob(body?.type, body?.payload);
        if (problem) return send(res, 400, { error: problem });
        const job = queue.submit(body.type, body.payload);
        return send(res, 202, { id: job.id });
      }

      const match = /^\/jobs\/([^/]+)$/.exec(url.pathname);
      if (req.method === 'GET' && match) {
        const job = queue.get(match[1]);
        if (!job) return send(res, 404, { error: 'job not found' });
        const { payload: _payload, ...view } = job;
        return send(res, 200, view);
      }

      send(res, 404, { error: 'not found' });
    } catch {
      send(res, 500, { error: 'internal error' });
    }
  });
}

if (require.main === module) {
  const queue = new JobQueue({ concurrency: Number(process.env.WORKERS ?? 4) });
  const server = createServer(queue);
  const port = Number(process.env.PORT ?? 3000);
  server.listen(port, () => console.log(`job queue listening on :${port}`));
  const shutdown = () => {
    server.close();
    void queue.close().then(() => process.exit(0));
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}
