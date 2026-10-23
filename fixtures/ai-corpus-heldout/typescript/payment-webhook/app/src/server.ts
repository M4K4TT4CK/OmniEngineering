import { createApp } from "./app.js";

const webhookSecret = process.env.WEBHOOK_SECRET;
if (!webhookSecret) {
  console.error("WEBHOOK_SECRET must be set");
  process.exit(1);
}

const port = Number(process.env.PORT ?? 3000);
const dbPath = process.env.DATABASE_PATH ?? "payments.db";

const { server } = createApp({ dbPath, webhookSecret });

server.listen(port, () => {
  console.log(`payment-webhook listening on :${port} (db: ${dbPath})`);
});

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
