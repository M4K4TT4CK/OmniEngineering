// CWE-352: the session cookie stays on same-site requests.
import express from "express";
import session from "express-session";

export const app = express();
app.use(session({
  secret: process.env.SESSION_SECRET ?? "",
  resave: false,
  saveUninitialized: false,
  cookie: { secure: true, httpOnly: true, sameSite: "lax" },
}));
