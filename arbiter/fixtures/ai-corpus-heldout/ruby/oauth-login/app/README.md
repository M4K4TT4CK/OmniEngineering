# GitHub OAuth Login (Sinatra + SQLite)

A small Sinatra app that signs users in with GitHub via OAuth 2.0 and stores them in SQLite.

## Routes

| Route                       | Purpose                                                                 |
|-----------------------------|-------------------------------------------------------------------------|
| `GET /`                     | Home page with a sign-in link                                           |
| `GET /login`                | Stores a random `state` and the return path, redirects to GitHub        |
| `GET /auth/github/callback` | Verifies `state`, exchanges the code, upserts the user, starts session  |
| `GET /profile`              | Shows the signed-in user's details (redirects to login if needed)       |
| `POST /logout`              | Ends the session (requires the CSRF token embedded in the profile page) |

The page to return to comes from `/login?return_to=/some/path`, or the same-origin `Referer`.
Only local paths are accepted; anything else falls back to `/` so the app can't be used as an open redirect.

## Setup

1. Create a GitHub OAuth App (GitHub → Settings → Developer settings → OAuth Apps) and set its
   callback URL to `http://localhost:9292/auth/github/callback`.
2. Install gems and configure:

   ```sh
   bundle install
   export GITHUB_CLIENT_ID=...
   export GITHUB_CLIENT_SECRET=...
   export SESSION_SECRET=$(ruby -rsecurerandom -e 'puts SecureRandom.hex(64)')
   ```

3. Run: `bundle exec rackup` and open <http://localhost:9292>.

| Variable               | Default                                       | Notes                                   |
|------------------------|-----------------------------------------------|-----------------------------------------|
| `GITHUB_CLIENT_ID`     | —                                             | Required in production                  |
| `GITHUB_CLIENT_SECRET` | —                                             | Required in production                  |
| `GITHUB_REDIRECT_URI`  | `http://localhost:9292/auth/github/callback`  | Must match the OAuth App's callback URL |
| `SESSION_SECRET`       | random per process (dev/test only)            | ≥ 64 chars; required in production      |
| `DATABASE_PATH`        | `users.db`                                    | SQLite file                             |
| `APP_ENV`              | `development`                                 | `production` enables `Secure` cookies   |

## Tests

```sh
bundle exec ruby app_test.rb
```

The tests use Rack::Test and WebMock (GitHub is never contacted) and an in-memory SQLite database.

## Notes

- The OAuth `state` is single-use and compared in constant time; mismatches are rejected before any call to GitHub.
- The session is cleared and renewed on login and logout. Cookies are `HttpOnly` and `SameSite=Lax`.
- The GitHub access token is used only to fetch the profile and is not stored.
