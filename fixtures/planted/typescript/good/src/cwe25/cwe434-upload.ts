// CWE-434: multer with a type allowlist, a size cap and a server-chosen filename.
import { randomUUID } from "node:crypto";
import express from "express";
import multer from "multer";

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
