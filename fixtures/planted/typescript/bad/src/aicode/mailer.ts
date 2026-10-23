// SMTP transport as model-written job handlers build it (ARB-083).
import nodemailer from "nodemailer";

export function makeTransport(host: string, port: number, user: string, pass: string) {
  return nodemailer.createTransport({ // expect: arbiter/typescript.mail-starttls-not-required
    host,
    port,
    secure: port === 465,
    auth: { user, pass },
  });
}
