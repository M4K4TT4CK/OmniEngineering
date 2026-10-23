import Database from "better-sqlite3";
import { createApp } from "./app.js";

const webhookSecret = process.env.WEBHOOK_SECRET;
const adminToken = process.env.ADMIN_TOKEN;
if (!webhookSecret || !adminToken) {
  console.error("WEBHOOK_SECRET and ADMIN_TOKEN must be set");
  process.exit(1);
}

const db = new Database(process.env.DB_PATH ?? "events.db");
db.pragma("journal_mode = WAL");

const { app, flush } = createApp({ db, webhookSecret, adminToken });
const port = Number(process.env.PORT ?? 3000);
const server = app.listen(port, () => console.log(`listening on :${port}`));

function shutdown(): void {
  server.close(async () => {
    await flush();
    db.close();
    process.exit(0);
  });
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
