// SMTP transport that refuses to log in over plaintext (ARB-083).
import nodemailer from "nodemailer";

export function makeTransport(host: string, port: number, user: string, pass: string) {
  return nodemailer.createTransport({
    host,
    port,
    secure: port === 465,
    requireTLS: true,
    auth: { user, pass },
  });
}
