import { createApp } from './app';

const port = Number(process.env.PORT ?? 3000);
const { app } = createApp({ dbPath: process.env.DB_PATH ?? 'notes.db' });

app.listen(port, () => console.log(`notes-api listening on :${port}`));
