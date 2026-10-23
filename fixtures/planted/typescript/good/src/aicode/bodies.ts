// Upload and body handling with limits (ARB-083).
import http from "node:http";
import zlib from "node:zlib";
import multer from "multer";

const MAX_BODY = 1024 * 1024;

export const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 5 * 1024 * 1024, files: 1 } });

export const thumbOptions = { limitInputPixels: 40_000_000 };

export function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    req.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_BODY) {
        req.destroy();
        reject(new Error("body too large"));
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
  });
}

export function inflate(req: { body: Buffer }): Buffer {
  return zlib.gunzipSync(req.body, { maxOutputLength: 16 * MAX_BODY });
}
