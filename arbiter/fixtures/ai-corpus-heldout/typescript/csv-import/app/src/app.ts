import express, { type ErrorRequestHandler } from "express";
import multer from "multer";
import type { Importer } from "./importer.js";

export interface AppOptions {
  maxFileBytes?: number;
}

export function createApp(importer: Importer, options: AppOptions = {}) {
  const upload = multer({
    storage: multer.memoryStorage(),
    limits: { fileSize: options.maxFileBytes ?? 20 * 1024 * 1024, files: 1 },
  });

  const app = express();

  // Upload a CSV as multipart/form-data in the "file" field.
  app.post("/imports", upload.single("file"), (req, res) => {
    if (!req.file) {
      res.status(400).json({ error: 'expected a CSV file in multipart form field "file"' });
      return;
    }
    if (req.file.size === 0) {
      res.status(400).json({ error: "uploaded file is empty" });
      return;
    }
    const id = importer.start(req.file.buffer, req.file.originalname);
    const statusUrl = `/imports/${id}`;
    res.status(202).location(statusUrl).json({ id, status: "pending", statusUrl });
  });

  // Progress and per-row errors. Errors are paginated with ?errorsLimit=&errorsOffset=.
  app.get("/imports/:id", (req, res) => {
    const limit = intParam(req.query.errorsLimit, 100, 1000);
    const offset = intParam(req.query.errorsOffset, 0, Number.MAX_SAFE_INTEGER);
    if (limit === null || offset === null) {
      res.status(400).json({ error: "errorsLimit must be 0-1000 and errorsOffset a non-negative integer" });
      return;
    }
    const status = importer.getStatus(req.params.id, limit, offset);
    if (!status) {
      res.status(404).json({ error: "import not found" });
      return;
    }
    res.json(status);
  });

  const onError: ErrorRequestHandler = (err, _req, res, _next) => {
    if (err instanceof multer.MulterError) {
      const status = err.code === "LIMIT_FILE_SIZE" ? 413 : 400;
      res.status(status).json({ error: err.message });
      return;
    }
    console.error(err);
    res.status(500).json({ error: "internal server error" });
  };
  app.use(onError);

  return app;
}

function intParam(value: unknown, fallback: number, max: number): number | null {
  if (value === undefined) return fallback;
  if (typeof value !== "string" || !/^\d+$/.test(value)) return null;
  const n = Number(value);
  return n <= max ? n : null;
}
