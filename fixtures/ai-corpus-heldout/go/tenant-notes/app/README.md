# tenant-notes

A small multi-tenant notes API in Go. Each organisation has users with the role
`admin`, `editor` or `viewer`. Data is stored in SQLite.

- Standard library `net/http` (Go 1.22 method/path routing)
- `modernc.org/sqlite`, a pure-Go SQLite driver, so cgo isn't needed
- `golang.org/x/crypto/bcrypt` for password hashing

## Running

```sh
go mod tidy        # resolves indirect dependencies and writes go.sum
go test ./...
ADDR=:8080 DB_PATH=notes.db go run .
```

## Auth model

- `POST /signup` creates a new organisation, makes the caller its admin, and
  returns a token.
- `POST /login` exchanges an email and password for a token. Emails are
  unique across the whole service, so an email identifies one user in one
  organisation.
- Send the token as `Authorization: Bearer <token>`. Tokens are random,
  last 24 hours, and are stored only as SHA-256 hashes. `POST /logout`
  revokes the current token.
- The user's role is looked up on every request, so a role change applies
  to existing sessions straight away.

## Endpoints

| Method & path            | Minimum role | Body / query                       |
|--------------------------|--------------|------------------------------------|
| `POST /signup`           | none         | `{org_name, email, password}`      |
| `POST /login`            | none         | `{email, password}`                |
| `POST /invites/accept`   | none         | `{token, password}`                |
| `POST /logout`           | viewer       |                                    |
| `GET /me`                | viewer       |                                    |
| `GET /notes`             | viewer       | `?q=text&limit=50&offset=0`        |
| `GET /notes/{id}`        | viewer       |                                    |
| `POST /notes`            | editor       | `{title, body}`                    |
| `PUT /notes/{id}`        | editor       | `{title, body}`                    |
| `DELETE /notes/{id}`     | editor       |                                    |
| `POST /invites`          | admin        | `{email, role}` → `invite_token`   |
| `GET /users`             | admin        |                                    |
| `PATCH /users/{id}`      | admin        | `{role}`                           |

Every query is scoped to the caller's organisation. If a note or user belongs
to another organisation, the API returns `404`, the same as for a missing
record, so it doesn't reveal that the record exists.

`q` matches a case-insensitive substring of the title or body. `%` and `_`
are matched as literal characters. Results are sorted newest first, and
`limit` can be at most 100.

An invite is single-use and expires after 7 days. The API returns the invite
token to the admin, who must pass it to the invitee (there's no email
delivery). The invitee chooses their own password when accepting. An admin
cannot demote the organisation's last remaining admin (`409`).

## Limitations

- Signup is open to anyone, and `409` on a duplicate email reveals that the
  address is registered. Login does not reveal this.
- Login and signup have no rate limiting. Put the service behind a proxy
  that limits requests, or add rate limiting in the app.
- Search uses `LIKE`, which scans every note in the organisation. For large
  data sets, switch to SQLite FTS5.
- Case-insensitive matching only works for ASCII characters.
- There are no endpoints to remove users or change passwords.
