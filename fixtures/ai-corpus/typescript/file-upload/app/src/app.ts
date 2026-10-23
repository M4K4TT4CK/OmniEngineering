import express, { NextFunction, Request, Response } from "express";
import multer from "multer";
import sharp from "sharp";
import fs from "node:fs/promises";
import path from "node:path";

export interface AppOptions {
  /** Directory where uploaded files are stored (created if missing). */
  dir: string;
  /** Maximum upload size in bytes. Defaults to 50 MiB. */
  maxFileSize?: number;
  /** Thumbnail bounding box in pixels. Defaults to 200. */
  thumbnailSize?: number;
}

const THUMB_DIR = ".thumbnails";

/** A name is valid only if it is a plain file name: no separators, no hidden files, no traversal. */
function isValidName(name: string): boolean {
  return (
    name.length > 0 &&
    name.length <= 255 &&
    name === path.basename(name) &&
    !name.includes("\\") &&
    !name.includes("\0") &&
    !name.startsWith(".")
  );
}

class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export function createApp(opts: AppOptions): express.Express {
  const root = path.resolve(opts.dir);
  const thumbRoot = path.join(root, THUMB_DIR);
  const thumbSize = opts.thumbnailSize ?? 200;
  const upload = multer({
    storage: multer.memoryStorage(),
    limits: { fileSize: opts.maxFileSize ?? 50 * 1024 * 1024, files: 1 },
  });

  const app = express();
  const ready = fs.mkdir(thumbRoot, { recursive: true });

  const wrap =
    (fn: (req: Request, res: Response) => Promise<void>) =>
    (req: Request, res: Response, next: NextFunction) =>
      ready.then(() => fn(req, res)).catch(next);

  /** Validates the :name param and returns it; throws 400 if unsafe. */
  const nameOf = (req: Request): string => {
    const name = req.params.name;
    if (!isValidName(name)) throw new HttpError(400, "invalid file name");
    return name;
  };

  /** Stats a stored file; throws 404 if it does not exist or is not a regular file. */
  const statFile = async (name: string) => {
    const st = await fs.stat(path.join(root, name)).catch(() => null);
    if (!st || !st.isFile()) throw new HttpError(404, "file not found");
    return st;
  };

  app.post(
    "/files",
    upload.single("file"),
    wrap(async (req, res) => {
      const file = req.file;
      if (!file) throw new HttpError(400, 'missing multipart field "file"');
      const name = file.originalname;
      if (!isValidName(name)) throw new HttpError(400, "invalid file name");
      try {
        // "wx" fails if the file already exists, so uploads never silently overwrite.
        await fs.writeFile(path.join(root, name), file.buffer, { flag: "wx" });
      } catch (err) {
        if ((err as NodeJS.ErrnoException).code === "EEXIST") {
          throw new HttpError(409, "file already exists");
        }
        throw err;
      }
      res.status(201).json({ name, size: file.size });
    })
  );

  app.get(
    "/files",
    wrap(async (_req, res) => {
      const entries = await fs.readdir(root, { withFileTypes: true });
      const files = await Promise.all(
        entries
          .filter((e) => e.isFile() && !e.name.startsWith("."))
          .map(async (e) => {
            const st = await fs.stat(path.join(root, e.name));
            return { name: e.name, size: st.size, uploadedAt: st.birthtime.getTime() > 0 ? st.birthtime.toISOString() : st.mtime.toISOString() };
          })
      );
      files.sort((a, b) => a.name.localeCompare(b.name));
      res.json(files);
    })
  );

  app.get(
    "/files/:name",
    wrap(async (req, res) => {
      const name = nameOf(req);
      await statFile(name);
      res.download(path.join(root, name), name);
    })
  );

  app.delete(
    "/files/:name",
    wrap(async (req, res) => {
      const name = nameOf(req);
      await statFile(name);
      await fs.unlink(path.join(root, name));
      await fs.rm(path.join(thumbRoot, name + ".png"), { force: true });
      res.status(204).end();
    })
  );

  app.post(
    "/files/:name/thumbnail",
    wrap(async (req, res) => {
      const name = nameOf(req);
      await statFile(name);
      try {
        await sharp(path.join(root, name))
          .rotate()
          .resize(thumbSize, thumbSize, { fit: "inside", withoutEnlargement: true })
          .png()
          .toFile(path.join(thumbRoot, name + ".png"));
      } catch {
        throw new HttpError(415, "file is not a supported image");
      }
      res.status(201).json({ name, thumbnail: `/files/${encodeURIComponent(name)}/thumbnail` });
    })
  );

  app.get(
    "/files/:name/thumbnail",
    wrap(async (req, res) => {
      const name = nameOf(req);
      const thumb = path.join(thumbRoot, name + ".png");
      const st = await fs.stat(thumb).catch(() => null);
      if (!st) throw new HttpError(404, "thumbnail not found; POST to generate it");
      res.type("image/png").sendFile(thumb);
    })
  );

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
    if (err instanceof HttpError) {
      res.status(err.status).json({ error: err.message });
    } else if (err instanceof multer.MulterError) {
      res.status(err.code === "LIMIT_FILE_SIZE" ? 413 : 400).json({ error: err.message });
    } else {
      console.error(err);
      res.status(500).json({ error: "internal server error" });
    }
  });

  return app;
}
