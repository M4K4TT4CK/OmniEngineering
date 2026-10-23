# GitHub OAuth login (Go)

A small Go web app where users sign in with GitHub (OAuth 2.0 authorization-code flow with PKCE). Users and sessions are stored in SQLite.

## Routes

| Route                 | What it does |
|-----------------------|--------------|
| `GET /`               | Home page: a login link, or the signed-in user and a logout button |
| `GET /login?next=/x`  | Redirects to GitHub. Uses `next` as the return path, or falls back to a same-origin `Referer`, or `/` |
| `GET /auth/callback`  | Checks `state`, exchanges the code (with the PKCE verifier) for a token, fetches `GET /user`, creates or updates the user, starts a new session, then redirects to the return path |
| `GET /profile`        | Shows the signed-in user's details. If you're not signed in, it redirects to `/login?next=/profile` |
| `POST /logout`        | Deletes the session and clears the cookie |

## Setup

1. Create an OAuth App at <https://github.com/settings/developers> and set its
   **Authorization callback URL** to `http://localhost:8080/auth/callback`
   (or `$BASE_URL/auth/callback`).
2. Fetch dependencies (this also creates `go.sum`) and run the app:

   ```sh
   go mod tidy
   GITHUB_CLIENT_ID=... GITHUB_CLIENT_SECRET=... go run .
   ```

3. Open <http://localhost:8080>.

| Env var                | Default                 | Notes |
|------------------------|-------------------------|-------|
| `GITHUB_CLIENT_ID`     | (required)              | |
| `GITHUB_CLIENT_SECRET` | (required)              | |
| `BASE_URL`             | `http://localhost:8080` | Public URL. If it's `https`, cookies get the `Secure` flag |
| `ADDR`                 | `:8080`                 | Listen address |
| `DATABASE_PATH`        | `app.db`                | SQLite file |

## Tests

```sh
go test ./...
```

The tests use a fake GitHub server (`httptest`) and a temporary SQLite file. They cover the full login flow, PKCE, rejection of a bad or replayed `state`, token-exchange failures, user upsert, session rotation and expiry, logout (POST only, same-origin only), blocking of open redirects in `next`, and HTML escaping.

## Security notes

- The `state` value, the PKCE verifier and the return path go in one short-lived cookie (HttpOnly, SameSite=Lax). The cookie can only be used once.
- Return paths must be local paths. Values like `//host`, `https://…`, backslashes and control characters fall back to `/`.
- Session tokens are 256-bit random values. The database stores only their SHA-256 hash. A new token is issued on every login.
- The app doesn't keep the GitHub access token after it fetches the profile.
- Every response sets a strict CSP, `X-Frame-Options: DENY`, `nosniff` and `Referrer-Policy: same-origin`.
- Email shows only if the user has a public email on GitHub (the scope is `read:user`).

## Files

`main.go` (config and server), `app.go` (handlers, OAuth, SQLite, templates), `app_test.go`, `go.mod`.

Dependencies: `golang.org/x/oauth2` and `modernc.org/sqlite` (pure Go, so no cgo).
