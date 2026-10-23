// Stand-in for src/worker.ts so tests don't need sharp, SMTP, or a TS build.
// Same message protocol; behaviour is selected by job type.
const { parentPort } = require('node:worker_threads');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

parentPort.on('message', async ({ id, type, payload, attempt }) => {
  try {
    if (type === 'crash') process.exit(1);
    if (type === 'fail') throw new Error('always fails');
    if (type === 'flaky' && attempt < payload.succeedOn) throw new Error(`attempt ${attempt} failed`);
    if (type === 'slow') {
      const start = Date.now();
      await sleep(payload.ms);
      parentPort.postMessage({ id, ok: true, result: { start, end: Date.now() } });
      return;
    }
    parentPort.postMessage({ id, ok: true, result: { type, payload, attempt } });
  } catch (err) {
    parentPort.postMessage({ id, ok: false, error: err.message });
  }
});
