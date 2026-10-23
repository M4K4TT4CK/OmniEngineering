# Tenant Notes

A small multi-tenant notes API built with FastAPI and SQLite (stdlib `sqlite3`).

## Run

```sh
pip install -r requirements.txt
NOTES_DB=notes.db uvicorn app:create_app --factory
pytest
```

## Model

- **Organisation**: a tenant. Created via `/signup`, and the person who signs up becomes its first admin.
- **User**: belongs to exactly one organisation. Emails are globally unique and case-insensitive.
- **Roles**:
  - `viewer`: can list, fetch and search notes
  - `editor`: everything a viewer can do, plus create, update and delete notes
  - `admin`: everything an editor can do, plus list users, invite users and change roles

Every query is scoped to the caller's organisation. If a note or user belongs to another
organisation, the API returns `404`, the same as for an id that doesn't exist.

## API

All endpoints except `/signup`, `/login` and `/invitations/accept` need an
`Authorization: Bearer <token>` header.

| Method | Path | Role | Notes |
|---|---|---|---|
| POST | `/signup` | — | `{org_name, email, password}` → token |
| POST | `/login` | — | `{email, password}` → token |
| POST | `/logout` | any | revokes the current token |
| GET | `/me` | any | |
| GET | `/users` | admin | users in your org |
| POST | `/invitations` | admin | `{email, role}` → `invite_token` |
| POST | `/invitations/accept` | — | `{token, password}` → creates the user and returns a token |
| PATCH | `/users/{id}/role` | admin | `{role}` |
| GET | `/notes?q=&limit=&offset=` | any | list; `q` does a case-insensitive substring search over title and body |
| GET | `/notes/{id}` | any | |
| POST | `/notes` | editor/admin | `{title, body}` |
| PATCH | `/notes/{id}` | editor/admin | `{title?, body?}` |
| DELETE | `/notes/{id}` | editor/admin | |

## Security notes

- Passwords are hashed with scrypt and a per-user salt. Login takes the same time
  whether or not the email exists.
- Tokens are random 256-bit values. Only their SHA-256 hash is stored. They expire
  after 24 hours and can be revoked with `/logout`.
- Each request reads the user's role from the database, so a role change takes effect
  immediately for tokens that already exist.
- Invitations are single-use and expire after 7 days. The invite token is returned in the
  API response for simplicity; a real deployment would email it instead.
- The last admin of an organisation can't be demoted.
- All SQL is parameterised, and `%` and `_` in search text are matched literally.
- Not included: rate limiting on login, password reset, and removing users.
