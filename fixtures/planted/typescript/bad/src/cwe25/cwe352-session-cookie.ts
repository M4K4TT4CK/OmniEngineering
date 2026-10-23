// CWE-352: a session cookie the browser sends on cross-site requests, and no CSRF token.
import express from "express";
import session from "express-session";

export const app = express();
app.use(session({
  secret: process.env.SESSION_SECRET ?? "",
  resave: false,
  saveUninitialized: false,
  cookie: { secure: true, httpOnly: true, sameSite: "none" }, // expect: arbiter/typescript.session-cookie-cross-site
}));
