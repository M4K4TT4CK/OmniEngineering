// CWE-352: cookie-session Express app with a double-submit CSRF token on every unsafe method.
import express, { type Request, type Response } from "express";
import session from "express-session";
import cookieParser from "cookie-parser";
import { doubleCsrf } from "csrf-csrf";

const app = express();
const { doubleCsrfProtection } = doubleCsrf({
  getSecret: () => process.env.CSRF_SECRET ?? "",
  cookieName: "__Host-csrf",
  cookieOptions: { sameSite: "strict", secure: true },
});
app.use(express.urlencoded({ extended: false }));
app.use(cookieParser());
app.use(session({ secret: process.env.SESSION_SECRET ?? "", resave: false, saveUninitialized: false,
  cookie: { sameSite: "strict", secure: true, httpOnly: true } }));
app.use(doubleCsrfProtection);

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
