# GitHub OAuth Login (Flask + SQLite)

A small Flask app that lets users sign in with GitHub using OAuth 2.0.

| Route        | Purpose |
|--------------|---------|
| `/login`     | Redirects to GitHub. Accepts `?next=/some/path` to return there afterwards. |
| `/callback`  | Checks `state`, exchanges the code (with PKCE) for a token, fetches `/user`, creates or updates the SQLite record, starts a session and redirects to `next`. |
| `/profile`   | Shows the logged-in user's details. Requires login. |
| `/logout`    | `POST` only, CSRF-protected. Clears the session. |

## Setup

1. Create a GitHub OAuth App (Settings → Developer settings → OAuth Apps) with
   callback URL `http://localhost:5000/callback`.
2. Install dependencies and set the environment:

   ```sh
   pip install -r requirements.txt
   export GITHUB_CLIENT_ID=...
   export GITHUB_CLIENT_SECRET=...
   export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
   export DATABASE=users.db           # optional, default users.db
   export SESSION_COOKIE_SECURE=1     # default; set 0 only for plain-HTTP non-localhost dev
   python app.py
   ```

3. Open http://localhost:5000 and click "Log in with GitHub".

The app refuses to start if `SECRET_KEY`, `GITHUB_CLIENT_ID` or
`GITHUB_CLIENT_SECRET` are missing.

## Security notes

- `state` is random per login, single-use and compared in constant time.
- PKCE (S256) is used on top of the client secret.
- `next` must be a local path; absolute URLs, `//host` and backslash tricks
  fall back to `/`, so there's no open redirect.
- The session is cleared and rebuilt on login to prevent session fixation.
- Cookies are `HttpOnly`, `SameSite=Lax` and `Secure` by default.
- The GitHub access token is not stored. It's used only once, to fetch the profile.
- Templates are auto-escaped, and profile links/avatars are only shown when
  they use `https://`.

## Tests

```sh
pytest
```

The tests stub out GitHub's HTTP endpoints, so they need no network access
or real credentials.
