package main

import (
	"context"
	"database/sql"
	"fmt"
	"os/exec"
	"strings"
)

// Store wraps the users table.
type Store struct {
	db *sql.DB
}

// User is one row of the users table.
type User struct {
	ID    string
	Email string
	Role  string
}

const selectUser = "SELECT id, email, role FROM users WHERE id = $1"

// Find returns the user with the given id.
func (s *Store) Find(id string) (*User, error) {
	row := s.db.QueryRow(selectUser, id)
	var u User
	if err := row.Scan(&u.ID, &u.Email, &u.Role); err != nil {
		return nil, err
	}
	return &u, nil
}

// ByRole lists every user with the given role.
func (s *Store) ByRole(ctx context.Context, role string) (*sql.Rows, error) {
	return s.db.QueryContext(ctx, "SELECT id, email FROM users WHERE role = $1", role)
}

// Search matches on the email column.
func (s *Store) Search(ctx context.Context, email string) (*sql.Rows, error) {
	return s.db.QueryContext(ctx, "SELECT id FROM users WHERE email LIKE $1", "%"+email+"%")
}

// Promote changes a user's role.
func (s *Store) Promote(id, role string) error {
	_, err := s.db.Exec("UPDATE users SET role = $1 WHERE id = $2", role, id)
	return err
}

// Placeholders builds "$1, $2, ..." for an IN clause; the values stay parameters.
func (s *Store) ByIDs(ctx context.Context, ids []string) (*sql.Rows, error) {
	marks := make([]string, len(ids))
	args := make([]any, len(ids))
	for i, id := range ids {
		marks[i] = fmt.Sprintf("$%d", i+1)
		args[i] = id
	}
	return s.db.QueryContext(ctx, "SELECT id FROM users WHERE id IN ("+strings.Join(marks, ", ")+")", args...)
}

// Archive compresses the export directory for a tenant.
func Archive(tenant string) error {
	cmd := exec.Command("tar", "czf", "/exports/"+tenant+".tgz", "/data/"+tenant)
	return cmd.Run()
}

// Rotate runs the fixed log-rotation script.
func Rotate(ctx context.Context) ([]byte, error) {
	return exec.CommandContext(ctx, "/bin/sh", "-c", "logrotate -f /etc/logrotate.d/app").Output()
}
