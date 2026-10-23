import { createApp } from "./app";

const dir = process.env.UPLOAD_DIR ?? "./uploads";
const port = Number(process.env.PORT ?? 3000);
const maxFileSize = process.env.MAX_FILE_SIZE ? Number(process.env.MAX_FILE_SIZE) : undefined;

createApp({ dir, maxFileSize }).listen(port, () => {
  console.log(`File upload service listening on :${port}, storing files in ${dir}`);
});
