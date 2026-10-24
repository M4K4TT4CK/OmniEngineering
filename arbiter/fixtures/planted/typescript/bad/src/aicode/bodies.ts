// Upload and body handling as model-written Node services ship it (ARB-083).
import http from "node:http";
import zlib from "node:zlib";
import multer from "multer";

export const upload = multer({ storage: multer.memoryStorage() }); // expect: arbiter/typescript.upload-memory-storage-unbounded

export const thumbOptions = { limitInputPixels: false }; // expect: arbiter/typescript.image-pixel-limit-disabled

export function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (chunk: Buffer) => { data += chunk; }); // expect: arbiter/typescript.request-body-accumulated-unbounded
    req.on("end", () => resolve(data));
  });
}

export function inflate(req: { body: Buffer }): Buffer {
  return zlib.gunzipSync(req.body); // expect: arbiter/typescript.decompress-sync-unbounded
}
