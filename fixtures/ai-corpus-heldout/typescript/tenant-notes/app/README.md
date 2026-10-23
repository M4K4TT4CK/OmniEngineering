# tenant-notes

A multi-tenant notes service in TypeScript on Node.js, persisted in SQLite. No web framework: it uses `node:http` directly.

## Concepts

- **Organisations** are tenants. Every note and user belongs to exactly one organisation, and every query is scoped to the caller's organisation. Other tenants' notes return `404`.
- **Roles**
  - `admin`: everything an editor can do, plus invite users and change roles.
  - `editor`: create, update and delete notes.
  - `viewer`: read notes only.

## Files

- `store.ts`: SQLite schema, password and token handling, role checks, and all domain logic.
- `server.ts`: HTTP routing and JSON handling. Run it directly to start the server.
- `server.test.ts`: automated tests (`node:test`).
- `package.json`, `tsconfig.json`: dependencies and config.

## Setup

Requires Node.js 18.18 or newer.

```sh
npm install
npm test          # run the test suite
npm run typecheck
npm start         # listens on PORT (default 3000), stores data in DB_PATH (default notes.db)
```

## API

Send tokens as `Authorization: Bearer <token>`. Bodies are JSON.

| Method | Path | Role | Description |
| --- | --- | --- | --- |
| POST | `/orgs` | public | Create an organisation and its first admin. Body: `{ organisation, email, password }` |
| POST | `/auth/login` | public | Body: `{ email, password }`. Returns `{ token }` |
| GET | `/notes?q=text` | any | List notes, newest first. `q` optionally filters by title or body text |
| POST | `/notes` | editor, admin | Body: `{ title, body? }` |
| GET | `/notes/:id` | any | Fetch one note |
| PATCH | `/notes/:id` | editor, admin | Update `title` and/or `body` |
| DELETE | `/notes/:id` | editor, admin | Delete a note (`204`) |
| POST | `/users` | admin | Invite a user. Body: `{ email, role }`. Returns the user and a `temporaryPassword` |
| PATCH | `/users/:id` | admin | Change a user's role. Body: `{ role }` |

Roles are `admin`, `editor` and `viewer`. Errors are returned as `{ "error": "..." }` with a matching status code.

## Design notes and limitations

- Passwords are hashed with scrypt. Tokens are random 256-bit values, and only their SHA-256 is stored.
- Tokens do not expire and there is no logout endpoint. Revoking a token means deleting its row in `tokens`.
- Invited users get a generated temporary password, returned once in the invite response. There is no password-change endpoint yet, so users cannot replace it.
- Email addresses are globally unique, so a person can belong to only one organisation.
- Search is a case-insensitive `LIKE` substring match. This is simple and correct, but it does not scale to large datasets. FTS5 would be the next step.
- An organisation must always keep at least one admin.
