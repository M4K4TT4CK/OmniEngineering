import { parentPort } from 'node:worker_threads';
import { handlers } from './handlers';

// Worker protocol: receives { id, type, payload, attempt }, replies with
// { id, ok: true, result } or { id, ok: false, error }.
parentPort!.on('message', async ({ id, type, payload }) => {
  try {
    const handler = handlers[type];
    if (!handler) throw new Error(`no handler for job type "${type}"`);
    const result = await handler(payload);
    parentPort!.postMessage({ id, ok: true, result });
  } catch (err) {
    parentPort!.postMessage({ id, ok: false, error: err instanceof Error ? err.message : String(err) });
  }
});
