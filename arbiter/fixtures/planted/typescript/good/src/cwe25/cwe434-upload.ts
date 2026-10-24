// CWE-434: multer with a type allowlist, a size cap and a server-chosen filename.
import { randomUUID } from "node:crypto";
import express from "express";
import multer from "multer";
import formidable from "formidable";
import fs from "node:fs";
import path from "node:path";

const ALLOWED: Record<string, string> = { "image/png": ".png", "image/jpeg": ".jpg" };
const storage = multer.diskStorage({
  destination: "var/uploads",
  filename: (_req, file, cb) => cb(null, randomUUID() + ALLOWED[file.mimetype]),
});
const upload = multer({
  storage,
  limits: { fileSize: 2 * 1024 * 1024 },
  fileFilter: (_req, file, cb) => cb(null, file.mimetype in ALLOWED),
});

export const router = express.Router();
router.post("/avatar", upload.single("avatar"), (req, res) => {
  res.json({ id: req.file?.filename });
});

// formidable: the type is checked and the stored name is the server's.
export function importDocument(req: express.Request, res: express.Response): void {
  formidable({ maxFileSize: 5 * 1024 * 1024 }).parse(req, (err, _fields, files) => {
    const doc = files.doc?.[0];
    if (err || !doc || doc.mimetype !== "application/pdf") {
      res.sendStatus(400);
      return;
    }
    fs.renameSync(doc.filepath, path.join("var/docs", `${randomUUID()}.pdf`));
    res.sendStatus(201);
  });
}
