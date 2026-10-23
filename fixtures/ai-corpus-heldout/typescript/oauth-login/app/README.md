# GitHub OAuth login

A small Express + SQLite app that signs users in with GitHub (OAuth 2.0 authorization code flow).

## Files

- `app.ts` – routes (`/login`, `/callback`, `/profile`, `/logout`) and the server entry point
- `db.ts` – SQLite schema, user upsert, and session storage
- `app.test.ts` – tests against a fake GitHub server (no network access needed)
- `package.json`, `tsconfig.json` – project setup

## Setup

1. Register an OAuth App at https://github.com/settings/developers.
   Set the authorization callback URL to `http://localhost:3000/callback`.
2. Install dependencies (Node 20+; `better-sqlite3` builds a native module on install):

   ```sh
   npm install
   ```

3. Set environment variables:

   ```sh
   export GITHUB_CLIENT_ID=your_client_id
   export GITHUB_CLIENT_SECRET=your_client_secret
   # optional
   export PORT=3000
   export PUBLIC_URL=http://localhost:3000   # must match the callback URL registered with GitHub
   export DATABASE_FILE=app.db
   ```

4. Run:

   ```sh
   npm run dev      # or: npm run build && npm start
   ```

   Open http://localhost:3000 and click "Log in with GitHub".

## Routes

| Route | Method | Behavior |
| --- | --- | --- |
| `/` | GET | Home page with a login link |
| `/login?next=/some/path` | GET | Sets a state cookie and redirects to GitHub |
| `/callback` | GET | Checks state, exchanges the code for a token, fetches the profile, upserts the user, starts a session, redirects to `next` |
| `/profile` | GET | Shows the logged-in user's details, or redirects to login |
| `/logout` | POST | Deletes the session and clears the cookie |

## Tests

```sh
npm test
```

The tests cover the full login flow, repeat logins updating the same user row, rejection of bad or missing state, failed token exchange, open-redirect protection, logout, expired sessions, and HTML escaping.

## Design notes

- **CSRF:** a random `state` value is stored in a short-lived HttpOnly cookie and must match on callback.
- **Open redirects:** `next` must be a same-site path; anything else falls back to `/profile`.
- **Sessions:** a random 256-bit session ID is stored in SQLite with an expiry (7 days). The cookie is HttpOnly and SameSite=Lax, and is marked Secure when `PUBLIC_URL` is https.
- **GitHub token:** used once to fetch the profile and not stored.
- **Logout** is a POST so it can't be triggered by a cross-site link or image.
- Only the `read:user` scope is requested. `email` is populated only if it is public on GitHub.
