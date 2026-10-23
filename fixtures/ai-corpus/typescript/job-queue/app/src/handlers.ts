import path from 'node:path';
import nodemailer, { Transporter } from 'nodemailer';
import sharp from 'sharp';
import type { ResizeImagePayload, SendEmailPayload } from './jobs';

type Handler = (payload: any) => Promise<unknown>;

const DEFAULT_WIDTH = 800;

/**
 * Writes `<name>-resized<ext>` next to the input. If IMAGE_ROOT is set, the
 * input must resolve to a location inside that directory.
 */
async function resizeImage({ path: input, width = DEFAULT_WIDTH }: ResizeImagePayload) {
  const resolved = path.resolve(input);
  const root = process.env.IMAGE_ROOT;
  if (root) {
    const rel = path.relative(path.resolve(root), resolved);
    if (rel.startsWith('..') || path.isAbsolute(rel)) {
      throw new Error('path is outside IMAGE_ROOT');
    }
  }
  const { dir, name, ext } = path.parse(resolved);
  const outputPath = path.join(dir, `${name}-resized${ext}`);
  const info = await sharp(resolved).resize({ width, withoutEnlargement: true }).toFile(outputPath);
  return { outputPath, width: info.width, height: info.height, size: info.size };
}

let transporter: Transporter | undefined;

function getTransporter(): Transporter {
  if (transporter) return transporter;
  const host = process.env.SMTP_HOST;
  if (!host) throw new Error('SMTP_HOST is not configured');
  const port = Number(process.env.SMTP_PORT ?? 587);
  const user = process.env.SMTP_USER;
  transporter = nodemailer.createTransport({
    host,
    port,
    secure: process.env.SMTP_SECURE ? process.env.SMTP_SECURE === 'true' : port === 465,
    auth: user ? { user, pass: process.env.SMTP_PASS } : undefined,
  });
  return transporter;
}

async function sendEmail({ to, subject, body }: SendEmailPayload) {
  const from = process.env.SMTP_FROM ?? process.env.SMTP_USER;
  if (!from) throw new Error('SMTP_FROM is not configured');
  const info = await getTransporter().sendMail({ from, to, subject, text: body });
  return { messageId: info.messageId, accepted: info.accepted };
}

export const handlers: Record<string, Handler> = {
  'resize-image': resizeImage,
  'send-email': sendEmail,
};
