# GitHub OAuth login (Java)

A small Javalin web app that signs users in with GitHub (OAuth 2.0 authorization code flow), stores users and sessions in SQLite, and shows a profile page.

## Routes

| Route | Purpose |
| --- | --- |
| `GET /` | Home page with a login link |
| `GET /login?next=/path` | Sets a CSRF `state` cookie and redirects to GitHub |
| `GET /callback` | Verifies `state`, exchanges `code` for a token, fetches `/user`, upserts the local user, starts a session, redirects to `next` |
| `GET /profile` | Shows the logged-in user; redirects to `/login?next=/profile` if not logged in |
| `POST /logout` | Deletes the session and clears the cookie |

## Setup

1. Create a GitHub OAuth App at https://github.com/settings/developers.
   Set the authorization callback URL to `http://localhost:8080/callback`.
2. Set environment variables:

   ```sh
   export GITHUB_CLIENT_ID=your_client_id
   export GITHUB_CLIENT_SECRET=your_client_secret
   # optional
   export BASE_URL=http://localhost:8080   # must match the callback URL; https enables Secure cookies
   export DB_PATH=oauth-login.db
   export PORT=8080
   ```

3. Run:

   ```sh
   mvn compile exec:java
   ```

   Then open http://localhost:8080.

## Tests

```sh
mvn test
```

The tests start the app on an ephemeral port with an in-memory SQLite database, and point it at a fake GitHub server running on `localhost`. No network access or real OAuth app is needed.

## Notes

- Sessions are random 256-bit tokens in an HttpOnly, SameSite=Lax cookie. They expire after 30 days server-side.
- The `next` redirect target is limited to same-site relative paths.
- Logout is a POST so it can't be triggered by a plain link from another site.
- Only the GitHub `read:user` scope is requested. Email is taken from the profile and may be empty if the user keeps it private.
