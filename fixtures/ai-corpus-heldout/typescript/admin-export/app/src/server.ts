import { createApp } from "./app.js";
import { createAdminKey, openDb } from "./db.js";

const db = openDb(process.env.DATABASE_PATH ?? "shop.db");

if (process.argv[2] === "create-admin") {
  const name = process.argv[3];
  if (!name) {
    console.error("Usage: create-admin <name>");
    process.exit(1);
  }
  const key = createAdminKey(db, name);
  console.log(`Created admin '${name}'. API key (store it now, it is not shown again):\n${key}`);
  db.close();
} else {
  const port = Number(process.env.PORT ?? 3000);
  const host = process.env.HOST ?? "127.0.0.1";
  createApp(db).listen(port, host, () => {
    console.log(`Admin reporting service listening on http://${host}:${port}`);
  });
}
