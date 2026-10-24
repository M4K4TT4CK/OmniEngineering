import { createApp, openDb } from "./app";

const port = Number(process.env.PORT ?? 3000);
const db = openDb(process.env.DB_PATH ?? "links.db");

createApp(db).listen(port, () => {
  console.log(`URL shortener listening on port ${port}`);
});
