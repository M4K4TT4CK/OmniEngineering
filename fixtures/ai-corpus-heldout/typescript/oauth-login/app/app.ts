import crypto from "node:crypto";
import express from "express";
import type { CookieOptions, Request } from "express";
import {
  createSession,
  deleteSession,
  findSessionUser,
  openDatabase,
  SESSION_TTL_MS,
  upsertUser,
} from "./db";
import type { Db, GitHubProfile, User } from "./db";

export interface Config {
  publicUrl: string;
  githubClientId: string;
  githubClientSecret: string;
  githubWebUrl: string;
  githubApiUrl: string;
  secureCookies: boolean;
}

const SESSION_COOKIE = "sid";
const STATE_COOKIE = "oauth_state";
const NEXT_COOKIE = "oauth_next";
const LOGIN_TTL_MS = 10 * 60 * 1000;
const DEFAULT_PAGE = "/profile";

export function createApp(db: Db, config: Config): express.Express {
  const app = express();
  const baseCookie: CookieOptions = {
    httpOnly: true,
    sameSite: "lax",
    secure: config.secureCookies,
    path: "/",
  };

  app.get("/", (_req, res) => {
    res.type("html").send(
      '<!doctype html><title>OAuth login</title><a href="/login">Log in with GitHub</a>',
    );
  });

  // Starts the flow: remember a random state (CSRF token) and where to return to, then send the user to GitHub.
  app.get("/login", (req, res) => {
    const state = crypto.randomBytes(16).toString("hex");
    res.cookie(STATE_COOKIE, state, { ...baseCookie, maxAge: LOGIN_TTL_MS });
    res.cookie(NEXT_COOKIE, safePath(req.query.next) ?? DEFAULT_PAGE, {
      ...baseCookie,
      maxAge: LOGIN_TTL_MS,
    });

    const authorize = new URL("/login/oauth/authorize", config.githubWebUrl);
    authorize.search = new URLSearchParams({
      client_id: config.githubClientId,
      redirect_uri: new URL("/callback", config.publicUrl).href,
      scope: "read:user",
      state,
    }).toString();
    res.redirect(authorize.href);
  });

  app.get("/callback", async (req, res) => {
    const expectedState = readCookie(req, STATE_COOKIE);
    const next = safePath(readCookie(req, NEXT_COOKIE)) ?? DEFAULT_PAGE;
    res.clearCookie(STATE_COOKIE, baseCookie);
    res.clearCookie(NEXT_COOKIE, baseCookie);

    const { state, code } = req.query;
    if (
      typeof state !== "string" ||
      typeof code !== "string" ||
      !expectedState ||
      !safeEqual(state, expectedState)
    ) {
      res.status(400).send("Login failed: invalid or expired login request. Please try again.");
      return;
    }

    let profile: GitHubProfile;
    try {
      profile = await fetchGithubProfile(config, await exchangeCode(config, code));
    } catch (err) {
      console.error("GitHub login failed:", err);
      res.status(502).send("Login failed: could not complete sign-in with GitHub.");
      return;
    }

    const user = upsertUser(db, profile);
    res.cookie(SESSION_COOKIE, createSession(db, user.id), {
      ...baseCookie,
      maxAge: SESSION_TTL_MS,
    });
    res.redirect(next);
  });

  app.get("/profile", (req, res) => {
    const user = currentUser(req);
    if (!user) {
      res.redirect(`/login?next=${encodeURIComponent("/profile")}`);
      return;
    }
    res.type("html").send(profilePage(user));
  });

  app.post("/logout", (req, res) => {
    const sessionId = readCookie(req, SESSION_COOKIE);
    if (sessionId) deleteSession(db, sessionId);
    res.clearCookie(SESSION_COOKIE, baseCookie);
    res.redirect("/");
  });

  function currentUser(req: Request): User | undefined {
    const sessionId = readCookie(req, SESSION_COOKIE);
    return sessionId ? findSessionUser(db, sessionId) : undefined;
  }

  return app;
}

// Only same-site paths are allowed as redirect targets, so login can't be used as an open redirect.
function safePath(value: unknown): string | undefined {
  if (
    typeof value !== "string" ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.startsWith("/\\") ||
    /[\r\n]/.test(value)
  ) {
    return undefined;
  }
  return value;
}

function readCookie(req: Request, name: string): string | undefined {
  for (const pair of (req.get("cookie") ?? "").split(";")) {
    const index = pair.indexOf("=");
    if (index !== -1 && pair.slice(0, index).trim() === name) {
      return pair.slice(index + 1).trim();
    }
  }
  return undefined;
}

function safeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  return left.length === right.length && crypto.timingSafeEqual(left, right);
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function exchangeCode(config: Config, code: string): Promise<string> {
  const res = await fetch(new URL("/login/oauth/access_token", config.githubWebUrl), {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({
      client_id: config.githubClientId,
      client_secret: config.githubClientSecret,
      code,
      redirect_uri: new URL("/callback", config.publicUrl).href,
    }),
  });
  const body = (await res.json()) as { access_token?: string; error?: string };
  if (!res.ok || !body.access_token) {
    throw new Error(`token exchange failed: ${body.error ?? res.status}`);
  }
  return body.access_token;
}

async function fetchGithubProfile(config: Config, accessToken: string): Promise<GitHubProfile> {
  const res = await fetch(new URL("/user", config.githubApiUrl), {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "oauth-login-example",
    },
  });
  if (!res.ok) {
    throw new Error(`profile fetch failed: ${res.status}`);
  }
  const user = (await res.json()) as GitHubProfile;
  return {
    id: user.id,
    login: user.login,
    name: user.name,
    email: user.email,
    avatar_url: user.avatar_url,
  };
}

function profilePage(user: User): string {
  const avatar = user.avatar_url
    ? `<img src="${escapeHtml(user.avatar_url)}" alt="" width="96">`
    : "";
  return `<!doctype html>
<html>
<head><meta charset="utf-8"><title>Profile</title></head>
<body>
  <h1>${escapeHtml(user.name ?? user.login)}</h1>
  ${avatar}
  <dl>
    <dt>Login</dt><dd>${escapeHtml(user.login)}</dd>
    <dt>GitHub ID</dt><dd>${user.github_id}</dd>
    <dt>Name</dt><dd>${escapeHtml(user.name ?? "not set")}</dd>
    <dt>Email</dt><dd>${escapeHtml(user.email ?? "not public")}</dd>
    <dt>First login</dt><dd>${escapeHtml(user.created_at)}</dd>
  </dl>
  <form method="post" action="/logout"><button type="submit">Log out</button></form>
</body>
</html>`;
}

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`missing required environment variable ${name}`);
  return value;
}

if (require.main === module) {
  const port = Number(process.env.PORT ?? 3000);
  const publicUrl = process.env.PUBLIC_URL ?? `http://localhost:${port}`;
  const config: Config = {
    publicUrl,
    githubClientId: requireEnv("GITHUB_CLIENT_ID"),
    githubClientSecret: requireEnv("GITHUB_CLIENT_SECRET"),
    githubWebUrl: "https://github.com",
    githubApiUrl: "https://api.github.com",
    secureCookies: publicUrl.startsWith("https://"),
  };
  const db = openDatabase(process.env.DATABASE_FILE ?? "app.db");
  createApp(db, config).listen(port, () => {
    console.log(`Listening on ${publicUrl}`);
  });
}
