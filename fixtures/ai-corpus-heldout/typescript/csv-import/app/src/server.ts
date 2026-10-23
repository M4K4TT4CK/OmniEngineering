import { createApp } from "./app.js";
import { openDatabase } from "./db.js";
import { Importer } from "./importer.js";

const db = openDatabase(process.env.DB_PATH ?? "customers.db");
const app = createApp(new Importer(db), {
  maxFileBytes: process.env.MAX_FILE_BYTES ? Number(process.env.MAX_FILE_BYTES) : undefined,
});

const port = Number(process.env.PORT ?? 3000);
const server = app.listen(port, () => console.log(`listening on http://localhost:${port}`));

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    server.close(() => {
      db.close();
      process.exit(0);
    });
  });
}
