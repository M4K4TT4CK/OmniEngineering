# Tenant Notes

A small multi-tenant notes API in Ruby (Sinatra + SQLite + bcrypt).

Each organisation has users with one of three roles:

| Role   | Read notes | Create/update/delete notes | Invite users / change roles |
|--------|:----------:|:--------------------------:|:---------------------------:|
| admin  | yes        | yes                        | yes                         |
| editor | yes        | yes                        | no                          |
| viewer | yes        | no                         | no                          |

All data is scoped to the caller's organisation; notes or users belonging to
another organisation behave as if they don't exist (404).

## Files

- `app.rb` – the application (schema, auth, routes)
- `config.ru` – Rack entry point
- `app_test.rb` – Minitest + Rack::Test suite
- `Gemfile`

## Running

```sh
bundle install
DATABASE_PATH=notes.db bundle exec rackup -p 9292   # DATABASE_PATH defaults to ./notes.db
bundle exec ruby app_test.rb                        # tests use an in-memory database
```

## API

Send JSON bodies and authenticate with `Authorization: Bearer <token>`.

| Method & path               | Who         | Body / params                         |
|-----------------------------|-------------|---------------------------------------|
| `POST /signup`              | anyone      | `organization`, `email`, `password` – creates an org and its first admin |
| `POST /login`               | anyone      | `email`, `password` → `token`         |
| `DELETE /session`           | any user    | revokes the current token             |
| `GET /me`                   | any user    |                                       |
| `GET /notes`                | any role    | `q` (text search), `limit` (≤100), `offset` |
| `GET /notes/:id`            | any role    |                                       |
| `POST /notes`               | admin, editor | `title`, `body`                     |
| `PATCH /notes/:id`          | admin, editor | `title` and/or `body`               |
| `DELETE /notes/:id`         | admin, editor |                                     |
| `GET /users`                | admin       |                                       |
| `POST /invitations`         | admin       | `email`, `role` → invitation `token`  |
| `POST /invitations/accept`  | anyone      | `token`, `password` → login `token`   |
| `PATCH /users/:id`          | admin       | `role`                                |

Example:

```sh
curl -s -XPOST localhost:9292/signup -H 'Content-Type: application/json' \
  -d '{"organization":"Acme","email":"me@acme.test","password":"long enough"}'
curl -s localhost:9292/notes?q=milk -H "Authorization: Bearer $TOKEN"
```

## Design notes

- Passwords are hashed with bcrypt (8–72 bytes). Login does a bcrypt
  comparison even for unknown emails to avoid an account-existence timing
  signal.
- Login tokens are random 256-bit values; only their SHA-256 digest is stored.
  They expire after 24 hours. Roles are read from the database on each
  request, so role changes take effect immediately.
- Invitations are single-use, expire after 7 days, and the token is returned to
  the inviting admin to deliver (no email is sent).
- Emails are globally unique, since login is by email alone.
- An organisation can't demote its last admin.
- Search is a case-insensitive substring match on title and body (`LIKE`, with
  `%`/`_` escaped). For large datasets SQLite FTS5 would be a better fit.
- A single SQLite connection is shared and requests are serialised
  (`set :lock, true`), which keeps things simple at the cost of throughput.
- Not included: rate limiting on login, removing users, password reset.
