// CWE-434: multer stores whatever is uploaded, under the client's own filename, in a served directory.
import express from "express";
import multer from "multer";

const storage = multer.diskStorage({
  destination: "public/uploads",
  filename: (_req, file, cb) => cb(null, file.originalname), // gap: CWE-434 - nothing in this build reports it (docs/cwe-top25.md)
});
const upload = multer({ storage });

export const router = express.Router();
router.post("/avatar", upload.single("avatar"), (req, res) => {
  res.json({ url: `/uploads/${req.file?.filename}` });
});
