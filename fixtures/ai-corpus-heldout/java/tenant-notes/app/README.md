# Tenant Notes

A multi-tenant notes API in Java 17. It uses the JDK's built-in HTTP server, SQLite via
`sqlite-jdbc`, Jackson for JSON, and JUnit 5 for tests. All sources sit in this directory.

## Build and test

    mvn test

## Run

    mvn compile exec:java -Dexec.mainClass=tenantnotes.Server

Environment variables:

- `PORT` — HTTP port (default `8080`)
- `NOTES_DB_URL` — JDBC URL (default `jdbc:sqlite:notes.db`)

## Roles

| Role   | Read notes | Create/update/delete notes | Invite users, change roles |
|--------|------------|----------------------------|----------------------------|
| viewer | yes        | no                         | no                         |
| editor | yes        | yes                        | no                         |
| admin  | yes        | yes                        | yes                        |

Users belong to exactly one organisation. Notes and users are never visible across organisations;
cross-organisation requests return 404.

## API

Send `Authorization: Bearer <token>` on every route except the ones marked public.

| Method | Path                       | Body                               | Result                     |
|--------|----------------------------|------------------------------------|----------------------------|
| POST   | `/orgs` (public)           | `organisation`, `email`, `password` | 201 creates org and admin  |
| POST   | `/login` (public)          | `email`, `password`                | 200 `{token}`              |
| POST   | `/invitations`             | `email`, `role`                    | 201 `{invitationToken}`    |
| POST   | `/invitations/accept` (public) | `token`, `password`            | 200 `{token}`              |
| PUT    | `/users/{id}/role`         | `role`                             | 204                        |
| GET    | `/notes`                   | —                                  | 200 list                   |
| GET    | `/notes?q=text`            | —                                  | 200 matching notes         |
| POST   | `/notes`                   | `title`, `body`                    | 201 note                   |
| GET    | `/notes/{id}`              | —                                  | 200 note                   |
| PUT    | `/notes/{id}`              | `title` and/or `body`              | 200 note                   |
| DELETE | `/notes/{id}`              | —                                  | 204                        |

Errors are returned as `{"error": "..."}` with 400, 401, 403, 404, 409 or 500.

## Design notes and limitations

- Passwords are hashed with PBKDF2-HMAC-SHA256 (120,000 iterations). Session and invitation
  tokens are random and stored only as SHA-256 digests.
- Session tokens never expire and there is no logout endpoint.
- Invitations are not emailed. The admin receives the invitation token in the API response and
  must pass it on.
- An admin cannot demote the last admin of an organisation.
- Search is a case-insensitive `LIKE` over title and body. SQLite's `lower()` only folds ASCII.
- All data access is serialised by `synchronized` methods on a single connection, which suits
  a single-process deployment.
