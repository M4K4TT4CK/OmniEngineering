// CWE-434: multer stores whatever is uploaded, under the client's own filename, in a served directory.
import express from "express";
import multer from "multer";
import formidable from "formidable";
import fs from "node:fs";
import path from "node:path";

const storage = multer.diskStorage({
  destination: "public/uploads",
  filename: (_req, file, cb) => cb(null, file.originalname), // expect: arbiter/typescript.upload-client-filename
});
const upload = multer({ storage });

export const router = express.Router();
router.post("/avatar", upload.single("avatar"), (req, res) => {
  res.json({ url: `/uploads/${req.file?.filename}` });
});

// formidable: the parsed file is moved to its client-given name.
export function importDocument(req: express.Request, res: express.Response): void {
  formidable({}).parse(req, (err, _fields, files) => {
    if (err) {
      res.sendStatus(400);
      return;
    }
    const doc = files.doc?.[0];
    if (!doc) {
      res.sendStatus(400);
      return;
    }
    fs.renameSync(doc.filepath, path.join("public/docs", doc.originalFilename ?? "upload")); // expect: arbiter/typescript.upload-client-filename
    res.sendStatus(201);
  });
}
