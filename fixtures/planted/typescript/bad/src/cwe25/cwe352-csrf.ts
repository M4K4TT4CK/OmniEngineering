// CWE-352: cookie-session Express app whose state-changing routes take no CSRF token.
import express, { type Request, type Response } from "express";
import session from "express-session";

const app = express(); // gap: CWE-352 - nothing in this build reports it (docs/cwe-top25.md)
app.use(express.urlencoded({ extended: false }));
app.use(session({ secret: process.env.SESSION_SECRET ?? "", resave: false, saveUninitialized: false }));

app.post("/account/email", (req: Request, res: Response) => {
  const user = (req.session as { user?: { email: string } }).user;
  if (!user) {
    res.sendStatus(401);
    return;
  }
  user.email = String(req.body.email);
  res.redirect("/account");
});

export default app;
