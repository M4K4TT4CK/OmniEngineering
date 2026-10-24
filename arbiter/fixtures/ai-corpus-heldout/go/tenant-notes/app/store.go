package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

var (
	ErrNotFound  = errors.New("not found")
	ErrConflict  = errors.New("conflict")
	ErrLastAdmin = errors.New("organisation must keep at least one admin")
)

const schema = `
CREATE TABLE IF NOT EXISTS orgs (
	id         INTEGER PRIMARY KEY,
	name       TEXT NOT NULL,
	created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
	id            INTEGER PRIMARY KEY,
	org_id        INTEGER NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
	email         TEXT NOT NULL UNIQUE,
	password_hash TEXT NOT NULL,
	role          TEXT NOT NULL CHECK (role IN ('admin', 'editor', 'viewer')),
	created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS users_org ON users(org_id);
CREATE TABLE IF NOT EXISTS sessions (
	token_hash TEXT PRIMARY KEY,
	user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
	expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS invites (
	token_hash TEXT PRIMARY KEY,
	org_id     INTEGER NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
	email      TEXT NOT NULL,
	role       TEXT NOT NULL CHECK (role IN ('admin', 'editor', 'viewer')),
	invited_by INTEGER NOT NULL REFERENCES users(id),
	expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS invites_email ON invites(email);
CREATE TABLE IF NOT EXISTS notes (
	id         INTEGER PRIMARY KEY,
	org_id     INTEGER NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
	title      TEXT NOT NULL,
	body       TEXT NOT NULL,
	created_by INTEGER NOT NULL REFERENCES users(id),
	created_at TEXT NOT NULL,
	updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_org ON notes(org_id, id);
`

type User struct {
	ID        int64  `json:"id"`
	OrgID     int64  `json:"org_id"`
	Email     string `json:"email"`
	Role      string `json:"role"`
	CreatedAt string `json:"created_at"`
}

type Note struct {
	ID        int64  `json:"id"`
	OrgID     int64  `json:"org_id"`
	Title     string `json:"title"`
	Body      string `json:"body"`
	CreatedBy int64  `json:"created_by"`
	CreatedAt string `json:"created_at"`
	UpdatedAt string `json:"updated_at"`
}

type Store struct {
	db *sql.DB
}

func OpenStore(path string) (*Store, error) {
	db, err := sql.Open("sqlite", path+"?_pragma=foreign_keys(1)&_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)")
	if err != nil {
		return nil, err
	}
	// SQLite allows a single writer. One connection serialises transactions
	// (which the last-admin check relies on) and avoids SQLITE_BUSY errors.
	// Consequence: never use s.db while a transaction on it is open.
	db.SetMaxOpenConns(1)
	if _, err := db.Exec(schema); err != nil {
		db.Close()
		return nil, fmt.Errorf("migrate: %w", err)
	}
	return &Store{db: db}, nil
}

func (s *Store) Close() error { return s.db.Close() }

func timestamp(t time.Time) string { return t.UTC().Format(time.RFC3339) }

func isUniqueViolation(err error) bool {
	return err != nil && strings.Contains(err.Error(), "UNIQUE constraint failed")
}

// --- users and organisations ---

func (s *Store) CreateOrgWithAdmin(ctx context.Context, orgName, email, pwHash string, now time.Time) (User, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return User{}, err
	}
	defer tx.Rollback()

	ts := timestamp(now)
	res, err := tx.ExecContext(ctx, `INSERT INTO orgs (name, created_at) VALUES (?, ?)`, orgName, ts)
	if err != nil {
		return User{}, err
	}
	orgID, err := res.LastInsertId()
	if err != nil {
		return User{}, err
	}
	u, err := insertUser(ctx, tx, orgID, email, pwHash, RoleAdmin, ts)
	if err != nil {
		return User{}, err
	}
	return u, tx.Commit()
}

func insertUser(ctx context.Context, tx *sql.Tx, orgID int64, email, pwHash, role, ts string) (User, error) {
	res, err := tx.ExecContext(ctx,
		`INSERT INTO users (org_id, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)`,
		orgID, email, pwHash, role, ts)
	if isUniqueViolation(err) {
		return User{}, ErrConflict
	}
	if err != nil {
		return User{}, err
	}
	id, err := res.LastInsertId()
	if err != nil {
		return User{}, err
	}
	return User{ID: id, OrgID: orgID, Email: email, Role: role, CreatedAt: ts}, nil
}

