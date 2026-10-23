#!/usr/bin/env node
import { parseArgs } from "node:util";
import { Cron } from "croner";
import { BackupRunner, createS3Store, createStatusServer, errorMessage, loadConfig, runBackup } from "./core.js";

const USAGE = `Usage: backup-uploader [--config <file>] [--now]

  -c, --config <file>  Path to the JSON config file (default: ./config.json)
      --now            Run one backup immediately and exit (exit code 1 on failure)
  -h, --help           Show this help`;

function log(event: string, fields: Record<string, unknown> = {}): void {
  console.log(JSON.stringify({ time: new Date().toISOString(), event, ...fields }));
}

async function main(): Promise<void> {
  const { values } = parseArgs({
    options: {
      config: { type: "string", short: "c", default: "config.json" },
      now: { type: "boolean", default: false },
      help: { type: "boolean", short: "h", default: false },
    },
  });
  if (values.help) {
    console.log(USAGE);
    return;
  }

  const config = await loadConfig(values.config);
  const store = createS3Store(config.s3);
  const runner = new BackupRunner(() => runBackup(config, store));

  if (values.now) {
    const result = await runner.run();
    store.close();
    process.exitCode = result?.ok ? 0 : 1;
    return;
  }

  const job = new Cron(config.schedule, { timezone: config.timezone, protect: true }, () => runner.run());
  const server = createStatusServer(runner, () => job.nextRun());
  server.on("error", (err) => {
    console.error(JSON.stringify({ time: new Date().toISOString(), event: "status-server-error", error: err.message }));
    process.exit(1);
  });
  server.listen(config.status.port, config.status.host, () => {
    const addr = server.address();
    log("started", {
      schedule: config.schedule,
      timezone: config.timezone,
      nextRun: job.nextRun()?.toISOString() ?? null,
      status: typeof addr === "object" && addr ? `http://${addr.address}:${addr.port}/status` : addr,
    });
  });

  let stopping = false;
  const shutdown = async (signal: string) => {
    if (stopping) return;
    stopping = true;
    log("stopping", { signal, waitingForRun: runner.running });
    job.stop();
    server.close();
    await runner.idle();
    store.close();
    log("stopped");
  };
  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));
}

main().catch((err) => {
  console.error(JSON.stringify({ time: new Date().toISOString(), event: "fatal", error: errorMessage(err) }));
  process.exit(1);
});
