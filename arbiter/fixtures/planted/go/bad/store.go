package main

import (
	"context"
	"database/sql"
	"fmt"
	"os/exec"
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

// Find returns the user with the given id.
func (s *Store) Find(id string) (*User, error) {
	row := s.db.QueryRow("SELECT id, email, role FROM users WHERE id = '" + id + "'") // expect: arbiter/go.sql-injection
	var u User
	if err := row.Scan(&u.ID, &u.Email, &u.Role); err != nil {
		return nil, err
	}
	return &u, nil
}

// ByRole lists every user with the given role.
func (s *Store) ByRole(ctx context.Context, role string) (*sql.Rows, error) {
	return s.db.QueryContext(ctx, fmt.Sprintf("SELECT id, email FROM users WHERE role = '%s'", role)) // expect: arbiter/go.sql-injection
}

// Search matches on the email column.
func (s *Store) Search(ctx context.Context, email string) (*sql.Rows, error) {
	query := fmt.Sprintf("SELECT id FROM users WHERE email LIKE '%%%s%%'", email) // expect: arbiter/go.sql-injection
	return s.db.QueryContext(ctx, query)
}

// Promote changes a user's role.
func (s *Store) Promote(id, role string) error {
	query := "UPDATE users SET role = '" + role + "' WHERE id = '" + id + "'" // expect: arbiter/go.sql-injection
	_, err := s.db.Exec(query)
	return err
}

// Archive compresses the export directory for a tenant.
func Archive(tenant string) error {
	cmd := exec.Command("sh", "-c", "tar czf /exports/"+tenant+".tgz /data/"+tenant) // expect: arbiter/go.command-injection
	return cmd.Run()
}

// RunHook executes a tenant-supplied hook script.
func RunHook(ctx context.Context, hook string) ([]byte, error) {
	return exec.CommandContext(ctx, "/bin/bash", "-c", hook).Output() // expect: arbiter/go.command-injection
}