// UserByEmail returns the user and their password hash.
func (s *Store) UserByEmail(ctx context.Context, email string) (User, string, error) {
	var u User
	var hash string
	err := s.db.QueryRowContext(ctx,
		`SELECT id, org_id, email, role, created_at, password_hash FROM users WHERE email = ?`, email).
		Scan(&u.ID, &u.OrgID, &u.Email, &u.Role, &u.CreatedAt, &hash)
	if errors.Is(err, sql.ErrNoRows) {
		return User{}, "", ErrNotFound
	}
	return u, hash, err
}

func (s *Store) ListUsers(ctx context.Context, orgID int64) ([]User, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT id, org_id, email, role, created_at FROM users WHERE org_id = ? ORDER BY id`, orgID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	users := []User{}
	for rows.Next() {
		var u User
		if err := rows.Scan(&u.ID, &u.OrgID, &u.Email, &u.Role, &u.CreatedAt); err != nil {
			return nil, err
		}
		users = append(users, u)
	}
	return users, rows.Err()
}

// UpdateRole changes the role of a user in orgID. It refuses to demote the
// organisation's last remaining admin.
func (s *Store) UpdateRole(ctx context.Context, orgID, userID int64, role string) (User, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return User{}, err
	}
	defer tx.Rollback()

	var u User
	err = tx.QueryRowContext(ctx,
		`SELECT id, org_id, email, role, created_at FROM users WHERE id = ? AND org_id = ?`, userID, orgID).
		Scan(&u.ID, &u.OrgID, &u.Email, &u.Role, &u.CreatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return User{}, ErrNotFound
	}
	if err != nil {
		return User{}, err
	}

	if u.Role == RoleAdmin && role != RoleAdmin {
		var admins int
		err := tx.QueryRowContext(ctx,
			`SELECT COUNT(*) FROM users WHERE org_id = ? AND role = 'admin'`, orgID).Scan(&admins)
		if err != nil {
			return User{}, err
		}
		if admins <= 1 {
			return User{}, ErrLastAdmin
		}
	}

	if _, err := tx.ExecContext(ctx,
		`UPDATE users SET role = ? WHERE id = ? AND org_id = ?`, role, userID, orgID); err != nil {
		return User{}, err
	}
	u.Role = role
	return u, tx.Commit()
}

// --- sessions ---

func (s *Store) CreateSession(ctx context.Context, tokenHash string, userID int64, expires, now time.Time) error {
	if _, err := s.db.ExecContext(ctx, `DELETE FROM sessions WHERE expires_at <= ?`, now.Unix()); err != nil {
		return err
	}
	_, err := s.db.ExecContext(ctx,
		`INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)`,
		tokenHash, userID, expires.Unix())
	return err
}

// UserBySession resolves a session to its user. The role is read fresh on
// every request, so role changes take effect immediately.
func (s *Store) UserBySession(ctx context.Context, tokenHash string, now time.Time) (User, error) {
	var u User
	err := s.db.QueryRowContext(ctx,
		`SELECT u.id, u.org_id, u.email, u.role, u.created_at
		 FROM sessions s JOIN users u ON u.id = s.user_id
		 WHERE s.token_hash = ? AND s.expires_at > ?`, tokenHash, now.Unix()).
		Scan(&u.ID, &u.OrgID, &u.Email, &u.Role, &u.CreatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return User{}, ErrNotFound
	}
	return u, err
}

func (s *Store) DeleteSession(ctx context.Context, tokenHash string) error {
	_, err := s.db.ExecContext(ctx, `DELETE FROM sessions WHERE token_hash = ?`, tokenHash)
	return err
}

// --- invites ---

func (s *Store) CreateInvite(ctx context.Context, tokenHash string, orgID int64, email, role string, invitedBy int64, expires time.Time) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	var existing int
	if err := tx.QueryRowContext(ctx, `SELECT COUNT(*) FROM users WHERE email = ?`, email).Scan(&existing); err != nil {
		return err
	}
	if existing > 0 {
		return ErrConflict
	}
	// A new invite for the same address in the same org supersedes older ones.
	if _, err := tx.ExecContext(ctx, `DELETE FROM invites WHERE org_id = ? AND email = ?`, orgID, email); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx,
		`INSERT INTO invites (token_hash, org_id, email, role, invited_by, expires_at) VALUES (?, ?, ?, ?, ?, ?)`,
		tokenHash, orgID, email, role, invitedBy, expires.Unix()); err != nil {
		return err
	}
	return tx.Commit()
}

// AcceptInvite consumes an unexpired invite and creates the invited user.
func (s *Store) AcceptInvite(ctx context.Context, tokenHash, pwHash string, now time.Time) (User, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return User{}, err
	}
	defer tx.Rollback()

	var orgID int64
	var email, role string
	err = tx.QueryRowContext(ctx,
		`SELECT org_id, email, role FROM invites WHERE token_hash = ? AND expires_at > ?`, tokenHash, now.Unix()).
		Scan(&orgID, &email, &role)
	if errors.Is(err, sql.ErrNoRows) {
		return User{}, ErrNotFound
	}
	if err != nil {
		return User{}, err
	}

	u, err := insertUser(ctx, tx, orgID, email, pwHash, role, timestamp(now))
	if err != nil {
		return User{}, err
	}
	// The address is now taken, so every outstanding invite for it is dead.
	if _, err := tx.ExecContext(ctx, `DELETE FROM invites WHERE email = ?`, email); err != nil {
		return User{}, err
	}
	return u, tx.Commit()
}

// --- notes ---

const noteCols = `id, org_id, title, body, created_by, created_at, updated_at`

type rowScanner interface {
	Scan(dest ...any) error
}

func scanNote(row rowScanner) (Note, error) {
	var n Note
	err := row.Scan(&n.ID, &n.OrgID, &n.Title, &n.Body, &n.CreatedBy, &n.CreatedAt, &n.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return Note{}, ErrNotFound
	}
	return n, err
}

func (s *Store) CreateNote(ctx context.Context, orgID, userID int64, title, body string, now time.Time) (Note, error) {
	ts := timestamp(now)
	return scanNote(s.db.QueryRowContext(ctx,
		`INSERT INTO notes (org_id, title, body, created_by, created_at, updated_at)
		 VALUES (?, ?, ?, ?, ?, ?) RETURNING `+noteCols,
		orgID, title, body, userID, ts, ts))
}

func (s *Store) GetNote(ctx context.Context, orgID, id int64) (Note, error) {
	return scanNote(s.db.QueryRowContext(ctx,
		`SELECT `+noteCols+` FROM notes WHERE id = ? AND org_id = ?`, id, orgID))
}

func (s *Store) UpdateNote(ctx context.Context, orgID, id int64, title, body string, now time.Time) (Note, error) {
	return scanNote(s.db.QueryRowContext(ctx,
		`UPDATE notes SET title = ?, body = ?, updated_at = ?
		 WHERE id = ? AND org_id = ? RETURNING `+noteCols,
		title, body, timestamp(now), id, orgID))
}

func (s *Store) DeleteNote(ctx context.Context, orgID, id int64) error {
	res, err := s.db.ExecContext(ctx, `DELETE FROM notes WHERE id = ? AND org_id = ?`, id, orgID)
	if err != nil {
		return err
	}
	n, err := res.RowsAffected()
	if err != nil {
		return err
	}
	if n == 0 {
		return ErrNotFound
	}
	return nil
}

var likeEscaper = strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`)

// ListNotes returns notes in orgID, newest first. A non-empty query does a
// case-insensitive (ASCII) substring match on title and body.
func (s *Store) ListNotes(ctx context.Context, orgID int64, query string, limit, offset int) ([]Note, error) {
	q := `SELECT ` + noteCols + ` FROM notes WHERE org_id = ?`
	args := []any{orgID}
	if query != "" {
		pattern := "%" + likeEscaper.Replace(query) + "%"
		q += ` AND (title LIKE ? ESCAPE '\' OR body LIKE ? ESCAPE '\')`
		args = append(args, pattern, pattern)
	}
	q += ` ORDER BY id DESC LIMIT ? OFFSET ?`
	args = append(args, limit, offset)

	rows, err := s.db.QueryContext(ctx, q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	notes := []Note{}
	for rows.Next() {
		n, err := scanNote(rows)
		if err != nil {
			return nil, err
		}
		notes = append(notes, n)
	}
	return notes, rows.Err()
}
